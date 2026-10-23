"""Raccourcis clavier globaux (Ctrl + Alt + J, F8…).

Plutôt que les « hotkeys » de la bibliothèque keyboard (dont la suppression des
combinaisons avec modificateurs est capricieuse sous Windows), on écoute la touche
principale (J, K, L…) et on vérifie les modificateurs au moment de l'appui :
- combinaison complète : la touche est avalée et l'action déclenchée ;
- sinon : la touche passe normalement (J reste utilisable pour écrire).
Le relâchement est détecté sur la touche principale, dans n'importe quel ordre.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

MODIFIERS = ("ctrl", "alt", "shift", "windows")


def parse(hotkey: str) -> tuple[list[str], str]:
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError("raccourci vide")
    mods = [p for p in parts[:-1]]
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"modificateur inconnu : {m}")
    return mods, parts[-1]


class Combo:
    """Une combinaison : on_down à l'appui (une seule fois, sans répétition
    automatique), on_up au relâchement de la touche principale."""

    def __init__(self, kb, hotkey: str, on_down: Callable[[], None],
                 on_up: Callable[[], None] | None = None, name: str = ""):
        self.kb = kb
        self.mods, self.key = parse(hotkey)
        self.on_down = on_down
        self.on_up = on_up
        self.name = name or hotkey
        self.active = False
        self.remove = kb.hook_key(self.key, self._event, suppress=True)

    def _mods_match(self) -> bool:
        for m in MODIFIERS:
            pressed = self._pressed(m)
            if (m in self.mods) != pressed:
                return False
        return True

    def _pressed(self, name: str) -> bool:
        try:
            return bool(self.kb.is_pressed(name))
        except Exception:      # nom inconnu sur ce clavier
            return False

    def _event(self, event) -> bool:
        """Renvoie True pour laisser passer la touche, False pour l'avaler."""
        down = event.event_type == self.kb.KEY_DOWN
        if down:
            if self.active:
                return False            # répétition automatique : on avale
            if not self._mods_match():
                return True             # simple frappe : on laisse passer
            self.active = True
            log.info("Raccourci : %s", self.name)
            try:
                self.on_down()
            except Exception:
                log.exception("Action du raccourci %s", self.name)
            return False
        if self.active:                 # relâchement de notre combinaison
            self.active = False
            if self.on_up:
                try:
                    self.on_up()
                except Exception:
                    log.exception("Relâchement du raccourci %s", self.name)
            return False
        return True
