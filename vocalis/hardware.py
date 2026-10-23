"""Détection de la carte graphique."""
from __future__ import annotations

import os
import shutil
import subprocess

_GPU: bool | None = None
_TEST: dict | None = None


def test_flags() -> dict:
    """Mode test (section [test] de config.toml, absente par défaut) :
    sans_carte_graphique = true -> Vocalis se comporte comme sur un PC sans NVIDIA ;
    hors_ligne = true -> Vocalis n'utilise pas Internet (voix Edge, traduction en
    ligne, dictée Windows, mises à jour)."""
    global _TEST
    if _TEST is None:
        _TEST = {}
        try:
            import tomllib
            from .config import CONFIG_PATH
            with open(CONFIG_PATH, "rb") as f:
                _TEST = dict(tomllib.load(f).get("test", {}) or {})
        except Exception:
            _TEST = {}
    return _TEST


def simulate_no_gpu() -> bool:
    return bool(test_flags().get("sans_carte_graphique", False))


def offline() -> bool:
    return bool(test_flags().get("hors_ligne", False))


def test_label() -> str:
    parts = (["sans carte graphique"] if simulate_no_gpu() else []) + \
        (["hors ligne"] if offline() else [])
    return "Mode test : " + ", ".join(parts) if parts else ""


def ollama_options(options: dict) -> dict:
    """Sans carte graphique (simulée) : Ollama calcule sur le processeur."""
    if simulate_no_gpu():
        options["num_gpu"] = 0
    return options


def has_nvidia_gpu() -> bool:
    """Carte NVIDIA avec un pilote assez récent (570+) ?"""
    global _GPU
    if simulate_no_gpu():
        return False
    if _GPU is not None:
        return _GPU
    _GPU = False
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        exe = shutil.which("nvidia-smi") or os.path.join(
            os.environ.get("WINDIR", r"C:\Windows"), "System32", "nvidia-smi.exe")
        r = subprocess.run([exe, "--query-gpu=driver_version",
                            "--format=csv,noheader"], capture_output=True, text=True,
                           timeout=10, creationflags=flags)
        line = r.stdout.strip().splitlines()[0]
        _GPU = int(line.split(".")[0]) >= 570
    except Exception:
        pass
    return _GPU


ENGINES = {
    "whisper": "Whisper (carte graphique)",
    "parakeet": "Parakeet (processeur, sur ce PC)",
    "windows": "Windows (en ligne)",
}


def resolve_engine(cfg: dict) -> str:
    """Moteur de dictée choisi ; « auto » = Whisper avec une carte NVIDIA, sinon Parakeet."""
    e = cfg["dictee"].get("moteur", "auto")
    if e in ENGINES:
        return e
    return "whisper" if has_nvidia_gpu() else "parakeet"
