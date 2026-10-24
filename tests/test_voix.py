"""Lecture à voix haute : repères de mots, pauses, découpage du texte, choix des voix
(sans synthèse réelle ni haut-parleur)."""
import copy
from types import SimpleNamespace as NS

import numpy as np

from vocalis import config, reading, tts, voices
from vocalis.tts import Speech, align_marks, shape_pauses, word_bounds_from_alignments


# =========================================================================== repères de mots
def _al(*pairs):
    return [NS(phoneme=p, num_samples=n) for p, n in pairs]


def test_word_bounds_from_alignments():
    als = _al(("^", 10), ("b", 5), ("ɔ̃", 5), (" ", 4), ("ʒ", 3), ("u", 3), (",", 2),
              ("$", 1), ("_", 6))
    assert word_bounds_from_alignments(als) == [(10, 20), (24, 30)]
    assert word_bounds_from_alignments(als, offset=100) == [(110, 120), (124, 130)]
    assert word_bounds_from_alignments([]) == []


def test_align_marks():
    text = "Bonjour, l'équipe de Bechtle !"
    marks = [("Bonjour", 0.0), ("l'équipe", 0.5), ("de", 1.0), ("Bechtle", 1.25)]
    assert align_marks(text, marks, 1000) == [(0, 0), (500, 500), (1000, 1000),
                                              (1250, 1250), (1250, 1250)]


def test_align_marks_mot_manquant_reprend_le_precedent():
    marks = [("un", 0.0), ("trois", 1.0)]
    assert align_marks("un deux trois", marks, 10) == [(0, 0), (0, 0), (10, 10)]
    assert align_marks("", marks, 10) is None
    assert align_marks("texte", [], 10) is None


# =========================================================================== pauses
RATE = 1000


def _sound(ms):
    return (np.ones(ms, dtype=np.int16) * 3000)


def _silence(ms):
    return np.zeros(ms, dtype=np.int16)


def test_shape_pauses_raccourcit_les_silences():
    audio = np.concatenate([_silence(200), _sound(300), _silence(1000), _sound(300),
                            _silence(500)])
    bounds = [(200, 500), (1500, 1800)]
    sp = shape_pauses(audio, RATE, bounds, gap=0.2)
    # début : 30 ms gardées ; milieu : 200 ms ; fin : 200 ms
    assert len(sp.audio) == 30 + 300 + 200 + 300 + 200
    assert sp.word_bounds == [(30, 330), (530, 830)]
    assert sp.sample_rate == RATE


def test_shape_pauses_courte_pause_gardee():
    audio = np.concatenate([_sound(300), _silence(100), _sound(300)])
    sp = shape_pauses(audio, RATE, [(0, 300), (400, 700)], gap=0.2)
    assert len(sp.audio) == 700
    assert sp.word_bounds == [(0, 300), (400, 700)]


def test_shape_pauses_tout_silencieux():
    sp = shape_pauses(_silence(2000), RATE, [(0, 10)], gap=0.2)
    assert len(sp.audio) == 200 and sp.word_bounds is None


def test_shape_pauses_trop_court():
    sp = shape_pauses(_sound(15), RATE, None, gap=0.2)
    assert len(sp.audio) == 15


# =========================================================================== texte lu
def test_decoupage_en_phrases():
    doc = reading.from_text("Bonjour à tous. Comment allez-vous ? Très bien")
    assert doc.pieces == [(0, 3), (3, 6), (6, 8)]
    assert reading.speak_text(doc, 3, 6) == "Comment allez-vous ?"
    assert doc.piece_of(4) == 1
    assert doc.piece_of(99) == 2


def test_decoupage_phrase_trop_longue():
    doc = reading.from_text(" ".join(["mot"] * 70))
    assert doc.pieces == [(0, 32), (32, 64), (64, 70)]


def test_paragraphes_et_mot_coupe():
    doc = reading.from_paragraphs(["Un exem-", "ple court"], start_para=1, start_offset=4)
    assert doc.text == "Un exem-\n\nple court"
    assert doc.start_word == 3                       # le clic était sur « court »
    assert doc.pieces[0] == (0, 2)                   # nouveau paragraphe = nouveau morceau
    doc = reading.from_text("Un exem-\nple court")
    assert reading.speak_text(doc, 0, 4) == "Un exemple court"


def test_poids_des_mots():
    w = reading.word_weights(reading.from_text("le chat. dort").words)
    assert w[1] > w[2] > w[0]


# =========================================================================== voix choisies
def test_narrateur_choisi_garde():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["lecture"]["voix_fr"] = "edge:fr-CH-ArianeNeural"
    cfg["lecture"]["voix_pt"] = "edge:pt-PT-RaquelNeural"
    t = tts.Tts(cfg)
    assert t.voice_for("fr").name == "Ariane"
    assert t.voice_for("pt").name == "Raquel"
    assert t.voice_for("en").id == config.DEFAULTS["lecture"]["voix_en"]
    assert t.voice_for("multi").name == "Vivienne"
    assert t.pause == 0.2


def test_voix_inconnue_voix_par_defaut():
    assert voices.from_config("edge:n-existe-pas", "fr").id == voices.DEFAULT["fr"]
    assert voices.from_config("", "pt").id == voices.DEFAULT["pt"]
    assert voices.from_config(r"C:\voix\fr_FR-tom-medium.onnx", "fr").id == \
        "piper:fr_FR-tom-medium"


def test_toutes_les_langues_ont_une_voix():
    from vocalis.languages import ALL
    for lang in ALL:
        assert voices.from_config("", lang).lang == lang


def test_voix_piper_installee(isolated):
    v = voices.from_config("", "fr")
    assert v.engine == "piper" and not voices.installed(v)
    (isolated / "voix").mkdir()
    (isolated / "voix" / f"{v.file}.onnx").write_bytes(b"")
    (isolated / "voix" / f"{v.file}.onnx.json").write_bytes(b"")
    assert voices.installed(v)


def test_prononciation_garde_les_reperes_du_texte(isolated, monkeypatch):
    from vocalis import dictionary
    dictionary.save({"prononciation": [{"ecrit": "SQL", "dit": "ess cu elle"}]})
    t = tts.Tts(copy.deepcopy(config.DEFAULTS))
    said = {}

    def raw(text, lang, speed):
        said["text"] = text
        n = len(text.split())
        return Speech(np.zeros(10, dtype=np.int16), RATE, [(i, i + 1) for i in range(n)])

    monkeypatch.setattr(t, "_synth_raw", raw)
    sp = t._synth_pronounced("Le SQL marche", "fr", 1.0)
    assert said["text"] == "Le ess cu elle marche"
    assert sp.word_bounds == [(0, 1), (1, 2), (4, 5)]


def test_hors_ligne_voix_edge_remplacee(write_config, monkeypatch):
    """Mode test hors ligne : une voix Edge passe sur la voix Piper installée."""
    write_config("[test]\nhors_ligne = true\n")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["lecture"]["voix_fr"] = "edge:fr-CH-ArianeNeural"
    t = tts.Tts(cfg)
    monkeypatch.setattr(voices, "installed", lambda v: True)
    used = {}
    monkeypatch.setattr(t, "_synth_edge", lambda *a: used.setdefault("edge", True))
    monkeypatch.setattr(t, "_synth_piper",
                        lambda v, text, speed: used.setdefault("piper", v.name))
    t._synth_raw("Bonjour", "fr", 1.0)
    assert used == {"piper": "Siwis"}
    assert t.voice_for("fr").name == "Ariane"          # le choix n'est pas effacé
