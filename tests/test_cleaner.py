"""Correction du texte dicté : garde-fous sur la réponse du modèle (sans Ollama)."""
import copy

import pytest

from vocalis import cleaner, config
from vocalis.cleaner import Cleaner

S = Cleaner._sanitize
RAW = "bonjour je voudrais un rendez-vous demain matin"


def test_sortie_normale_gardee():
    out = "Bonjour, je voudrais un rendez-vous demain matin."
    assert S(out, RAW) == out


def test_balises_et_reflexion_retirees():
    out = "<think>je réfléchis</think><transcription>Bonjour, je voudrais un " \
          "rendez-vous demain matin.</transcription>"
    assert S(out, RAW) == "Bonjour, je voudrais un rendez-vous demain matin."


def test_contexte_recopie_retire():
    ctx = "Voici le début du message que j'écrivais tout à l'heure."
    out = ctx + " Bonjour, je voudrais un rendez-vous demain matin."
    assert S(out, RAW, ctx) == "Bonjour, je voudrais un rendez-vous demain matin."


def test_guillemets_autour_retires():
    assert S("« Bonjour, je voudrais un rendez-vous demain matin. »", RAW) == \
        "Bonjour, je voudrais un rendez-vous demain matin."
    assert S('"Bonjour, je voudrais un rendez-vous demain matin."', RAW) == \
        "Bonjour, je voudrais un rendez-vous demain matin."


def test_guillemets_gardes_si_le_texte_dicte_en_avait():
    raw = '"citation" dit il hier soir au téléphone'
    out = '"Citation", dit-il hier soir au téléphone"'
    assert S(out, raw).startswith('"')


@pytest.mark.parametrize("out", ["", "Oui.",
                                 "Bien sûr ! Voici une réponse très détaillée à ta demande "
                                 "de rendez-vous : je te propose demain matin à 9 h, ou "
                                 "bien l'après-midi si tu préfères, dis-moi ce qui te va."])
def test_reponse_trop_courte_ou_trop_longue_rejetee(out):
    assert S(out, RAW) == RAW


def test_traduction_limites_plus_larges():
    out = "Meeting tomorrow"          # trop court pour une correction, pas pour une traduction
    assert S(out, RAW) == RAW
    assert S(out, RAW, translating=True) == out


def test_consigne_avec_traduction():
    assert cleaner.system_prompt() == cleaner.SYSTEM_PROMPT
    p = cleaner.system_prompt("pt")
    assert "Ne traduis JAMAIS" not in p
    assert "traduis le texte corrigé en portugais du Portugal" in p


def _cleaner():
    return Cleaner(copy.deepcopy(config.DEFAULTS))


def test_seulement_des_hesitations(monkeypatch):
    c = _cleaner()
    monkeypatch.setattr(cleaner.requests, "post", lambda *a, **k: pytest.fail("appel Ollama"))
    assert c.clean("euh... hum, ben", "fr") == ""


def test_ollama_absent_texte_brut_garde():
    c = _cleaner()
    assert c.clean("  " + RAW + "  ", "fr") == RAW         # réseau coupé : texte brut
    assert c.translated is False


def test_correction_desactivee_en_mode_test_sans_gpu(write_config):
    write_config("[test]\nsans_carte_graphique = true\n")
    c = _cleaner()
    assert c.enabled is False
    assert c.clean(RAW, "fr") == RAW


def test_requete_envoyee_a_ollama(monkeypatch):
    """La requête contient le vocabulaire du dictionnaire et le texte brut."""
    sent = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Bonjour, je voudrais un rendez-vous demain "
                                           "matin."}}

    def post(url, json, timeout):
        sent.update(url=url, body=json)
        return R()

    monkeypatch.setattr(cleaner.requests, "post", post)
    c = _cleaner()
    out = c.clean(RAW, "fr", target="en")
    assert out == "Bonjour, je voudrais un rendez-vous demain matin."
    assert c.translated is True
    assert sent["url"].endswith("/api/chat")
    user = sent["body"]["messages"][1]["content"]
    assert "Vocalis" in user and RAW in user and "français" in user
