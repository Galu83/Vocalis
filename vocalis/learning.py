"""Apprentissage des corrections : après une dictée collée, Vocalis observe la case
pendant deux minutes. Si un mot dicté y est corrigé à la main (« vocalisse » ->
« Vocalis »), la correction est ajoutée au dictionnaire de la dictée.
Rien n'est envoyé nulle part : la comparaison se fait sur ce PC."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import dictionary

log = logging.getLogger(__name__)

WATCH_SECONDS = 120
POLL_SECONDS = 2.0


class CorrectionWatcher:
    def __init__(self, uia, on_learned: Callable[[str, str], None]):
        self.uia = uia
        self.on_learned = on_learned
        self._gen = 0

    def watch(self, inserted: str) -> None:
        if not getattr(self.uia, "available", False) or not inserted.strip():
            return
        try:
            if not dictionary.load().get("apprendre", True):
                return
        except Exception:
            return
        self._gen += 1
        threading.Thread(target=self._run, args=(self._gen, inserted), daemon=True,
                         name="apprentissage").start()

    def stop(self) -> None:
        """Une nouvelle dictée commence : l'observation en cours conclut tout de suite."""
        self._gen += 1

    def _snap(self):
        try:
            return self.uia.field_snapshot().result(timeout=3)
        except Exception:
            return None

    def _run(self, gen: int, inserted: str) -> None:
        time.sleep(0.6)                    # le collage doit être arrivé dans la case
        first = self._snap()
        if not first:
            return
        key, before = first
        if " ".join(inserted.split()) not in " ".join(before.replace("\r", "\n").split()):
            log.debug("Apprentissage : texte dicté introuvable dans la case")
            return
        last = before
        end = time.monotonic() + WATCH_SECONDS
        while time.monotonic() < end and gen == self._gen:
            time.sleep(POLL_SECONDS)
            snap = self._snap()
            if not snap or snap[0] != key:
                break                      # on a quitté la case : on compare
            last = snap[1]
        if last == before:
            return
        try:
            for old, new in dictionary.find_corrections(before, last, inserted):
                if dictionary.learn(old, new):
                    self.on_learned(old, new)
        except Exception:
            log.exception("Apprentissage des corrections impossible")
