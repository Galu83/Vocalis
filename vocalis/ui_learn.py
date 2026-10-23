"""Petite question après une correction à la main : « Retenir ? » (Oui / Non)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

STYLE = """
QLabel { color: #F0F0F0; font: 10pt 'Segoe UI'; background: transparent; }
QLabel#title { font: 600 10pt 'Segoe UI'; color: #FFFFFF; }
QPushButton { color: #E6E6EA; background: rgba(255,255,255,16); border: none;
              border-radius: 7px; padding: 6px 14px; font: 600 9.5pt 'Segoe UI'; }
QPushButton:hover { background: rgba(255,255,255,32); }
QPushButton#primary { background: #4DA3FF; color: white; }
QPushButton#primary:hover { background: #2F8CF0; }
"""


class LearnPrompt(QWidget):
    answered = Signal(str, str, bool)          # ancien, nouveau, oui ?

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(380)
        self.queue: list[tuple[str, str]] = []
        self.current: tuple[str, str] | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 14, 12)
        lay.setSpacing(8)
        t = QLabel("Retenir dans le dictionnaire ?")
        t.setObjectName("title")
        lay.addWidget(t)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton("Non")
        yes = QPushButton("Oui, retenir")
        yes.setObjectName("primary")
        for b in (no, yes):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        no.clicked.connect(lambda: self._answer(False))
        yes.clicked.connect(lambda: self._answer(True))
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._answer(False, silent=True))

    def ask(self, old: str, new: str, near: QRectF | None) -> None:
        if (old, new) == self.current or (old, new) in self.queue:
            return
        self.queue.append((old, new))
        self.near = near
        if self.current is None:
            self._next()

    def _next(self) -> None:
        if not self.queue:
            self.current = None
            self.hide()
            return
        self.current = self.queue.pop(0)
        old, new = self.current
        self.lbl.setText(f"Tu as remplacé « {old} » par « {new} ».\n"
                         f"La prochaine fois, Vocalis écrira directement « {new} ».")
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(20000)

    def _place(self) -> None:
        near = self.near
        screen = (QGuiApplication.screenAt(near.center().toPoint()) if near is not None
                  else None) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        if near is not None:
            x, y = near.left(), near.bottom() + 14
            if y + self.height() > geo.bottom():
                y = near.top() - 14 - self.height()
        else:
            x, y = geo.right() - self.width() - 16, geo.bottom() - self.height() - 16
        x = max(geo.left() + 4, min(int(x), geo.right() - self.width() - 4))
        y = max(geo.top() + 4, min(int(y), geo.bottom() - self.height() - 4))
        self.move(x, y)

    def _answer(self, yes: bool, silent: bool = False) -> None:
        self._timer.stop()
        if self.current is not None and not silent:
            self.answered.emit(self.current[0], self.current[1], yes)
        self._next()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)
        p.fillPath(path, QColor(28, 28, 32, 246))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)
