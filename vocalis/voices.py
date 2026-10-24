"""Catalogue des voix de lecture : voix Piper (téléchargées à la demande) et voix
installées dans Windows. Toutes fonctionnent hors ligne."""
from __future__ import annotations

import logging
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ROOT

log = logging.getLogger(__name__)

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES_DIR = ROOT / "voix"


@dataclass(frozen=True)
class Voice:
    id: str                    # "piper:<fichier>[#locuteur]" ou "windows:<identifiant>"
    lang: str                  # "fr" ou "pt"
    name: str
    gender: str                # "femme" / "homme"
    accent: str                # "France", "Portugal", "Brésil"…
    engine: str                # "piper" ou "windows"
    key: str = ""              # nom Piper officiel (ex. pt_PT-tugão-medium)
    file: str = ""             # nom de fichier local, sans accent
    speaker: int | None = None
    size_mb: int = 0

    @property
    def label(self) -> str:
        base = f"{self.name} — {self.gender} · {self.accent}"
        if self.engine == "windows":
            return base + " · voix Windows"
        if self.engine == "edge":
            return base + " · en ligne (Microsoft Edge)"
        return base if installed(self) else f"{base} · à télécharger ({self.size_mb} Mo)"


PIPER: list[Voice] = [
    Voice("piper:fr_FR-siwis-medium", "fr", "Siwis", "femme", "France", "piper",
          "fr_FR-siwis-medium", "fr_FR-siwis-medium", None, 63),
    Voice("piper:fr_FR-upmc-medium#0", "fr", "Jessica", "femme", "France", "piper",
          "fr_FR-upmc-medium", "fr_FR-upmc-medium", 0, 74),
    Voice("piper:fr_FR-upmc-medium#1", "fr", "Pierre", "homme", "France", "piper",
          "fr_FR-upmc-medium", "fr_FR-upmc-medium", 1, 74),
    Voice("piper:fr_FR-tom-medium", "fr", "Tom", "homme", "France", "piper",
          "fr_FR-tom-medium", "fr_FR-tom-medium", None, 63),
    Voice("piper:fr_FR-gilles-low", "fr", "Gilles", "homme", "France", "piper",
          "fr_FR-gilles-low", "fr_FR-gilles-low", None, 28),
    Voice("piper:de_DE-thorsten-medium", "de", "Thorsten", "homme", "Allemagne", "piper",
          "de_DE-thorsten-medium", "de_DE-thorsten-medium", None, 63),
    Voice("piper:de_DE-kerstin-low", "de", "Kerstin", "femme", "Allemagne", "piper",
          "de_DE-kerstin-low", "de_DE-kerstin-low", None, 63),
    Voice("piper:pt_PT-tugao-medium", "pt", "Tugão", "homme", "Portugal", "piper",
          "pt_PT-tugão-medium", "pt_PT-tugao-medium", None, 63),
    Voice("piper:pt_BR-faber-medium", "pt", "Faber", "homme", "Brésil", "piper",
          "pt_BR-faber-medium", "pt_BR-faber-medium", None, 63),
    Voice("piper:pt_BR-cadu-medium", "pt", "Cadu", "homme", "Brésil", "piper",
          "pt_BR-cadu-medium", "pt_BR-cadu-medium", None, 63),
    Voice("piper:pt_BR-jeff-medium", "pt", "Jeff", "homme", "Brésil", "piper",
          "pt_BR-jeff-medium", "pt_BR-jeff-medium", None, 63),
    Voice("piper:pt_BR-edresson-low", "pt", "Edresson", "homme", "Brésil", "piper",
          "pt_BR-edresson-low", "pt_BR-edresson-low", None, 28),
]

# Voix neuronales en ligne de Microsoft Edge (« Online (Natural) ») : gratuites, sans
# clé, mais il faut Internet. Sans connexion, la voix Piper par défaut prend le relais.
_EDGE = [
    ("fr", "fr-FR-DeniseNeural", "Denise", "femme"),
    ("fr", "fr-FR-EloiseNeural", "Eloise", "femme"),
    ("fr", "fr-FR-VivienneMultilingualNeural", "Vivienne", "femme"),
    ("fr", "fr-FR-HenriNeural", "Henri", "homme"),
    ("fr", "fr-FR-RemyMultilingualNeural", "Rémy", "homme"),
    ("fr", "fr-CH-ArianeNeural", "Ariane", "femme"),
    ("fr", "fr-CH-FabriceNeural", "Fabrice", "homme"),
    ("fr", "fr-BE-CharlineNeural", "Charline", "femme"),
    ("fr", "fr-BE-GerardNeural", "Gérard", "homme"),
    ("fr", "fr-CA-SylvieNeural", "Sylvie", "femme"),
    ("fr", "fr-CA-JeanNeural", "Jean", "homme"),
    ("fr", "fr-CA-AntoineNeural", "Antoine", "homme"),
    ("fr", "fr-CA-ThierryNeural", "Thierry", "homme"),
    ("pt", "pt-PT-RaquelNeural", "Raquel", "femme"),
    ("pt", "pt-PT-DuarteNeural", "Duarte", "homme"),
    ("pt", "pt-BR-FranciscaNeural", "Francisca", "femme"),
    ("pt", "pt-BR-ThalitaMultilingualNeural", "Thalita", "femme"),
    ("pt", "pt-BR-AntonioNeural", "Antônio", "homme"),
    ("de", "de-DE-SeraphinaMultilingualNeural", "Seraphina", "femme"),
    ("de", "de-DE-KatjaNeural", "Katja", "femme"),
    ("de", "de-DE-FlorianMultilingualNeural", "Florian", "homme"),
    ("de", "de-DE-ConradNeural", "Conrad", "homme"),
    ("de", "de-AT-IngridNeural", "Ingrid", "femme"),
    ("de", "de-CH-LeniNeural", "Leni", "femme"),
    ("en", "en-US-AvaMultilingualNeural", "Ava", "femme"),
    ("en", "en-US-AndrewMultilingualNeural", "Andrew", "homme"),
    ("en", "en-GB-SoniaNeural", "Sonia", "femme"),
    ("en", "en-GB-RyanNeural", "Ryan", "homme"),
]

