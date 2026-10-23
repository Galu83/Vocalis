"""Lecture du « vrai » texte des applications par l'accessibilité de Windows
(UI Automation) : navigateurs, Word, Outlook, PDF ouverts dans Edge…

Tout l'accès UI Automation se fait dans UN fil dédié (exigence COM). Les autres
parties de l'outil lui envoient des demandes et reçoivent des « futures ».

Principe :
- au clic, on récupère le paragraphe sous le point, puis les suivants ;
- pour situer un mot à l'écran, on le cherche (FindText) juste après le mot
  précédent : c'est robuste et on obtient sa position EN DIRECT, donc le cadre
  suit le défilement de la page.
"""
from __future__ import annotations

import logging
import queue
import re
import sys
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

PARAGRAPH, CHARACTER = 4, 0           # TextUnit
START, END = 0, 1                     # TextPatternRangeEndpoint
TEXT_PATTERN = 10014                  # PatternId.TextPattern
MAX_CHARS = 8000
FIELD_CHARS = 60000
MAX_PARAS = 60
MIN_SELECTION_WORDS = 3           # en dessous : on lit tout le paragraphe


@dataclass
class UiaDoc:
    paragraphs: list[str]
    start_para: int = 0               # paragraphe où commencer
    start_offset: int = 0             # position (caractères) du clic dans ce paragraphe
    selection: bool = False


@dataclass
class _State:
    ranges: list = field(default_factory=list)       # plages COM où chercher les mots
    para_range: list = field(default_factory=list)   # paragraphe -> indice dans ranges
    words: list = field(default_factory=list)        # (paragraphe, texte) par mot
    found: dict = field(default_factory=dict)        # mot -> plage COM
    last_scrolled: int = -1

    def group(self, word: int) -> int:
        return self.para_range[self.words[word][0]]


