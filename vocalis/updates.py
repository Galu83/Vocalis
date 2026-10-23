"""Mises à jour de Vocalis depuis GitHub.

Au lancement (puis une fois par jour), Vocalis lit version.json sur GitHub. Si une
version plus récente existe, une fenêtre propose « Mettre à jour maintenant » ou
« Plus tard ». La mise à jour remplace le programme (dossier vocalis) sans toucher
aux réglages, aux modèles ni aux voix, puis Vocalis redémarre ; au redémarrage, les
nouvelles bibliothèques éventuelles s'installent toutes seules (updater.py).
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import time
import urllib.request
import zipfile
from typing import Callable

from . import __version__
from .config import ROOT

log = logging.getLogger(__name__)

REPO = "Galu83/Vocalis"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/main/version.json"
ZIP_URL = "https://codeload.github.com/" + REPO + "/zip/refs/tags/{tag}"
SETUP_URL = f"https://github.com/{REPO}/raw/main/dist/Vocalis-Setup.exe"
# Ce qui est remplacé lors d'une mise à jour (jamais : réglages, modèles, voix, .venv).
REPLACED = ("vocalis", "requirements.txt", "requirements-gpu.txt", "README.md")


def parse(version: str) -> tuple[int, ...]:
    out = []
    for part in version.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


def installed_copy() -> bool:
    """Vrai pour une installation normale (pas un dossier de développement)."""
    return (ROOT / ".venv").exists() and not (ROOT / ".git").exists()


def check(timeout: float = 8) -> dict | None:
    """Infos de la nouvelle version ({"version", "tag", "notes"}) ou None."""
    req = urllib.request.Request(VERSION_URL + f"?t={int(time.time())}",
                                 headers={"User-Agent": f"Vocalis/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        info = json.loads(r.read().decode("utf-8"))
    if parse(info.get("version", "0")) > parse(__version__):
        info.setdefault("tag", "v" + info["version"])
        return info
    return None


def install(info: dict, progress: Callable[[int, str], None]) -> None:
    """Télécharge la version et remplace le programme. Lève une erreur si besoin
    (l'ancienne version est alors remise en place)."""
    url = ZIP_URL.format(tag=info["tag"])
    progress(0, "Téléchargement de la nouvelle version…")
    req = urllib.request.Request(url, headers={"User-Agent": f"Vocalis/{__version__}"})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 16):
            buf.write(chunk)
            done += len(chunk)
            if total:
                progress(min(70, done * 70 // total), "Téléchargement de la nouvelle version…")
    progress(75, "Préparation…")
    work = ROOT / "_maj"
    shutil.rmtree(work, ignore_errors=True)
    new, old = work / "nouveau", work / "ancien"
    new.mkdir(parents=True)
    old.mkdir(parents=True)
    with zipfile.ZipFile(buf) as z:
        z.extractall(new)
    tops = [p for p in new.iterdir() if p.is_dir()]
    src = tops[0] if len(tops) == 1 and not (new / "vocalis").exists() else new
    if not (src / "vocalis" / "__init__.py").exists():
        raise RuntimeError("Archive de mise à jour invalide")
    progress(85, "Installation…")
    moved: list[str] = []
    try:
        for name in REPLACED:
            if (src / name).exists():
                if (ROOT / name).exists():
                    shutil.move(str(ROOT / name), str(old / name))
                    moved.append(name)
                shutil.move(str(src / name), str(ROOT / name))
    except Exception:
        log.exception("Mise à jour interrompue : retour à la version précédente")
        for name in moved:
            shutil.rmtree(ROOT / name, ignore_errors=True) if (ROOT / name).is_dir() \
                else (ROOT / name).unlink(missing_ok=True)
            shutil.move(str(old / name), str(ROOT / name))
        raise
    shutil.rmtree(work, ignore_errors=True)
    progress(100, "Mise à jour terminée : redémarrage…")
    log.info("Mise à jour installée : %s -> %s", __version__, info.get("version"))