DEFAULT = {"fr": "piper:fr_FR-siwis-medium", "pt": "piper:pt_PT-tugao-medium",
           "en": "edge:en-US-AvaMultilingualNeural", "de": "piper:de_DE-thorsten-medium"}
# Autres langues du catalogue : voix Edge déclarées dans languages.py
from .languages import LANGS as _LANGS  # noqa: E402
for _l in _LANGS.values():
    for _key, _name, _gender in _l.edge_voices:
        _EDGE.append((_l.code, _key, _name, _gender))
    if _l.edge_voices and _l.code not in DEFAULT:
        DEFAULT[_l.code] = "edge:" + _l.edge_voices[0][0]
ACCENTS = {"fr-FR": "France", "fr-CA": "Canada", "fr-BE": "Belgique", "fr-CH": "Suisse",
           "pt-PT": "Portugal", "pt-BR": "Brésil", "en-US": "États-Unis",
           "en-GB": "Royaume-Uni", "de-DE": "Allemagne", "de-AT": "Autriche",
           "de-CH": "Suisse", "es-ES": "Espagne", "es-MX": "Mexique", "it-IT": "Italie",
           "nl-NL": "Pays-Bas", "nl-BE": "Belgique", "pl-PL": "Pologne"}
EDGE = [Voice(f"edge:{key}", lang, name, gender, ACCENTS["-".join(key.split("-")[:2])], "edge",
              key) for lang, key, name, gender in _EDGE]


# --------------------------------------------------------------------------- Piper
def local_path(v: Voice) -> Path:
    return VOICES_DIR / f"{v.file}.onnx"


def installed(v: Voice) -> bool:
    if v.engine in ("windows", "edge"):
        return True
    p = local_path(v)
    return p.exists() and Path(str(p) + ".json").exists()


def _url(v: Voice, ext: str) -> str:
    lang_region, name, quality = v.key.split("-")
    family = lang_region.split("_")[0]
    q = urllib.parse.quote
    return f"{PIPER_BASE}/{family}/{lang_region}/{q(name)}/{quality}/{q(v.key)}{ext}?download=true"


def download(v: Voice, progress: Callable[[int], None] | None = None) -> None:
    """Télécharge une voix Piper (modèle + configuration)."""
    if v.engine != "piper" or installed(v):
        return
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    for ext in (".onnx.json", ".onnx"):
        dest = Path(str(local_path(v)).replace(".onnx", "") + ext)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(_url(v, ext), timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and total and ext == ".onnx":
                    progress(done * 100 // total)
        tmp.replace(dest)
    log.info("Voix téléchargée : %s", v.key)


# --------------------------------------------------------------------------- Windows
def windows_voices(lang: str) -> list[Voice]:
    if sys.platform != "win32":
        return []
    try:
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer, VoiceGender
    except Exception:
        return []
    out = []
    try:
        for info in SpeechSynthesizer.all_voices:
            tag = info.language or ""
            if not tag.lower().startswith(lang):
                continue
            name = info.display_name.replace("Microsoft ", "").split(" - ")[0].strip()
            gender = "femme" if info.gender == VoiceGender.FEMALE else "homme"
            out.append((tag.lower(), Voice(f"windows:{info.id}", lang, name, gender,
                                           ACCENTS.get(tag, tag), "windows")))
    except Exception:
        log.exception("Voix Windows illisibles")
    # Variante préférée d'abord (portugais du Portugal, français de France…)
    preferred = {"pt": "pt-pt", "fr": "fr-fr", "en": "en-us", "de": "de-de", "es": "es-es"}
    pref = preferred.get(lang, "")
    out.sort(key=lambda tv: 0 if tv[0] == pref else 1)
    return [v for _, v in out]


# --------------------------------------------------------------------------- choix
def catalog(lang: str) -> list[Voice]:
    return ([v for v in EDGE if v.lang == lang] + [v for v in PIPER if v.lang == lang]
            + windows_voices(lang))


def find(voice_id: str, lang: str) -> Voice | None:
    for v in catalog(lang):
        if v.id == voice_id:
            return v
    return None


def from_config(value: str, lang: str) -> Voice:
    """Voix choisie dans config.toml. Accepte l'ancien format (chemin d'un .onnx)."""
    value = (value or "").strip()
    if value.endswith(".onnx"):               # ancienne configuration
        stem = Path(value).stem
        value = next((v.id for v in PIPER if v.file == stem and v.speaker in (None, 0)),
                     DEFAULT.get(lang, ""))
    v = find(value, lang) if value else None
    if v is None:
        v = find(DEFAULT[lang], lang) or PIPER[0]
    return v
