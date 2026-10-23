"""Reconnaissance vocale avec Parakeet TDT 0.6B v3 (NVIDIA), sur le processeur.

Modèle multilingue (25 langues européennes, dont français, portugais et anglais),
ponctuation et majuscules incluses. Il tourne avec sherpa-onnx, sans carte graphique.
Il détecte la langue tout seul : on ne peut pas la lui imposer.
"""
from __future__ import annotations

import importlib
import logging
import os
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np

from .asr import SAMPLE_RATE, Word
from .config import MODELS_DIR

log = logging.getLogger(__name__)

NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
       f"{NAME}.tar.bz2")
SIZE_MB = 465
FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
MAX_CHUNK_S = 40           # morceaux retranscrits séparément (mémoire, vitesse)


def model_dir() -> Path:
    return MODELS_DIR / NAME


def installed() -> bool:
    d = model_dir()
    return all((d / f).exists() for f in FILES)


def download(progress: Callable[[int], None] | None = None) -> None:
    """Télécharge et décompresse le modèle (environ 465 Mo, une seule fois)."""
    if installed():
        return
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    archive = MODELS_DIR / f"{NAME}.tar.bz2"
    tmp = archive.with_suffix(".part")
    log.info("Téléchargement de Parakeet : %s", URL)
    with urllib.request.urlopen(URL, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    tmp.replace(archive)
    with tarfile.open(archive, "r:bz2") as tar:
        members = [m for m in tar.getmembers()
                   if m.isfile() and Path(m.name).name in FILES]
        for m in members:
            m.name = f"{NAME}/{Path(m.name).name}"
            tar.extract(m, MODELS_DIR)
    archive.unlink(missing_ok=True)
    if not installed():
        raise RuntimeError("Archive de Parakeet incomplète")


def split_points(audio: np.ndarray, max_s: float = MAX_CHUNK_S) -> list[tuple[int, int]]:
    """Découpe un long enregistrement aux silences (au plus max_s secondes)."""
    n, step = len(audio), int(max_s * SAMPLE_RATE)
    if n <= step:
        return [(0, n)]
    out, start = [], 0
    frame = int(0.1 * SAMPLE_RATE)
    while n - start > step:
        lo, hi = start + step // 2, start + step
        seg = audio[lo:hi]
        k = len(seg) // frame
        energy = np.sqrt(np.mean(seg[: k * frame].reshape(k, frame) ** 2, axis=1))
        cut = lo + int(np.argmin(energy)) * frame + frame // 2
        out.append((start, cut))
        start = cut
    out.append((start, n))
    return out


class ParakeetAsr:
    device = "cpu"
    live_window = 8.0

    def __init__(self, cfg: dict):
        # sherpa-onnx et Piper (lecture) apportent chacun leur onnxruntime.dll. Sous
        # Windows, le premier chargé sert aux deux : on charge d'abord celui de Piper
        # (le plus récent), qui accepte aussi les appels de sherpa-onnx.
        try:
            importlib.import_module("onnxruntime")
        except Exception:
            pass
        import sherpa_onnx
        d = cfg["dictee"]
        self.allowed = [l for l in d["langues"] if l] or ["fr"]
        self.vocab = d.get("vocabulaire", "").strip()
        self.preferred = d.get("langue_preferee", "") or self.allowed[0]
        self.model_name = "Parakeet v3"
        self.gpu_error = ""
        if not installed():
            download()
        m = model_dir()
        threads = max(1, min(8, (os.cpu_count() or 4) - 1))
        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(m / "encoder.int8.onnx"), decoder=str(m / "decoder.int8.onnx"),
            joiner=str(m / "joiner.int8.onnx"), tokens=str(m / "tokens.txt"),
            num_threads=threads, model_type="nemo_transducer")
        log.info("Parakeet prêt (%d fils de calcul)", threads)

    # Parakeet choisit la langue tout seul : on la devine ensuite d'après le texte.
    def detect_language(self, audio: np.ndarray) -> tuple[str, float]:
        return self.preferred, 0.0

    def guess_language(self, text: str, default: str) -> str:
        from . import langdetect
        return langdetect.detect(text, default, tuple(self.allowed))

    def _decode(self, audio: np.ndarray):
        s = self.rec.create_stream()
        s.accept_waveform(SAMPLE_RATE, audio.astype(np.float32, copy=False))
        self.rec.decode_stream(s)
        return s.result

    def transcribe_words(self, audio: np.ndarray, language: str = "", prompt: str = "",
                         beam_size: int = 1, **_) -> list[Word]:
        if len(audio) < SAMPLE_RATE // 4:
            return []
        res = self._decode(audio)
        return tokens_to_words(list(res.tokens), list(res.timestamps), len(audio) / SAMPLE_RATE)

    def transcribe_final(self, audio: np.ndarray, language: str = "", beam_size: int = 1) -> str:
        parts = []
        for a, b in split_points(audio):
            if b - a >= SAMPLE_RATE // 4:
                parts.append(self._decode(audio[a:b]).text.strip())
        return " ".join(p for p in parts if p).strip()


def tokens_to_words(tokens: list[str], times: list[float], total: float) -> list[Word]:
    """Regroupe les morceaux de mots (BPE) en mots avec leurs instants.
    Un morceau qui commence par une espace (ou ▁) commence un nouveau mot."""
    words: list[Word] = []
    for i, (tok, t) in enumerate(zip(tokens, times)):
        tok = tok.replace("▁", " ")
        end = times[i + 1] if i + 1 < len(times) else total
        if not words or tok.startswith(" "):
            if tok.strip() or not words:
                words.append(Word(float(t), float(end), tok if tok.startswith(" ") else " " + tok))
            continue
        w = words[-1]
        words[-1] = Word(w.start, float(end), w.text + tok)
    return [w for w in words if w.text.strip()]
