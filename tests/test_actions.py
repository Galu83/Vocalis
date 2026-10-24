"""Actions de « Demander » et conversation avec l'assistant (sans Ollama)."""
import copy
import json

import pytest

from vocalis import actions as A
from vocalis import assistant, config


# =========================================================================== fichier
def test_actions_par_defaut(isolated):
    acts = A.load()
    assert [a["id"] for a in acts][:4] == ["resumer", "expliquer", "points", "repondre"]
    assert A.get("poli")["type"] == "transformer"
    assert A.get("resumer")["lire"] is True
    assert A.get("inconnue") is None


def test_enregistrer_puis_relire(isolated):
    acts = A.load()
    acts[0]["usages"] = 3
    A.save(acts)
    assert A.load()[0]["usages"] == 3
    assert not (isolated / "actions.tmp").exists()


def test_nettoyage_des_actions(isolated):
    (isolated / "actions.json").write_text(json.dumps({"actions": [
        {"nom": "  Ma action ", "type": "bizarre", "creativite": "beaucoup"},
        {"nom": ""},
        {"nom": "Chaude", "type": "transformer", "creativite": 9},
    ]}), encoding="utf-8")
    acts = A.load()
    assert len(acts) == 2
    assert acts[0]["nom"] == "Ma action" and acts[0]["type"] == "repondre"
    assert acts[0]["creativite"] == 0.3 and acts[0]["id"]
    assert acts[1]["creativite"] == 1.5 and acts[1]["lire"] is False


def test_fichier_illisible_actions_par_defaut(isolated):
    (isolated / "actions.json").write_text("[[", encoding="utf-8")
    assert len(A.load()) == len(A.DEFAULTS)


def test_visible_les_plus_utilisees_d_abord(isolated):
    A.count_use("poli")
    A.count_use("poli")
    A.count_use("corriger")
    shown, more = A.visible(limit=3)
    assert [a["id"] for a in shown] == ["poli", "corriger", "resumer"]
    assert "court" not in [a["id"] for a in shown + more]          # inactive


def test_ajout_depuis_une_question(isolated):
    a = A.add_from_question("Traduire en anglais", "Traduis en anglais", "transformer")
    assert A.get(a["id"])["consigne"] == "Traduis en anglais"
    assert A.get(a["id"])["usages"] == 1


# =========================================================================== choix
@pytest.mark.parametrize("text,kind", [
    ("Rends ce texte plus clair", "transformer"),
    ("Corrige les fautes", "transformer"),
    ("Translate this into English", "transformer"),
    ("Résume ce texte", "repondre"),
    ("Que veut dire ce mot ?", "repondre"),
])
def test_guess_type(text, kind):
    assert A.guess_type(text) == kind


def test_suggest_name():
    assert A.suggest_name("résume ce texte.") == "Résume ce texte"
    assert A.suggest_name("donne moi les trois points les plus importants") == \
        "Donne moi les trois points…"


def test_match_spoken(isolated):
    assert A.match_spoken("Plus poli !")["id"] == "poli"
    assert A.match_spoken("points clé")["id"] == "points"
    assert A.match_spoken("quelle heure est-il") is None
    assert A.match_spoken("plus court") is None                   # action inactive


# =========================================================================== messages
def test_build_messages_repondre():
    msgs, temp, model = A.build_messages(A.get("resumer"), "Le texte.", "français",
                                         system_default="SYS")
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert "<text>\nLe texte.\n</text>" in msgs[1]["content"]
    assert msgs[1]["content"].endswith("Question: Résume ce texte en quelques phrases.")
    assert (temp, model) == (0.3, "")


def test_build_messages_transformer_avec_question():
    msgs, _, _ = A.build_messages(A.get("poli"), "Salut.", "français", "en anglais")
    assert msgs[0]["content"] == A.TRANSFORM_SYSTEM
    assert msgs[1]["content"].endswith("sans changer le sens. en anglais")


def test_build_messages_consigne_longue():
    action = A._clean({"nom": "Rôle", "consigne": "Tu es un juriste.\nRègle 1.\nRègle 2."})
    msgs, _, _ = A.build_messages(action, "Contrat", "français", "Et alors ?")
    assert msgs[0]["content"].startswith("Tu es un juriste.")
    assert msgs[1]["content"] == "<text>\nContrat\n</text>\n\nEt alors ?"


def test_build_messages_mode_avance():
    action = A._clean({"nom": "Expert", "expert": True, "systeme": " Sois bref. ",
                       "prompt": "En {langue} : {question}", "creativite": 0.8,
                       "modele": "qwen3.5:9b"})
    msgs, temp, model = A.build_messages(action, "Texte", "portugais", "pourquoi ?")
    assert msgs == [{"role": "system", "content": "Sois bref."},
                    {"role": "user", "content":
                     "En portugais : pourquoi ?\n\n<text>\nTexte\n</text>"}]
    assert (temp, model) == (0.8, "qwen3.5:9b")
    action["prompt"] = "Corrige {texte}"
    msgs, _, _ = A.build_messages(action, "Texte", "fr")
    assert msgs[-1]["content"] == "Corrige Texte"                 # pas de texte en double


