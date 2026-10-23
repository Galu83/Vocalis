"""Une session de dictée (marche / arrêt).

Pendant la dictée : rien n'est écrit dans la case. La fenêtre de dictée affiche
la phrase en cours mot par mot, et les phrases validées en dessous.

À l'arrêt : tout l'enregistrement est retranscrit, le texte entier est corrigé
d'un coup, puis il est collé à l'endroit du curseur (un seul Ctrl+Z l'annule).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from . import winutil
from .asr import SAMPLE_RATE
from .streaming import StreamingTranscriber

log = logging.getLogger(__name__)


def find_input_device(name_part: str):
    if not name_part:
        return None
    import sounddevice as sd
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name_part.lower() in dev["name"].lower():
            return idx
    log.warning("Micro %r introuvable, micro par défaut utilisé", name_part)
    return None


class DictationSession(threading.Thread):
    """États envoyés à on_state : listening, finishing, cleaning, done, error."""

    def __init__(self, cfg: dict, asr, cleaner, on_state: Callable[[str, str], None],
                 translator=None):
        super().__init__(daemon=True, name="dictee")
        d = cfg["dictee"]
        self.asr = asr
        self.cleaner = cleaner
        self.on_state = on_state
        self.live = bool(d["ecrire_en_direct"])
        self.replace = bool(d["remplacer_par_version_nettoyee"])
        self.interval = float(d["intervalle_secondes"])
        if getattr(asr, "device", "cuda") != "cuda":
            # Sur le processeur, l'aperçu est plus lent à calculer.
            self.interval = max(self.interval, 1.0)
        self.device = find_input_device(d.get("micro", ""))
        self.forced = d.get("langue_forcee", "")
        self.preferred = d.get("langue_preferee", "")
        # Traduction : langue cliquée dans l'encadré (vide = pas de traduction).
        # Sélection : texte sélectionné quand on a appuyé sur la touche. Si l'on parle,
        # la dictée la remplace ; sinon, une langue cliquée la traduit.
        self.translator = translator
        self.target = ""
        self.selection = ""
        self._preview_id = 0
        self.st: StreamingTranscriber | None = None
        self._stop_event = threading.Event()
        self._audio: "queue.Queue[np.ndarray]" = queue.Queue()
        self.level = 0.0
        self.typed = ""          # aperçu actuellement écrit dans la case
        self.hwnd = 0
        self.window_lost = False
        self.cancelled = False
        self.note = ""
        self.ask_mode = False    # « Demander » : la phrase dite est une question
        self.preset = ""         # « Demander » : bouton tout prêt choisi

    # appelé depuis l'interface
    def request_stop(self) -> None:
        self._stop_event.set()

    def cancel(self) -> None:
        """Abandonne la dictée : rien n'est retranscrit ni collé."""
        self.cancelled = True
        self._stop_event.set()

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    def set_language(self, lang: str) -> None:
        """Clic sur la pastille pendant la dictée."""
        self.preferred = lang
        if self.st is not None:
            self.st.set_language(lang)

    def _report_language(self, st: StreamingTranscriber | None) -> None:
        """Signale à la pastille la langue utilisée (et si elle a changé toute seule)."""
        lang = (st.lang or st.live_language) if st is not None else (self.preferred or "fr")
        state = f"{lang}|{'auto' if self.preferred and lang != self.preferred else ''}"
        if state != getattr(self, "_lang_shown", ""):
            self._lang_shown = state
            self.on_state("lang", state)

    # ------------------------------------------------------------------ sélection, traduction
    def set_selection(self, text: str) -> None:
        self.selection = text.strip()
        self.on_state("selection", self.selection[:600] if self.ask_mode else self.selection)
        if self.target:
            self._preview()

    def set_target(self, lang: str) -> None:
        """Clic sur une langue : la choisir, ou l'enlever si elle l'était déjà."""
        if self.ask_mode:
            self.preset = lang               # bouton tout prêt : on valide tout de suite
            self.request_stop()
            return
        self.target = "" if lang == self.target else lang
        log.info("Traduction choisie : %s", self.target or "aucune")
        self.on_state("target", self.target)
        self.on_state("translation", "")
        if self.target:
            self._preview()

    def _current_text(self) -> str:
        """Ce qui serait traduit maintenant : la dictée, ou à défaut la sélection."""
        spoken = ""
        if self.st is not None:
            sure, maybe, done = self.st.live_parts()
            spoken = " ".join(x for x in (*done, sure, maybe) if x)
        return spoken or self.selection

    def _preview(self) -> None:
        """Aperçu de la traduction de ce qui est déjà là (sélection + dictée)."""
        text = self._current_text()
        if not text or self.translator is None:
            return
        self._preview_id += 1
        pid, target = self._preview_id, self.target

        def work():
            try:
                out = self.translator.translate(text, target)
            except Exception as exc:
                log.warning("Aperçu de traduction impossible : %s", exc)
                return
            if pid == self._preview_id and target == self.target and not self.stopping:
                self.on_state("translation", out)

        threading.Thread(target=work, daemon=True, name="apercu-traduction").start()

    def _finalize(self, raw: str, lang: str) -> str:
        """Correction de la dictée (qui remplace la sélection), ou à défaut la sélection
        elle-même, puis traduction si une langue a été choisie."""
        from . import dictionary
        raw = dictionary.apply_dictation(raw)
        spoken = raw
        if raw and self.cleaner.enabled and self.replace:
            self.on_state("cleaning", "")
            spoken = self.cleaner.clean(raw, lang) or raw
            log.info("Texte corrigé : %s", spoken)
        spoken = dictionary.apply_dictation(spoken)
        full = spoken if raw else self.selection
        if self.target and self.translator is not None and full:
            self.on_state("cleaning", "Traduction…")
            try:
                full = self.translator.translate(full, self.target)
                log.info("Texte traduit (%s) : %s", self.target, full)
            except Exception as exc:
                log.warning("Traduction impossible : %s", exc)
                self.note = "Traduction impossible : texte gardé dans sa langue"
        return full

    def _complete(self, raw: str, lang: str) -> None:
        """Fin de dictée commune à tous les moteurs : texte final, puis collage."""
        if self.ask_mode:
            # Rien n'est écrit : la question part vers l'assistant.
            self.on_state("question", json.dumps([raw, lang, self.preset],
                                                 ensure_ascii=False))
            return
        if not raw and not self.selection:
            if self.typed and self._same_window():
                winutil.backspace(len(self.typed))
            self.on_state("done", "Rien entendu")
            return
        if not raw and not self.target:
            self.on_state("done", "Aucun changement")       # sélection laissée telle quelle
            return
        final = self._finalize(raw, lang)
        if self.cancelled:                       # croix pendant la finalisation
            self.on_state("cancelled", "")
            return
        message = self._insert_final(final)
        self.on_state("done", message or self.note)

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0].copy()
        self._audio.put(mono)
        rms = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
        self.level = min(1.0, rms * 12)

    def _drain(self, st: StreamingTranscriber) -> None:
        while True:
            try:
                st.add_audio(self._audio.get_nowait())
            except queue.Empty:
                return

    def _same_window(self) -> bool:
        if not self.window_lost and winutil.foreground_window() != self.hwnd:
            self.window_lost = True
            log.info("Fenêtre changée pendant la dictée : écriture suspendue")
        return not self.window_lost

    def _insert_final(self, text: str) -> str:
        """Remplace l'aperçu par le texte final. Renvoie le message à afficher."""
        if not self._same_window():
            winutil.set_clipboard_text(text)
            return "Fenêtre changée : texte copié (Ctrl+V)"
        if self.typed == text:
            return ""
        if self.typed:
            winutil.select_left(len(self.typed))
            time.sleep(0.05)
        winutil.paste_text(text)
        self.typed = text
        if not self.target:
            self.on_state("inserted", text)      # pour apprendre des corrections
        return ""

    def run(self) -> None:
        import sounddevice as sd

        try:
            self.hwnd = winutil.foreground_window()
            st = self.st = StreamingTranscriber(self.asr, forced_language=self.forced,
                                                preferred=self.preferred)
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    blocksize=int(SAMPLE_RATE * 0.05), device=self.device,
                                    callback=self._callback)
            self.on_state("listening", "")
            self._report_language(st)
            last = time.monotonic()
            shown = ""
            with stream:
                while not self._stop_event.is_set():
                    time.sleep(0.03)
                    if time.monotonic() - last < self.interval:
                        continue
                    last = time.monotonic()
                    self._drain(st)
                    st.process()
                    self._report_language(st)
                    if self.live:
                        live = json.dumps(st.live_parts(), ensure_ascii=False)
                        if live != shown:
                            shown = live
                            self.on_state("live", live)
                time.sleep(0.3)  # garde la fin de la dernière syllabe
            self.level = 0.0
            if self.cancelled:
                log.info("Dictée annulée")
                self.on_state("cancelled", "")
                return
            self.on_state("finishing", "")
            self._drain(st)

            raw, lang = st.final_text()
            st.lang = lang
            self._report_language(st)
            log.info("Texte brut [%s] : %s", lang, raw)
            self._complete(raw, lang)
        except Exception as exc:
            log.exception("Erreur pendant la dictée")
            self.on_state("error", f"Erreur : {exc}")
