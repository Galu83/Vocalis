"""Dictionnaire : mots de la dictée, apprentissage des corrections, prononciation."""
import json

from vocalis import dictionary as D


def _write(isolated, data):
    (isolated / "dictionnaire.json").write_text(json.dumps(data, ensure_ascii=False),
                                                encoding="utf-8")


# =========================================================================== fichier
def test_sans_fichier_dictionnaire_vide(isolated):
    assert D.load() == D.empty()
    assert D.vocabulary() == ["Vocalis"]            # le nom de l'outil, toujours


def test_ancien_vocabulaire_repris(write_config):
    write_config('[dictee]\nvocabulaire = "Bechtle, Galu , "\n')
    assert [e["mot"] for e in D.load()["dictee"]] == ["Bechtle", "Galu"]


def test_nettoyage_a_la_lecture(isolated):
    _write(isolated, {"dictee": [{"mot": " Bechtle ", "entendu": "vistly, Bechtle, vistly"},
                                 {"mot": ""}],
                      "prononciation": [{"ecrit": "SQL", "dit": ""},
                                        {"ecrit": "Bechtle", "dit": "bèchtle", "langue": None}],
                      "apprendre": False})
    data = D.load()
    assert data["dictee"] == [{"mot": "Bechtle", "entendu": ["vistly"], "appris": False}]
    assert data["prononciation"] == [{"ecrit": "Bechtle", "dit": "bèchtle", "langue": ""}]
    assert data["apprendre"] is False


def test_fichier_illisible(isolated):
    (isolated / "dictionnaire.json").write_text("{pas du json", encoding="utf-8")
    assert D.load() == D.empty()


def test_enregistrer_puis_relire(isolated):
    data = D.empty()
    data["dictee"].append({"mot": "Raquel", "entendu": ["raquelle"]})
    D.save(data)
    assert D.load()["dictee"] == [{"mot": "Raquel", "entendu": ["raquelle"], "appris": False}]
    assert not (isolated / "dictionnaire.tmp").exists()


def test_load_renvoie_une_copie(isolated):
    D.load()["dictee"].append({"mot": "X"})
    assert D.load()["dictee"] == []


# =========================================================================== dictée
def test_remplacement_des_erreurs_connues(isolated):
    _write(isolated, {"dictee": [{"mot": "Bechtle", "entendu": ["vistly", "bec tel"]}]})
    assert D.apply_dictation("je travaille chez vistly depuis longtemps") == \
        "je travaille chez Bechtle depuis longtemps"
    assert D.apply_dictation("chez Bec-Tel.") == "chez Bechtle."
    assert D.apply_dictation("vistlyx reste") == "vistlyx reste"      # mot entier seulement


def test_majuscule_et_nom_de_l_outil(isolated):
    _write(isolated, {"dictee": [{"mot": "iPhone", "entendu": ["aïe faune"]}]})
    assert D.apply_dictation("j'utilise vocalis tous les jours") == \
        "j'utilise Vocalis tous les jours"
    # Un mot en minuscule en début de phrase prend une majuscule.
    _write(isolated, {"dictee": [{"mot": "wifi", "entendu": ["oui fi"]}]})
    D._cache.update(mtime=None, data=None, compiled=None)
    assert D.apply_dictation("Il marche. oui fi coupé") == "Il marche. Wifi coupé"


def test_prompt_words_limite(isolated):
    D.save({"dictee": [{"mot": f"Mot{i:03d}"} for i in range(200)]})
    words = D.prompt_words()
    assert len(words) <= D.MAX_PROMPT_CHARS
    assert words.startswith("Mot000, Mot001")


# =========================================================================== apprentissage
def test_is_learnable():
    assert D.is_learnable("vocal is", "Vocalis")          # mots collés
    assert D.is_learnable("bechtel", "Bechtle")           # nom propre
    assert D.is_learnable("covid", "COVID-19")            # sigle, chiffre
    assert not D.is_learnable("maison", "raison")         # simple reformulation
    assert not D.is_learnable("Bonjour", "bonjour")       # même mot
    assert not D.is_learnable("douze", "12")


