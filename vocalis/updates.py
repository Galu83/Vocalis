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
# Dernier état publié : l'API de GitHub donne l'empreinte (sha) du dernier envoi, sans
# le cache de quelques minutes de raw.githubusercontent.com. On lit ensuite
# version.json et l'archive de CET envoi précis : les deux concordent toujours.
HEAD_URL = f"https://api.github.com/repos/{REPO}/commits/main"
VERSION_AT = "https://raw.githubusercontent.com/" + REPO + "/{ref}/version.json"
ZIP_URL = "https://codeload.github.com/" + REPO + "/zip/{ref}"
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


def _get(url: str, timeout: float, accept: str = "") -> bytes:
    headers = {"User-Agent": f"Vocalis/{__version__}"}
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                timeout=timeout) as r:
        return r.read()


def latest(timeout: float = 8) -> dict:
    """version.json du dernier envoi publié (avec son empreinte « ref »)."""
    try:
        sha = _get(HEAD_URL, timeout, "application/vnd.github.sha").decode().strip()
        if len(sha) == 40:
            info = json.loads(_get(VERSION_AT.format(ref=sha), timeout).decode("utf-8"))
            info["ref"] = sha
            return info
    except Exception as exc:
        log.info("API GitHub indisponible (%s) : lecture directe", exc)
    info = json.loads(_get(VERSION_URL + f"?t={int(time.time())}", timeout).decode("utf-8"))
    info["ref"] = "refs/heads/" + info.get("branch", "main")
    return info


def check(timeout: float = 8) -> dict | None:
    """Infos de la nouvelle version ({"version", "notes", "ref"}) ou None."""
    info = latest(timeout)
    if parse(info.get("version", "0")) > parse(__version__):
        return info
    return None


def download(info: dict, progress: Callable[[int], None] = lambda _p: None) -> bytes:
    """Télécharge l'archive de la version (progress : pourcentage de 0 à 100)."""
    url = ZIP_URL.format(ref=info.get("ref") or "refs/heads/" + info.get("branch", "main"))
    req = urllib.request.Request(url, headers={"User-Agent": f"Vocalis/{__version__}"})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 16):
            buf.write(chunk)
            done += len(chunk)
            if total:
                progress(min(100, done * 100 // total))
    progress(100)
    return buf.getvalue()


def install(info: dict, progress: Callable[[int, str], None],
            archive: bytes | None = None) -> None:
    """Télécharge la version (sauf si l'archive est déjà là) et remplace le programme.
    Lève une erreur si besoin (l'ancienne version est alors remise en place)."""
    if archive is None:
        msg = "Téléchargement de la nouvelle version…"
        progress(0, msg)
        archive = download(info, lambda pct: progress(pct * 70 // 100, msg))
    buf = io.BytesIO(archive)
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
