"""Suivi du défilement vertical d'une zone d'écran (pour le texte lu par OCR).

On compare le « profil » de l'image (luminosité moyenne de chaque ligne de
pixels) avec celui de l'image de référence : le décalage qui les superpose le
mieux est le défilement. Rapide (quelques millisecondes) et fiable pour un
défilement vertical simple.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage


def image_to_gray(img: QImage) -> np.ndarray:
    g = img.convertToFormat(QImage.Format_Grayscale8)
    w, h, bpl = g.width(), g.height(), g.bytesPerLine()
    arr = np.frombuffer(g.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
    return arr[:, :w].astype(np.float32)


def profile(gray: np.ndarray) -> np.ndarray:
    """Profil vertical : moyenne et variation de chaque ligne (texte = variation)."""
    sub = gray[:, ::3]
    return np.abs(np.diff(sub, axis=1)).mean(axis=1) + 0.25 * sub.mean(axis=1) / 8


def estimate_shift(ref: np.ndarray, cur: np.ndarray,
                   min_overlap: float = 0.35) -> tuple[int, float]:
    """Décalage (en lignes de pixels) de cur par rapport à ref, et un score de
    confiance entre 0 et 1. Positif = le contenu est remonté (défilement vers le bas)."""
    n = min(len(ref), len(cur))
    if n < 20:
        return 0, 0.0
    ref, cur = ref[:n], cur[:n]
    best_dy, best_score = 0, -1.0
    lim = int(n * (1 - min_overlap))
    # Recherche grossière (pas de 4), puis fine autour du meilleur.
    for step, center, radius in ((4, 0, lim), (1, None, 6)):
        c = best_dy if center is None else center
        for dy in range(max(-lim, c - radius), min(lim, c + radius) + 1, step):
            if dy >= 0:
                a, b = ref[dy:], cur[:n - dy]
            else:
                a, b = ref[:n + dy], cur[-dy:]
            a = a - a.mean()
            b = b - b.mean()
            den = float(np.sqrt((a * a).sum() * (b * b).sum()))
            if den < 1e-6:
                continue
            score = float((a * b).sum() / den)
            if score > best_score:
                best_dy, best_score = dy, score
    return best_dy, max(0.0, best_score)
