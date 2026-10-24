"""Petit cercle qui tourne (comme dans Windows) : « Vocalis travaille, patience »."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class Spinner(QWidget):
    def __init__(self, size: int = 14, color: str = "#4DA3FF", parent=None):
        super().__init__(parent)
        self._size = size
        self.color = QColor(color)
        self.setFixedSize(QSize(size + 2, size + 2))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self.hide()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(30)
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = time.monotonic() - self._t0
        r = QRectF(1.5, 1.5, self._size - 1, self._size - 1)
        p.setPen(QPen(QColor(self.color.red(), self.color.green(), self.color.blue(), 50),
                      2.2))
        p.drawEllipse(r)
        p.setPen(QPen(self.color, 2.2, Qt.SolidLine, Qt.RoundCap))
        # arc qui tourne et respire, comme l'anneau de chargement de Windows 11
        span = int((90 + 120 * (0.5 + 0.5 * math.sin(t * 3))) * 16)
        p.drawArc(r, int(-t * 400 * 16) % (360 * 16), span)


class Mark(QWidget):
    """Pastille de résultat, à la place du cercle qui tourne : ✓ vert (« c'est bon »)
    ou ! orange (« ça n'a pas marché »)."""

    COLORS = {"ok": "#2FA84F", "error": "#E8890C"}

    def __init__(self, size: int = 16, parent=None):
        super().__init__(parent)
        self._size = size
        self.kind = "ok"
        self.setFixedSize(QSize(size + 2, size + 2))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def show_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()
        self.show()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.COLORS.get(self.kind, "#2FA84F")))
        p.drawEllipse(QRectF(1, 1, s, s))
        p.setPen(QPen(QColor("white"), s / 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.kind == "ok":
            p.drawPolyline([QPointF(1 + s * 0.27, 1 + s * 0.52), QPointF(1 + s * 0.44, 1 + s * 0.69),
                            QPointF(1 + s * 0.74, 1 + s * 0.35)])
        else:
            p.drawLine(QPointF(1 + s / 2, 1 + s * 0.25), QPointF(1 + s / 2, 1 + s * 0.55))
            p.drawPoint(QPointF(1 + s / 2, 1 + s * 0.75))