def test_is_plausible():
    assert D.is_plausible("Vistly", "Bechtle")
    assert D.is_plausible("maison", "raison")
    assert not D.is_plausible("chat", "ordinateur")


def test_learn(isolated):
    assert D.learn("vocalisse", "Vocalis,")
    assert D.load()["dictee"] == [{"mot": "Vocalis", "entendu": ["vocalisse"], "appris": True}]
    assert not D.learn("vocalisse", "Vocalis")           # déjà connu
    assert D.learn("vocaliz", "Vocalis")
    assert D.load()["dictee"][0]["entendu"] == ["vocalisse", "vocaliz"]
    assert D.apply_dictation("avec vocaliz") == "avec Vocalis"


def test_learn_ne_contredit_pas_un_mot_choisi(isolated):
    D.save({"dictee": [{"mot": "Galu"}]})
    assert not D.learn("galu", "Gallou")


def test_learn_desactive(isolated):
    D.save({"dictee": [], "apprendre": False})
    assert not D.learn("bechtel", "Bechtle")
    assert D.learn("bechtel", "Bechtle", confirmed=True)


def test_find_corrections(isolated):
    before = "Bonjour, je travaille chez bechtel depuis deux ans."
    after = "Bonjour, je travaille chez Bechtle depuis deux ans."
    inserted = "je travaille chez bechtel depuis deux ans."
    assert D.find_corrections(before, after, inserted) == [("bechtel", "Bechtle", True)]


def test_find_corrections_hors_du_texte_dicte_ignore(isolated):
    before = "Bonjour bechtel. je travaille ici"
    after = "Bonjour Bechtle. je travaille ici"
    assert D.find_corrections(before, after, "je travaille ici") == []
    assert D.find_corrections(before, before, "je travaille ici") == []


def test_find_corrections_a_proposer(isolated):
    before = "on se voit à la maison demain"
    after = "on se voit à la raison demain"
    assert D.find_corrections(before, after, before) == [("maison", "raison", False)]


def test_inserted_present():
    assert D.inserted_present("un deux trois quatre", "un deux cinq six")
    assert not D.inserted_present("un deux", "trois quatre cinq")
    assert not D.inserted_present("un", "")


# =========================================================================== prononciation
def test_pronounce_sans_regle(isolated):
    assert D.pronounce("Bonjour à tous", "fr") == ("Bonjour à tous", None)


def test_pronounce_mapping_des_mots(isolated):
    D.save({"prononciation": [{"ecrit": "SQL", "dit": "ess cu elle"},
                              {"ecrit": "Bechtle", "dit": "bèchtle", "langue": "fr"}]})
    spoken, mapping = D.pronounce("Le SQL, chez Bechtle.", "fr")
    assert spoken == "Le ess cu elle, chez bèchtle."
    # Mot d'origine -> mot prononcé : Le=0, SQL=1 (« ess »), chez=4, Bechtle=5.
    assert mapping == [0, 1, 4, 5]


def test_pronounce_sigle_respecte_la_casse(isolated):
    D.save({"prononciation": [{"ecrit": "US", "dit": "u esse"}]})
    assert D.pronounce("Les US et nous", "fr")[0] == "Les u esse et nous"
    assert D.pronounce("Venez chez us", "fr") == ("Venez chez us", None)


def test_pronounce_langue(isolated):
    D.save({"prononciation": [{"ecrit": "Bechtle", "dit": "bèchtle", "langue": "fr"}]})
    assert D.pronounce("Bechtle", "pt") == ("Bechtle", None)


def test_pronounce_plusieurs_mots(isolated):
    D.save({"prononciation": [{"ecrit": "Smart App Control", "dit": "contrôle"}]})
    spoken, mapping = D.pronounce("Le Smart App Control actif", "fr")
    assert spoken == "Le contrôle actif"
    assert mapping == [0, 1, 1, 1, 2]
