"""Reconnaissance vocale avec faster-whisper (Whisper large-v3-turbo), en local."""
from __future__ import annotations

import logging
import os
import re
import site
import sys
import sysconfig
import unicodedata
from dataclasses import dataclass

import numpy as np

from .config import MODELS_DIR

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Phrases que Whisper "invente" parfois sur du silence (sous-titres de vidéos).
HALLUCINATIONS = [
    "sous-titres réalisés par la communauté d'amara.org",
    "sous-titrage st' 501",
    "sous-titrage société radio-canada",
    "merci d'avoir regardé cette vidéo",
    "merci d'avoir regardé",
    "abonnez-vous",
    "legendas pela comunidade amara.org",
    "obrigado por assistir",
    "obrigada por assistir",
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amara.org community",
]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text.lower())
    return re.sub(r"[^\w']+", " ", text).strip()


_HALLU_NORM = [_norm(h) for h in HALLUCINATIONS]


def is_hallucination(text: str) -> bool:
    n = _norm(text)
    return bool(n) and any(h == n or (len(n) > 12 and h in n and len(n) < len(h) + 15)
                           for h in _HALLU_NORM)


def add_cuda_dll_dirs() -> None:
    """Rend visibles les DLL CUDA installées par pip (cuBLAS, cuDNN) sous Windows."""
    if sys.platform != "win32":
        return
    bases = set(site.getsitepackages())
    try:
        bases.add(site.getusersitepackages())
    except Exception:
        pass
    bases.add(sysconfig.get_paths()["purelib"])
    found = []
    for base in bases:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            path = os.path.join(base, "nvidia", sub, "bin")
            if os.path.isdir(path) and path not in found:
                found.append(path)
    for path in found:
        try:
            os.add_dll_directory(path)
        except OSError:
            pass
    if found:
        os.environ["PATH"] = os.pathsep.join(found) + os.pathsep + os.environ.get("PATH", "")
        log.info("DLL CUDA ajoutées : %s", found)


def neutralize_av() -> None:
    """faster-whisper importe PyAV (décodage de fichiers audio) dès son chargement.
    Vocalis lui donne directement le son du micro et n'en a jamais besoin.
    Smart App Control (Windows 11) bloque les DLL de PyAV, et les examine
    longuement au démarrage (jusqu'à 30 s) : on le remplace d'office par un module
    vide, sans même essayer de le charger."""
    import types
    if "av" in sys.modules and getattr(sys.modules["av"], "__file__", None):
        return
    for name in [n for n in sys.modules if n == "av" or n.startswith("av.")]:
        del sys.modules[name]
    stub = types.ModuleType("av")
    stub.__path__ = []  # se comporte comme un paquet vide
    sys.modules["av"] = stub


@dataclass
class Word:
    start: float
    end: float
    text: str


class Asr:
    live_window = 15.0          # secondes retranscrites à chaque passe de l'aperçu

    def __init__(self, cfg: dict, model_name: str | None = None,
                 device: str | None = None):
        add_cuda_dll_dirs()
        neutralize_av()
        from faster_whisper import WhisperModel

        d = cfg["dictee"]
        self.allowed = [l for l in d["langues"] if l] or ["fr"]
        self.vocab = d.get("vocabulaire", "").strip()
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        name = model_name or d["modele_whisper"]
        device = device or d["appareil"]
        compute = d["type_calcul"] if device == "cuda" else "int8"
        self.device = device
        self.gpu_error = ""
        try:
            self.model = WhisperModel(name, device=device, compute_type=compute,
                                      download_root=str(MODELS_DIR))
            if device == "cuda":
                self._smoke_test()
            self.model_name = name
        except Exception as exc:
            if device == "cpu":
                raise
            self.gpu_error = str(exc)
            log.exception("Carte graphique indisponible, passage sur le processeur")
            self.device = "cpu"
            self.model_name = d.get("modele_secours_cpu", "small")
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                      download_root=str(MODELS_DIR))
        if self.device == "cpu":
            self.live_window = 8.0
        log.info("Whisper prêt : %s sur %s", self.model_name, self.device)

    def _smoke_test(self) -> None:
        """Vérifie que le calcul passe vraiment sur la carte (erreurs CUDA tardives)."""
        audio = (np.random.RandomState(0).randn(SAMPLE_RATE) * 0.01).astype(np.float32)
        segments, _ = self.model.transcribe(audio, language="fr", beam_size=1,
                                            vad_filter=False)
        list(segments)

    def detect_language(self, audio: np.ndarray) -> tuple[str, float]:
        """Langue la plus probable parmi celles autorisées, avec sa probabilité."""
        if len(self.allowed) == 1:
            return self.allowed[0], 1.0
        try:
            _, _, probs = self.model.detect_language(audio[: SAMPLE_RATE * 30])
            best = max((p for p in probs if p[0] in self.allowed),
                       key=lambda p: p[1], default=None)
            if best:
                log.info("Langue détectée : %s (%.2f)", best[0], best[1])
                return best[0], float(best[1])
        except Exception:
            log.exception("Détection de langue impossible")
        return self.allowed[0], 0.0

    def transcribe_words(self, audio: np.ndarray, language: str, prompt: str = "",
                         beam_size: int = 2,
                         condition_on_previous_text: bool = False) -> list[Word]:
        full_prompt = ", ".join(x for x in (self.vocab, prompt) if x) or None
        segments, _ = self.model.transcribe(
            audio,
            language=language,
            beam_size=beam_size,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
            condition_on_previous_text=condition_on_previous_text,
            initial_prompt=full_prompt,
            temperature=0.0,
        )
        words: list[Word] = []
        for seg in segments:
            if is_hallucination(seg.text):
                log.info("Phrase parasite ignorée : %r", seg.text)
                continue
            if seg.no_speech_prob > 0.7 and seg.avg_logprob < -0.8:
                continue
            for w in seg.words or []:
                if w.word.strip():
                    words.append(Word(float(w.start), float(w.end), w.word))
        return words

    def transcribe_final(self, audio: np.ndarray, language: str, beam_size: int = 5) -> str:
        """Texte complet de l'enregistrement, avec la meilleure précision."""
        words = self.transcribe_words(audio, language, "", beam_size,
                                      condition_on_previous_text=True)
        return "".join(w.text for w in words).strip()


def build_asr(cfg: dict):
    """Reconnaissance vocale sur la carte graphique, ou sur le processeur sans elle."""
    from .hardware import has_nvidia_gpu
    d = cfg["dictee"]
    if d["appareil"] == "cuda" and not has_nvidia_gpu():
        # Pas de carte NVIDIA : on ne télécharge pas le gros modèle pour rien.
        asr = Asr(cfg, model_name=d.get("modele_secours_cpu", "small"), device="cpu")
        asr.gpu_error = "Aucune carte NVIDIA (pilote 570 ou plus) détectée"
        return asr
    return Asr(cfg)
