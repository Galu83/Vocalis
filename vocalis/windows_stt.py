"""Dictée avec la reconnaissance vocale en ligne de Windows (celle de Win+H).

Gratuite, sans clé, rien à télécharger. Windows écoute lui-même le micro par
défaut ; le texte part chez Microsoft. Il faut que « Reconnaissance vocale en
ligne » soit activée dans Paramètres > Confidentialité et sécurité > Voix.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import timedelta

import numpy as np

from .dictation import DictationSession

log = logging.getLogger(__name__)

from .languages import LANGS  # noqa: E402

TAGS = {c: list(l.windows_speech) for c, l in LANGS.items()}
LANG_NAMES = {c: l.name.lower() for c, l in LANGS.items()}

PRIVACY_HRESULT = 0x80045509          # reconnaissance vocale en ligne désactivée
PRIVACY_MSG = ("Active « Reconnaissance vocale en ligne » : Paramètres Windows > "
               "Confidentialité et sécurité > Voix")


class WindowsEngine:
    """Rien à charger : sert seulement à indiquer le moteur choisi."""
    device = "windows"
    model_name = "Windows (en ligne)"
    gpu_error = ""

    def __init__(self, cfg: dict):
        d = cfg["dictee"]
        self.allowed = [l for l in d["langues"] if l] or ["fr"]


def supported_tags() -> list[str]:
    from winrt.windows.media.speechrecognition import SpeechRecognizer
    return [l.language_tag for l in SpeechRecognizer.supported_topic_languages]


def pick_tag(lang: str, supported: list[str]) -> str | None:
    low = [s.lower() for s in supported]
    for tag in TAGS.get(lang, []):
        if tag.lower() in low:
            return supported[low.index(tag.lower())]
    return next((s for s in supported if s.lower().startswith(lang + "-")), None)


def _hresult(exc: BaseException) -> int:
    code = getattr(exc, "winerror", None)
    if code is None and exc.args and isinstance(exc.args[0], int):
        code = exc.args[0]
    return (code or 0) & 0xFFFFFFFF


class WindowsDictationSession(DictationSession):
    """Même fonctionnement que la dictée habituelle (aperçu en direct, texte collé
    à la fin, correction), mais la reconnaissance est faite par Windows."""

    def __init__(self, cfg: dict, cleaner, on_state, translator=None):
        super().__init__(cfg, None, cleaner, on_state, translator)
        self._lock = threading.Lock()
        self._segments: list[str] = []
        self._hypothesis = ""
        self._completed = threading.Event()
        self._end_status = ""

    # ------------------------------------------------------------------ niveau du micro
    def _level_stream(self):
        """Petit flux micro, seulement pour animer la pastille."""
        try:
            import sounddevice as sd

            def cb(indata, frames, t, status):
                rms = float(np.sqrt(np.mean(indata[:, 0] ** 2))) if frames else 0.0
                self.level = min(1.0, rms * 12)

            s = sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                               blocksize=800, callback=cb)
            s.start()
            return s
        except Exception:
            log.debug("Niveau du micro indisponible", exc_info=True)
            return None

    # ------------------------------------------------------------------ événements Windows
    def _on_result(self, result) -> None:
        from winrt.windows.media.speechrecognition import SpeechRecognitionResultStatus
        text = (result.text or "").strip()
        ok = result.status == SpeechRecognitionResultStatus.SUCCESS
        with self._lock:
            if ok and text:
                self._segments.append(text)
            self._hypothesis = ""

    def _on_hypothesis(self, text: str) -> None:
        with self._lock:
            self._hypothesis = (text or "").strip()

    def _on_completed(self, status) -> None:
        self._end_status = str(getattr(status, "name", status))
        self._completed.set()

    def _current_text(self) -> str:
        with self._lock:
            spoken = " ".join(self._segments + ([self._hypothesis] if self._hypothesis else []))
        return spoken or self.selection

    def _live(self) -> str:
        with self._lock:
            return json.dumps(["", self._hypothesis, list(self._segments)],
                              ensure_ascii=False)

    # ------------------------------------------------------------------ déroulement
    def run(self) -> None:
        import asyncio
        try:
            asyncio.run(self._main())
        except Exception as exc:
            log.exception("Erreur de la dictée Windows")
            msg = PRIVACY_MSG if _hresult(exc) == PRIVACY_HRESULT else f"Erreur : {exc}"
            self.on_state("error", msg)
        finally:
            self.level = 0.0

    async def _main(self) -> None:
        import asyncio

        from winrt.windows.globalization import Language
        from winrt.windows.media.speechrecognition import (
            SpeechRecognitionResultStatus, SpeechRecognitionScenario,
            SpeechRecognitionTopicConstraint, SpeechRecognizer)

        from . import winutil

        lang = self.forced or self.preferred or "fr"
        supported = supported_tags()
        tag = pick_tag(lang, supported)
        if tag is None:
            self.on_state("error", f"Windows ne sait pas dicter en {LANG_NAMES.get(lang, lang)} "
                                   "sur ce PC : ajoute la langue avec « Reconnaissance "
                                   "vocale » dans Paramètres > Heure et langue")
            return
        if lang == "pt" and tag.lower() != "pt-pt":
            self.note = "Portugais du Portugal indisponible : portugais du Brésil utilisé"
        log.info("Dictée Windows en %s (langues disponibles : %s)", tag, supported)

        self.hwnd = winutil.foreground_window()
        rec = SpeechRecognizer(Language(tag))
        rec.constraints.append(SpeechRecognitionTopicConstraint(
            SpeechRecognitionScenario.DICTATION, "dictee"))
        comp = await rec.compile_constraints_async()
        if comp.status != SpeechRecognitionResultStatus.SUCCESS:
            self.on_state("error", f"Reconnaissance Windows indisponible ({comp.status.name})")
            return
        session = rec.continuous_recognition_session
        session.auto_stop_silence_timeout = timedelta(hours=1)
        session.add_result_generated(lambda s, a: self._on_result(a.result))
        session.add_completed(lambda s, a: self._on_completed(a.status))
        rec.add_hypothesis_generated(lambda s, a: self._on_hypothesis(a.hypothesis.text))

        level = self._level_stream()
        try:
            try:
                await session.start_async()
            except OSError as exc:
                if _hresult(exc) == PRIVACY_HRESULT:
                    self.on_state("error", PRIVACY_MSG)
                    return
                raise
            self.on_state("listening", "")
            self._report_language(None)
            shown = ""
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)
                if self._completed.is_set():       # Windows a arrêté de lui-même
                    break
                if self.live:
                    live = self._live()
                    if live != shown:
                        shown = live
                        self.on_state("live", live)
            if self.cancelled:
                try:
                    await session.cancel_async()
                except Exception:
                    pass
                log.info("Dictée annulée")
                self.on_state("cancelled", "")
                return
            self.on_state("finishing", "")
            if not self._completed.is_set():
                try:
                    await session.stop_async()     # renvoie les derniers résultats
                except Exception:
                    log.debug("Arrêt de la session", exc_info=True)
                for _ in range(40):
                    if self._completed.is_set():
                        break
                    await asyncio.sleep(0.05)
        finally:
            if level is not None:
                try:
                    level.stop()
                    level.close()
                except Exception:
                    pass
            self.level = 0.0
            try:
                rec.close()
            except Exception:
                pass

        status = self._end_status.upper()
        with self._lock:
            raw = " ".join(self._segments).strip()
        log.info("Texte brut Windows [%s] (fin : %s) : %s", tag, status, raw)
        if not raw and "NETWORK" in status:
            self.on_state("error", "Pas de connexion : la dictée Windows a besoin d'Internet")
            return
        if not raw and "MICROPHONE" in status:
            self.on_state("error", "Micro indisponible pour Windows (micro par défaut)")
            return
        self._complete(raw, lang)