class UiaReader(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="uia")
        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self.available = sys.platform == "win32"
        self.state = _State()
        if self.available:
            self.start()

    # ------------------------------------------------------------------ fil dédié
    def run(self) -> None:
        try:
            import uiautomation as auto
            auto.InitializeUIAutomationInCurrentThread()
            self.auto = auto
        except Exception:
            log.exception("UI Automation indisponible : la lecture utilisera l'OCR")
            self.available = False
            return
        while True:
            fn, args, fut = self._jobs.get()
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except Exception as exc:
                log.debug("UIA : %s", exc)
                fut.set_exception(exc)

    def _submit(self, fn, *args) -> Future:
        fut: Future = Future()
        if not self.available:
            fut.set_result(None)
            return fut
        self._jobs.put((fn, args, fut))
        return fut

    # ------------------------------------------------------------------ API publique
    def paragraphs_at(self, x: int, y: int) -> Future:
        return self._submit(self._paragraphs_at, x, y)

    def selected_text(self) -> Future:
        """Texte sélectionné dans la zone où l'on écrit (sans rien modifier)."""
        return self._submit(self._selected_text)

    def focused_text(self) -> Future:
        """Texte de la zone où l'on écrit (pour deviner la langue de la réponse)."""
        return self._submit(self._focused_text)

    def selection(self) -> Future:
        return self._submit(self._selection)

    def caret_rect(self) -> Future:
        """Rectangle (pixels physiques) de la sélection, sinon du curseur de texte,
        dans l'application où l'on écrit ; None si introuvable."""
        return self._submit(self._caret_rect)

    def field_snapshot(self) -> Future:
        """(identifiant, texte) de la case où l'on écrit, ou None (apprentissage des
        corrections après une dictée)."""
        return self._submit(self._field_snapshot)

    def set_words(self, words: list[tuple[int, str]]) -> Future:
        return self._submit(self._set_words, words)

    def rects(self, word: int, para: int, autoscroll: bool) -> Future:
        """(rectangles du mot, rectangles visibles du paragraphe), en pixels physiques."""
        return self._submit(self._rects, word, para, autoscroll)

    # ------------------------------------------------------------------ outils COM
    def _pattern_up(self, ctrl):
        """Remonte les parents jusqu'à un élément qui fournit son texte."""
        import os
        for _ in range(25):
            if ctrl is None:
                return None
            try:
                if ctrl.ProcessId == os.getpid():
                    return None          # nos propres fenêtres
                pat = ctrl.GetPattern(TEXT_PATTERN)
                if pat is not None and getattr(pat, "pattern", None):
                    return pat.pattern
            except Exception:
                pass
            ctrl = ctrl.GetParentControl()
        return None

    def _text_pattern_at(self, x: int, y: int):
        return self._pattern_up(self.auto.ControlFromPoint(x, y))

    @staticmethod
    def _rect_list(rng) -> list[tuple[float, float, float, float]]:
        try:
            f = rng.GetBoundingRectangles()
        except Exception:
            return []
        f = list(f or [])
        return [tuple(f[i:i + 4]) for i in range(0, len(f) - 3, 4) if f[i + 2] > 0]

    @staticmethod
    def _text(rng) -> str:
        t = rng.GetText(-1) or ""
        return t.replace("\r\n", "\n").replace("\r", "\n").replace("￼", " ")

    def _collect(self, first) -> tuple[list, list[str]]:
        """Le paragraphe `first` et les suivants (dans la limite de MAX_CHARS)."""
        ranges, texts, total = [], [], 0
        cur = first
        while len(ranges) < MAX_PARAS and total < MAX_CHARS:
            text = self._text(cur)
            if text.strip():
                ranges.append(cur)
                texts.append(text.strip())
                total += len(text)
            nxt = cur.Clone()
            if nxt.Move(PARAGRAPH, 1) == 0:
                break
            nxt.ExpandToEnclosingUnit(PARAGRAPH)
            if nxt.CompareEndpoints(START, cur, START) <= 0:
                break
            cur = nxt
        return ranges, texts

    # ------------------------------------------------------------------ travaux
    def _paragraphs_at(self, x: int, y: int) -> UiaDoc | None:
        pat = self._text_pattern_at(x, y)
        if pat is None:
            return None
        from ctypes import wintypes
        point = pat.RangeFromPoint(wintypes.POINT(int(x), int(y)))
        if not point:
            return None
        para = point.Clone()
        para.ExpandToEnclosingUnit(PARAGRAPH)
        # Le paragraphe trouvé doit être sous le clic (sinon : image, marge… -> OCR).
        near = any(l - 40 <= x <= l + w + 40 and t - 25 <= y <= t + h + 25
                   for l, t, w, h in self._rect_list(para))
        if not near:
            log.info("UIA : aucun texte sous le clic")
            return None
        before = para.Clone()
        before.MoveEndpointByRange(END, point, START)
        offset = len(self._text(before).lstrip())
        ranges, texts = self._collect(para)
        if not texts:
            return None
        self.state = _State(ranges=ranges, para_range=list(range(len(ranges))))
        log.info("UIA : %d paragraphe(s) récupéré(s)", len(texts))
        return UiaDoc(texts, 0, offset)

    def _selected_text(self) -> str:
        pat = self._pattern_up(self.auto.GetFocusedControl())
        if pat is None:
            return ""
        try:
            sel = pat.GetSelection()
            if not sel or sel.Length == 0:
                return ""
            return self._text(sel.GetElement(0))[:MAX_CHARS]
        except Exception:
            log.debug("Sélection illisible", exc_info=True)
            return ""

    def _focused_text(self) -> str:
        ctrl = self.auto.GetFocusedControl()
        pat = self._pattern_up(ctrl)
        if pat is not None:
            try:
                return self._text(pat.DocumentRange)[:4000]
            except Exception:
                log.debug("Texte du document illisible", exc_info=True)
        try:
            vp = ctrl.GetValuePattern()
            return (vp.Value or "")[:4000]
        except Exception:
            return ""

    def _caret_rect(self):
        import os

        from .screenmap import win32_caret_native
        ctrl = self.auto.GetFocusedControl()
        if ctrl is None or ctrl.ProcessId == os.getpid():
            return None
        pat = self._pattern_up(ctrl)
        if pat is not None:
            try:
                sel = pat.GetSelection()
                if sel and sel.Length:
                    rng = sel.GetElement(0)
                    rects = self._rect_list(rng)
                    if not rects:
                        # curseur seul (plage vide) : on prend le caractère voisin
                        r2 = rng.Clone()
                        r2.MoveEndpointByUnit(END, CHARACTER, 1)
                        rects = self._rect_list(r2)
                        if not rects:
                            r2 = rng.Clone()
                            r2.MoveEndpointByUnit(START, CHARACTER, -1)
                            rects = self._rect_list(r2)
                    if rects:
                        # sélection longue : on garde ses premières lignes
                        rects = rects[:6]
                        log.info("Position : sélection ou curseur (UIA)")
                        left = min(r[0] for r in rects)
                        top = min(r[1] for r in rects)
                        right = max(r[0] + r[2] for r in rects)
                        bottom = max(r[1] + r[3] for r in rects)
                        return (left, top, right - left, bottom - top)
            except Exception:
                log.debug("Position du curseur illisible", exc_info=True)
        caret = win32_caret_native()
        if caret is not None:
            log.info("Position : curseur de texte Windows")
            return caret
        try:
            r = ctrl.BoundingRectangle
            if 0 < r.height() <= 120 and r.width() > 0:      # petite case de saisie
                log.info("Position : case de saisie")
                return (r.left, r.top, r.width(), r.height())
        except Exception:
            pass
        return None

    def _field_snapshot(self):
        import os
        ctrl = self.auto.GetFocusedControl()
        if ctrl is None or ctrl.ProcessId == os.getpid():
            return None
        try:
            key = (ctrl.ProcessId, tuple(ctrl.GetRuntimeId()))
        except Exception:
            key = (ctrl.ProcessId, ctrl.ControlTypeName, ctrl.AutomationId, ctrl.ClassName)
        text = None
        pat = self._pattern_up(ctrl)
        if pat is not None:
            try:
                text = self._text(pat.DocumentRange)[:FIELD_CHARS]
            except Exception:
                text = None
        if text is None:
            try:
                text = (ctrl.GetValuePattern().Value or "")[:FIELD_CHARS]
            except Exception:
                return None
        return key, text

    def _selection(self) -> UiaDoc | None:
        pat = self._pattern_up(self.auto.GetFocusedControl())
        if pat is None:
            return None
        sel = pat.GetSelection()
        if not sel or sel.Length == 0:
            return None
        rng = sel.GetElement(0)
        text = self._text(rng).strip()
        if len(text.split()) < MIN_SELECTION_WORDS:
            # Un ou deux mots : sélection presque toujours accidentelle. On lit le
            # paragraphe qui la contient (et les suivants), comme pour un clic.
            if not text:
                return None
            para = rng.Clone()
            para.ExpandToEnclosingUnit(PARAGRAPH)
            ranges, texts = self._collect(para)
            if not texts:
                return None
            self.state = _State(ranges=ranges, para_range=list(range(len(ranges))))
            log.info("UIA : sélection de %d mot(s), lecture du paragraphe qui la contient",
                     len(text.split()))
            return UiaDoc(texts, 0, 0)
        # La sélection est découpée en paragraphes (lignes vides ou retours à la ligne).
        parts = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
        self.state = _State(ranges=[rng], para_range=[0] * len(parts))
        log.info("UIA : sélection de %d caractère(s)", len(text))
        return UiaDoc(parts, 0, 0, selection=True)

    def _set_words(self, words: list[tuple[int, str]]) -> None:
        self.state.words = list(words)
        self.state.found = {}
        self.state.last_scrolled = -1

    def _find_word(self, i: int):
        """Plage COM du mot i, cherchée juste après le mot précédent du même bloc."""
        st = self.state
        if i in st.found:
            return st.found[i]
        if not (0 <= i < len(st.words)):
            return None
        g = st.group(i)
        if g >= len(st.ranges):
            return None
        # Premier mot du bloc, ou dernier mot déjà trouvé avant i dans ce bloc.
        k = i
        while k > 0 and st.group(k - 1) == g and (k - 1) not in st.found:
            k -= 1
        search = st.ranges[g].Clone()
        if k > 0 and st.group(k - 1) == g and (k - 1) in st.found:
            search.MoveEndpointByRange(START, st.found[k - 1], END)
        result = None
        while k <= i:
            hit = search.FindText(st.words[k][1], 0, 0)
            if not hit:
                break
            st.found[k] = hit
            search.MoveEndpointByRange(START, hit, END)
            if k == i:
                result = hit
            k += 1
        return result

    def _rects(self, word: int, para: int, autoscroll: bool):
        st = self.state
        rng = self._find_word(word)
        word_rects = self._rect_list(rng) if rng is not None else []
        if rng is not None and not word_rects and autoscroll and st.last_scrolled != word:
            st.last_scrolled = word        # le mot est hors de l'écran : on fait défiler
            try:
                rng.ScrollIntoView(1)
                word_rects = self._rect_list(rng)
            except Exception:
                pass
        para_rects = []
        if 0 <= para < len(st.para_range):
            para_rects = self._rect_list(st.ranges[st.para_range[para]])
        return word_rects, para_rects
