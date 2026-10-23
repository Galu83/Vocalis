"""Détection de la carte graphique."""
from __future__ import annotations

import os
import shutil
import subprocess

_GPU: bool | None = None


def has_nvidia_gpu() -> bool:
    """Carte NVIDIA avec un pilote assez récent (570+) ?"""
    global _GPU
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