# =========================================================================== assistant
def test_has_cjk():
    assert assistant.has_cjk("Bonjour 你好")
    assert assistant.has_cjk("こんにちは") and assistant.has_cjk("안녕")
    assert not assistant.has_cjk("Olá, ça va ? Grüß dich")
    assert not assistant.has_cjk(None)


def test_clean_for_speech():
    text = "# Titre\n**Gras** et `code`\n- un\n* deux\n• trois"
    assert assistant.clean_for_speech(text) == "Titre\nGras et code\nun\ndeux\ntrois"


def test_text_language():
    fr = "Je pense que nous devons partir tôt demain matin pour éviter les bouchons."
    assert assistant.text_language(fr, "pt") == "fr"
    assert assistant.text_language("Hello there", "fr") == "en"


def test_modele_de_l_assistant(monkeypatch):
    from vocalis import hardware
    cfg = copy.deepcopy(config.DEFAULTS)
    assert assistant.assistant_model(cfg) == assistant.CPU_MODEL
    monkeypatch.setattr(hardware, "_GPU", True)
    assert assistant.assistant_model(cfg) == cfg["nettoyage"]["modele"]
    assert assistant.context_limit(cfg) == assistant.GPU_CONTEXT_CHARS
    cfg["assistant"]["modele"] = "gemma4"
    assert assistant.assistant_model(cfg) == "gemma4"


class FakeOllama:
    """Réponses d'Ollama jouées d'avance (une liste de morceaux par requête)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.bodies = []

    def post(self, url, json=None, stream=False, timeout=None):
        self.bodies.append(json)
        pieces = self.answers.pop(0)
        lines = [_json({"message": {"content": p}}) for p in pieces] + \
            [_json({"done": True})]
        return _Resp(lines)


def _json(obj):
    return json.dumps(obj).encode()


class _Resp:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self.lines)


def _conv(context="Le texte sélectionné.", lang="fr"):
    return assistant.Conversation(copy.deepcopy(config.DEFAULTS), context, lang)


def test_question_et_question_de_suite(monkeypatch):
    fake = FakeOllama(["C'est ", "un texte."], ["Oui."])
    monkeypatch.setattr(assistant.requests, "post", fake.post)
    conv = _conv()
    got = []
    assert assistant.run_question(conv, "De quoi parle-t-il ?", got.append) == \
        "C'est un texte."
    assert got == ["C'est ", "un texte."]
    assert "Answer in French" in fake.bodies[0]["messages"][0]["content"]
    assert assistant.run_question(conv, "Sûr ?", got.append) == "Oui."
    roles = [m["role"] for m in fake.bodies[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_glissement_en_chinois_relance_une_fois(monkeypatch):
    fake = FakeOllama(["Bonjour ", "你好"], ["Bonjour, tout va bien."])
    monkeypatch.setattr(assistant.requests, "post", fake.post)
    got = []
    out = assistant.run_question(_conv(), "Ça va ?", got.append)
    assert out == "Bonjour, tout va bien."
    assert assistant.RESET in got
    assert "Never write Chinese" in fake.bodies[1]["messages"][0]["content"]
    assert fake.bodies[1]["options"]["temperature"] == 0.0


def test_glissement_deux_fois_garde_le_debut(monkeypatch):
    fake = FakeOllama(["Bonjour ", "你好"], ["Salut ", "再见"])
    monkeypatch.setattr(assistant.requests, "post", fake.post)
    assert assistant.run_question(_conv(), "Ça va ?", lambda p: None) == "Salut "


def test_chinois_accepte_si_le_texte_en_contient(monkeypatch):
    fake = FakeOllama(["你好 veut dire bonjour."])
    monkeypatch.setattr(assistant.requests, "post", fake.post)
    out = assistant.run_question(_conv("你好"), "Que veut dire ce mot ?", lambda p: None)
    assert out == "你好 veut dire bonjour."
    assert len(fake.bodies) == 1


def test_action_qui_reecrit_garde_la_langue_du_texte(monkeypatch):
    fake = FakeOllama(["Hello dear friend, I hope you are well."])
    monkeypatch.setattr(assistant.requests, "post", fake.post)
    text = "Hi, I think that we should meet tomorrow because the report is not ready yet."
    conv = _conv(text, lang="fr")
    assistant.run_question(conv, "", lambda p: None, action=A.get("poli"))
    assert "The text is written in English. Write the result in English" in \
        fake.bodies[0]["messages"][-1]["content"]


def test_mode_test_sans_gpu_ollama_sur_le_processeur(write_config, monkeypatch):
    write_config("[test]\nsans_carte_graphique = true\n")
    fake = FakeOllama(["Ok."])
    monkeypatch.setattr(assistant.requests, "post", fake.post)
    assistant.run_question(_conv(), "Test ?", lambda p: None)
    assert fake.bodies[0]["options"]["num_gpu"] == 0


def test_assistant_absent_sans_ollama(monkeypatch):
    monkeypatch.setattr(assistant, "find_ollama", lambda: None)
    with pytest.raises(assistant.NotInstalled):
        _conv().check()
