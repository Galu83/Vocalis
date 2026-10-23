"""Apprentissage des corrections : après une dictée collée, Vocalis relit la case
chaque seconde pendant deux minutes. Si un mot dicté y est corrigé à la main
(« vocalisse » -> « Vocalis »), la correction est retenue :
- tout de suite si elle est sûre (nom, marque, sigle, mots collés) ;
- sinon, Vocalis demande « Retenir ? » (Oui / Non).
La dernière version de la case compte : quand on la quitte, quand le texte part
(message envoyé) ou au bout de deux minutes. Tout se passe sur ce PC."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import dictionary

log = logging.getLogger(__name__)

WATCH_SECONDS = 120
POLL_SECONDS = 1.0


class CorrectionWatcher:
    def __init__(self, uia, on_learned: Callable[[str, str], None],
                 on_suggest: Callable[[str, str], None] | None = None):
        self.uia = uia
        self.on_learned = on_learned
        self.on_suggest = on_suggest
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
        """Une nouvelle dictée commence : l'observation en cours s'arrête."""
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
            log.info("Apprentissage : case illisible (l'application ne donne pas son texte)")
            return
        key, before = first
        if " ".join(inserted.split()) not in " ".join(before.replace("\r", "\n").split()):
            log.info("Apprentissage : texte dicté introuvable dans la case")
            return
        log.info("Apprentissage : j'observe la case (%d caractères)", len(before))
        seen: dict[tuple[str, str], bool] = {}     # correction -> sûre ?
        previous: set = set()
        end = time.monotonic() + WATCH_SECONDS
        while time.monotonic() < end and gen == self._gen:
            time.sleep(POLL_SECONDS)
            snap = self._snap()
            if not snap or snap[0] != key or not dictionary.inserted_present(snap[1], inserted):
                # On a quitté la case, ou le texte est parti (message envoyé) :
                # la dernière version vue compte.
                for pair in previous:
                    self._apply(pair, seen[pair])
                log.info("Apprentissage : fin de l'observation")
                return
            try:
                found = dictionary.find_corrections(before, snap[1], inserted)
            except Exception:
                log.exception("Comparaison impossible")
                return
            current = set()
            for old, new, sure in found:
                current.add((old, new))
                seen[(old, new)] = sure
            # On ne retient qu'à la fin (case quittée, texte envoyé ou deux minutes) :
            # un mot en cours de frappe (« Voca… ») n'est jamais pris pour une correction.
            previous = current
        for pair in previous:
            self._apply(pair, seen[pair])

    def _apply(self, pair: tuple[str, str], sure: bool) -> None:
        old, new = pair
        log.info("Apprentissage : « %s » -> « %s » (%s)", old, new,
                 "sûr" if sure else "à proposer")
        try:
            if sure:
                if dictionary.learn(old, new):
                    self.on_learned(old, new)
            elif self.on_suggest is not None:
                self.on_suggest(old, new)
        except Exception:
            log.exception("Apprentissage impossible")
