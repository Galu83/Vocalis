"""Mise en page du texte reconnu par OCR : lignes -> paragraphes, paragraphe
sous un clic, et recalage des mots après un défilement."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .ocr import OcrWord


@dataclass
class Line:
    words: list[OcrWord]

    @property
    def left(self) -> float:
        return min(w.x for w in self.words)

    @property
    def right(self) -> float:
        return max(w.x + w.w for w in self.words)

    @property
    def top(self) -> float:
        return min(w.y for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.y + w.h for w in self.words)

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


def group_paragraphs(lines: list[list[OcrWord]]) -> list[list[list[OcrWord]]]:
    """Regroupe les lignes (dans l'ordre de lecture de l'OCR) en paragraphes :
    une nouvelle ligne commence un paragraphe si l'écart vertical est grand,
    si elle n'est pas alignée avec la précédente (autre colonne), ou si la
    précédente se terminait nettement avant le bord droit par un point."""
    paras: list[list[Line]] = []
    for raw in lines:
        if not raw:
            continue
        line = Line(raw)
        if paras:
            prev = paras[-1][-1]
            h = max(prev.height, line.height)
            gap = line.top - prev.bottom
            para_right = max(l.right for l in paras[-1])
            overlap = min(prev.right, line.right) - max(prev.left, line.left)
            new = (
                gap > 0.75 * h                       # interligne plus grand
                or gap < -0.5 * h                    # remonte : autre colonne
                or overlap < 0                       # pas sous la ligne précédente
                or abs(line.left - prev.left) > 4 * h and line.left > prev.left
                or (prev.right < para_right - 3 * h
                    and re.search(r"[.!?:…]$", prev.words[-1].text))
            )
            if not new:
                paras[-1].append(line)
                continue
        paras.append([line])
    return [[l.words for l in p] for p in paras]


def _box(para: list[list[OcrWord]]) -> tuple[float, float, float, float]:
    ws = [w for line in para for w in line]
    left = min(w.x for w in ws)
    top = min(w.y for w in ws)
    return left, top, max(w.x + w.w for w in ws) - left, max(w.y + w.h for w in ws) - top


def paragraph_at(paras, x: float, y: float) -> int:
    """Indice du paragraphe sous le point (ou le plus proche)."""
    best, best_d = 0, float("inf")
    for i, p in enumerate(paras):
        l, t, w, h = _box(p)
        dx = max(l - x, 0, x - (l + w))
        dy = max(t - y, 0, y - (t + h))
        d = (dx * dx + dy * dy) ** 0.5
        if d < best_d:
            best, best_d = i, d
    return best


def same_column(paras, p: int):
    """Le paragraphe p puis les suivants de la même colonne (même largeur de texte,
    plus bas sur l'écran) : on ne déborde pas sur une autre fenêtre ou colonne."""
    l0, t0, w0, _ = _box(paras[p])
    out = [paras[p]]
    for para in paras[p + 1:]:
        l, t, w, h = _box(para)
        overlap = min(l0 + w0, l + w) - max(l0, l)
        if t >= t0 and overlap > 0.5 * min(w0, w):
            out.append(para)
    return out


def word_at(words: list[OcrWord], x: float, y: float, tolerance: float = 6) -> int | None:
    for i, w in enumerate(words):
        if w.x - tolerance <= x <= w.x + w.w + tolerance and \
                w.y - tolerance <= y <= w.y + w.h + tolerance:
            return i
    return None


def norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text.lower())
    return re.sub(r"[^\w]", "", t)


def reanchor(doc_words: list[str], start: int, ocr_words: list[OcrWord],
             window: int = 40) -> dict[int, OcrWord]:
    """Après un défilement : retrouve, dans le nouvel OCR, les mots du document
    à partir de `start`. Renvoie {indice du mot : mot OCR trouvé}."""
    found: dict[int, OcrWord] = {}
    norms = [norm(w.text) for w in ocr_words]
    # Point d'accroche : 3 mots consécutifs du document trouvés à la suite.
    pos = None
    for i in range(start, min(start + 30, len(doc_words) - 2)):
        key = [norm(doc_words[i + k]) for k in range(3)]
        for j in range(len(norms) - 2):
            if norms[j:j + 3] == key and all(key):
                pos = (i, j)
                break
        if pos:
            break
    if pos is None:
        return found
    i, j = pos
    while i < len(doc_words) and j < len(norms):
        target = norm(doc_words[i])
        hit = next((k for k in range(j, min(j + window, len(norms)))
                    if norms[k] == target), None)
        if hit is None:
            i += 1
            continue
        found[i] = ocr_words[hit]
        i, j = i + 1, hit + 1
    return found
