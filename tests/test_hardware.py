"""Carte graphique, moteur de dictée et mode test ([test] de config.toml)."""
import copy

from vocalis import config, hardware


def _cfg(moteur):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["dictee"]["moteur"] = moteur
    return cfg


def test_sans_section_test_rien_n_est_simule(isolated):
    assert hardware.test_flags() == {}
    assert not hardware.simulate_no_gpu()
    assert not hardware.offline()
    assert hardware.test_label() == ""
    assert hardware.ollama_options({"temperature": 0}) == {"temperature": 0}


def test_mode_test_lu_dans_config(write_config):
    write_config("[test]\nsans_carte_graphique = true\nhors_ligne = true\n")
    assert hardware.simulate_no_gpu()
    assert hardware.offline()
    assert hardware.test_label() == "Mode test : sans carte graphique, hors ligne"
    assert hardware.ollama_options({"temperature": 0}) == {"temperature": 0, "num_gpu": 0}


def test_mode_test_force_l_absence_de_gpu(write_config, monkeypatch):
    write_config("[test]\nsans_carte_graphique = true\n")
    monkeypatch.setattr(hardware, "_GPU", True)
    assert hardware.has_nvidia_gpu() is False
    assert hardware.resolve_engine(_cfg("auto")) == "parakeet"


def test_seulement_hors_ligne(write_config):
    write_config("[test]\nhors_ligne = true\n")
    assert hardware.offline() and not hardware.simulate_no_gpu()
    assert hardware.test_label() == "Mode test : hors ligne"


def test_config_illisible_pas_de_mode_test(write_config):
    write_config("[test\n")
    assert hardware.test_flags() == {}


def test_moteur_auto_selon_gpu(monkeypatch):
    monkeypatch.setattr(hardware, "_GPU", True)
    assert hardware.resolve_engine(_cfg("auto")) == "whisper"
    monkeypatch.setattr(hardware, "_GPU", False)
    assert hardware.resolve_engine(_cfg("auto")) == "parakeet"


def test_moteur_choisi_garde(monkeypatch):
    monkeypatch.setattr(hardware, "_GPU", False)
    for e in ("whisper", "parakeet", "windows"):
        assert hardware.resolve_engine(_cfg(e)) == e
    assert hardware.resolve_engine(_cfg("inconnu")) == "parakeet"
