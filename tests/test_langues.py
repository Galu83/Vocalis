"""Détection de langue, catalogue des langues, « même texte » de la traduction."""
import pytest

from vocalis import langdetect, languages
from vocalis.translator import Translator, _same

FR = "Je pense que nous devons partir tôt demain matin pour éviter les bouchons sur la route."
PT = "Eu acho que não podemos sair amanhã porque a estação está fechada e não há comboios."
EN = "I think that we should leave early tomorrow because the traffic is really bad there."
DE = "Ich glaube, dass wir morgen sehr früh fahren müssen, weil der Verkehr schlimm ist."


@pytest.mark.parametrize("text,lang", [(FR, "fr"), (PT, "pt"), (EN, "en"), (DE, "de")])
def test_detect_quatre_langues(text, lang):
    assert langdetect.detect(text, "fr", ("fr", "pt", "en", "de")) == lang


@pytest.mark.parametrize("text,lang", [(FR, "fr"), (PT, "pt"), (EN, "en"), (DE, "de")])
def test_detect_any(text, lang):
    assert langdetect.detect_any(text) == lang


def test_detect_sans_indice_langue_par_defaut():
    assert langdetect.detect("12345 67890", "pt", ("fr", "pt")) == "pt"
    assert langdetect.detect("", "fr") == "fr"


def test_detect_langue_inconnue_ignoree():
    assert langdetect.detect(FR, "en", ("xx",)) == "en"


def test_detect_any_texte_sans_mots_connus():
    text = " ".join(["zyx", "qwv", "plm", "krt"] * 4)
    assert langdetect.detect_any(text) == "??"
    assert langdetect.is_foreign(text)


def test_detect_any_langue_d_ecoute_preferee_si_egalite():
    # Peu d'indices : la langue d'écoute est gardée.
    text = "Bechtle Vocalis Windows Ollama Parakeet Whisper Qwen Edge Piper Kokoro Chatterbox et"
    assert langdetect.detect_any(text, prefer="pt") == "pt"
    assert langdetect.detect_any(text) == "fr"


def test_is_foreign():
    es = ("Creo que mañana tenemos que salir muy temprano porque la ciudad está llena de "
          "coches y no hay nada que hacer.")
    assert langdetect.detect_any(es) == "es"
    assert langdetect.is_foreign(es)
    assert not langdetect.is_foreign(FR)


def test_catalogue_des_langues():
    assert languages.ALL[:4] == ("fr", "pt", "en", "de")
    assert len(languages.ALL) == 8
    assert languages.name("pt") == "Portugais"
    assert languages.name("xx") == "xx"
    assert languages.lower_name("de") == "allemand"
    for lang in languages.LANGS.values():
        assert lang.windows_speech and lang.edge_translate


def test_langues_actives():
    assert languages.active({"dictee": {"langues": ["de", "fr", "xx"]}}) == ("fr", "de")
    assert languages.active({"dictee": {"langues": []}}) == ("fr",)
    assert languages.active({}) == ("fr",)


def test_same_texte_identique_a_la_casse_pres():
    assert _same("Bonjour à tous", "bonjour  à tous ")
    assert _same("Bonjour à tous.", "Bonjour à tous")
    assert not _same("Hello everyone", "Bonjour à tous")


def test_is_language():
    assert Translator._is_language("Court texte", "de")          # trop court pour juger
    assert Translator._is_language(FR, "fr")
    assert not Translator._is_language(FR, "en")


def test_traduction_hors_ligne_garde_le_texte_vide(isolated):
    import copy
    from vocalis import config
    t = Translator(copy.deepcopy(config.DEFAULTS))
    assert t.translate("   ", "en") == ""
    assert t.translate("Bonjour", "xx") == "Bonjour"          # langue inconnue


def test_traduction_echoue_sans_reseau(isolated):
    """Sans Ollama ni Internet, translate() lève une erreur : c'est à la lecture de
    la rattraper (passage lu tel quel)."""
    import copy
    from vocalis import config
    t = Translator(copy.deepcopy(config.DEFAULTS))
    with pytest.raises(Exception):
        t.translate("Bonjour à tous", "en")
