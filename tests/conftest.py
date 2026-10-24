"""Réglages communs des tests de Vocalis.

Chaque test tourne dans un dossier temporaire : config.toml, dictionnaire.json,
actions.json et les voix n'y sont jamais ceux de Flavio. Internet est coupé (une
connexion échoue comme si le PC était hors ligne) et aucune carte graphique n'est
détectée, pour que les résultats soient les mêmes partout.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":            # sans écran, Qt ne trouve pas seul les polices
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class NetworkBlocked(socket.gaierror):
    """Un test a essayé d'aller sur Internet (ou sur Ollama)."""


def _no_network(*args, **kwargs):
    raise NetworkBlocked("réseau coupé pendant les tests")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fichiers de l'utilisateur dans un dossier temporaire, sans réseau ni GPU."""
    from vocalis import actions, config, dictionary, hardware, voices

    # Même la recherche d'adresse (DNS) est coupée : sinon elle peut prendre des secondes.
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    # Les fonctions gardent le chemin d'origine comme valeur par défaut : on le remplace.
    for fn in (config.load_config, config.save_config, config.update_settings):
        monkeypatch.setattr(fn, "__defaults__", (cfg_path,))

    monkeypatch.setattr(dictionary, "PATH", tmp_path / "dictionnaire.json")
    monkeypatch.setattr(dictionary, "_cache", {"mtime": None, "data": None, "compiled": None})
    monkeypatch.setattr(actions, "PATH", tmp_path / "actions.json")
    monkeypatch.setattr(actions, "_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(voices, "VOICES_DIR", tmp_path / "voix")
    monkeypatch.setattr(voices, "windows_voices", lambda lang: [])

    monkeypatch.setattr(hardware, "_TEST", None)
    monkeypatch.setattr(hardware, "_GPU", False)
    return tmp_path


@pytest.fixture
def write_config(isolated):
    """Écrit un config.toml (texte TOML) dans le dossier du test."""
    def write(text: str) -> Path:
        path = isolated / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path
    return write


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
