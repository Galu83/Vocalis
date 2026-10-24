"""config.toml : valeurs par défaut, lecture, écriture, réglages de l'utilisateur."""
import logging

import pytest

from vocalis import config


def test_sans_fichier_valeurs_par_defaut(isolated):
    cfg = config.load_config()
    assert cfg == config.DEFAULTS
    assert cfg is not config.DEFAULTS
    cfg["lecture"]["vitesse"] = 2.0
    assert config.DEFAULTS["lecture"]["vitesse"] == 1.0      # les défauts restent intacts


def test_fichier_partiel_complete_par_les_defauts(write_config):
    write_config('[lecture]\nvoix_fr = "edge:fr-CH-ArianeNeural"\nvitesse = 1.3\n')
    cfg = config.load_config()
    assert cfg["lecture"]["voix_fr"] == "edge:fr-CH-ArianeNeural"
    assert cfg["lecture"]["vitesse"] == 1.3
    assert cfg["lecture"]["voix_pt"] == config.DEFAULTS["lecture"]["voix_pt"]
    assert cfg["raccourcis"] == config.DEFAULTS["raccourcis"]


def test_fichier_avec_bom_accepte(isolated):
    (isolated / "config.toml").write_bytes(
        "\ufeff[lecture]\nvitesse = 1.5\n".encode("utf-8"))
    assert config.load_config()["lecture"]["vitesse"] == 1.5


def test_fichier_mal_forme_valeurs_par_defaut(write_config):
    write_config("[lecture\nvitesse = = 3\n")
    assert config.load_config() == config.DEFAULTS


def test_anciens_reglages_retires(write_config):
    write_config('moteur = "x"\n[raccourcis]\nlecture_zone = "f9"\ndictee_traduite = "f8"\n'
                 '[dictee]\npause_phrase_secondes = 1\ntraduire_vers = "en"\n')
    cfg = config.load_config()
    assert "moteur" not in cfg
    assert "lecture_zone" not in cfg["raccourcis"]
    assert "dictee_traduite" not in cfg["raccourcis"]
    assert "pause_phrase_secondes" not in cfg["dictee"]
    assert "traduire_vers" not in cfg["dictee"]


def test_ecriture_puis_relecture_identique(isolated):
    cfg = config.load_config()
    cfg["lecture"]["voix_pt"] = "edge:pt-PT-RaquelNeural"
    cfg["dictee"]["langues"] = ["fr", "pt"]
    cfg["dictee"]["vocabulaire"] = 'Bechtle, "Vocalis", C:\\chemin'
    cfg["test"] = {"sans_carte_graphique": True, "hors_ligne": False}
    config.save_config(cfg)
    assert config.load_config() == cfg
    text = (isolated / "config.toml").read_text(encoding="utf-8")
    assert "# un appui pour commencer" in text          # commentaires gardés
    assert not (isolated / "config.tmp").exists()


def test_update_settings_ne_touche_pas_aux_autres_reglages(write_config):
    write_config('[lecture]\nvoix_fr = "edge:fr-CH-ArianeNeural"\n'
                 'voix_pt = "edge:pt-PT-RaquelNeural"\n'
                 '[test]\nsans_carte_graphique = true\n'
                 '[perso]\ncle = "valeur"\n')
    config.update_settings("lecture", {"vitesse": 1.4})
    cfg = config.load_config()
    assert cfg["lecture"]["vitesse"] == 1.4
    assert cfg["lecture"]["voix_fr"] == "edge:fr-CH-ArianeNeural"   # narrateur gardé
    assert cfg["lecture"]["voix_pt"] == "edge:pt-PT-RaquelNeural"
    assert cfg["test"] == {"sans_carte_graphique": True}           # mode test gardé
    assert cfg["perso"] == {"cle": "valeur"}


def test_update_settings_nouvelle_section(isolated):
    config.update_settings("interface", {"pastille_x": 12})
    config.update_settings("nouvelle", {"a": 1})
    cfg = config.load_config()
    assert cfg["interface"]["pastille_x"] == 12
    assert cfg["nouvelle"] == {"a": 1}


BROKEN = '[lecture]\nvoix_fr = "edge:fr-CH-ArianeNeural"\nvitesse = = 1.2\n'


def test_update_settings_fichier_illisible_jamais_ecrase(write_config, caplog):
    path = write_config(BROKEN)
    before = path.read_bytes()
    with caplog.at_level(logging.ERROR, logger="vocalis.config"):
        config.update_settings("lecture", {"vitesse": 2.0})
    assert path.read_bytes() == before                 # réglage refusé, fichier intact
    backups = list(path.parent.glob("config.toml.illisible-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert "config.toml illisible" in caplog.text
    config.update_settings("interface", {"pastille_x": 3})
    assert path.read_bytes() == before
    assert len(list(path.parent.glob("config.toml.illisible-*"))) == 1   # une seule copie


def test_save_config_refuse_un_fichier_illisible(write_config):
    path = write_config(BROKEN)
    with pytest.raises(config.ConfigIllisible) as err:
        config.save_config(config.load_config())
    assert path.read_text(encoding="utf-8") == BROKEN
    assert "Vocalis ne l'a pas modifié" in str(err.value)
    assert not (path.parent / "config.tmp").exists()


def test_save_config_fichier_lisible_sans_copie(write_config):
    path = write_config("﻿[lecture]\nvitesse = 1.5\n")
    config.update_settings("lecture", {"vitesse": 1.6})
    assert config.load_config()["lecture"]["vitesse"] == 1.6
    assert not list(path.parent.glob("config.toml.illisible-*"))


def test_fmt():
    assert config._fmt(True) == "true"
    assert config._fmt(0.123456) == "0.123"
    assert config._fmt(3) == "3"
    assert config._fmt(["fr", "pt"]) == '["fr", "pt"]'
    assert config._fmt('a "b"') == '"a \\"b\\""'
    assert config._fmt("é") == '"é"'


def test_key_label():
    assert config.key_label("ctrl+alt+shift+k") == "Ctrl + Alt + Maj + K"
    assert config.key_label("esc") == "Échap"
    assert config.key_label("ctrl+page down") == "Ctrl + Page suiv."
    assert config.key_label("") == "—"


def test_raccourcis_par_defaut():
    r = config.DEFAULTS["raccourcis"]
    assert r["dictee_bascule"] == "ctrl+alt+shift+k"
    assert r["lecture"] == "ctrl+alt+shift+j"
    assert r["demander"] == "ctrl+alt+shift+l"
    assert r["arreter_lecture"] == "esc"
