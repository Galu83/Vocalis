"""Fenêtre « Mise à jour disponible » (style du panneau de l'icône)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

STYLE = """
QLabel { color: #F0F0F0; font: 10pt 'Segoe UI'; background: transparent; }
QLabel#title { font: 600 14pt 'Segoe UI'; }
QLabel#sub { color: #A8A8B0; font: 9.5pt 'Segoe UI'; }
QLabel#notes { color: #D8D8DE; font: 9.5pt 'Segoe UI'; }
QPushButton#primary { color: white; background: #4DA3FF; border: none; border-radius: 8px;
                      padding: 8px 16px; font: 600 10pt 'Segoe UI'; }
QPushButton#primary:hover { background: #2F8CF0; }
QPushButton#later { color: #E6E6EA; background: rgba(255,255,255,16); border: none;
                    border-radius: 8px; padding: 8px 14px; font: 600 10pt 'Segoe UI'; }
QPushButton#later:hover { background: rgba(255,255,255,30); }
QProgressBar { border: none; border-radius: 4px; background: rgba(255,255,255,25);
               height: 8px; }
QProgressBar::chunk { background: #4DA3FF; border-radius: 4px; }
"""


class _Relay(QObject):
    progress = Signal(int, str)
    done = Signal(object)


class UpdateDialog(QWidget):
    """Propose la mise à jour ; si on accepte, l'installe avec une barre de
    progression puis appelle on_done(None) (ou on_done(erreur))."""

    def __init__(self, current: str, info: dict, installer, on_done):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(440)
        self.info = info
        self.installer = installer
        self.on_done = on_done
        self._relay = _Relay()
        self._relay.progress.connect(self._show_progress)
        self._relay.done.connect(self._finished)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)
        from .icon import make_icon
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(make_icon().pixmap(36, 36))
        head.addWidget(logo)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        t = QLabel("Mise à jour disponible")
        t.setObjectName("title")
        s = QLabel(f"Vocalis {info.get('version')} est prêt à être installé "
                   f"(tu as la version {current}).")
        s.setObjectName("sub")
        s.setWordWrap(True)
        texts.addWidget(t)
        texts.addWidget(s)
        head.addLayout(texts, 1)
        lay.addLayout(head)
        notes = (info.get("notes") or "").strip()
        if notes:
            n = QLabel("Nouveautés :\n" + notes)
            n.setObjectName("notes")
            n.setWordWrap(True)
            lay.addSpacing(4)
            lay.addWidget(n)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("sub")
        self.status.hide()
        lay.addSpacing(6)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_later = QPushButton("Plus tard")
        self.btn_later.setObjectName("later")
        self.btn_later.setCursor(Qt.PointingHandCursor)
        self.btn_later.clicked.connect(self.close)
        self.btn_go = QPushButton("Mettre à jour maintenant")
        self.btn_go.setObjectName("primary")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.clicked.connect(self._start)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_go)
        lay.addSpacing(6)
        lay.addLayout(row)

    def show_centered(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2,
                  screen.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def _start(self) -> None:
        self.btn_go.setEnabled(False)
        self.btn_later.hide()
        self.bar.show()
        self.status.show()
        self.adjustSize()

        def work():
            try:
                self.installer(self.info, lambda p, msg: self._relay.progress.emit(p, msg))
                self._relay.done.emit(None)
            except Exception as exc:
                self._relay.done.emit(exc)

        threading.Thread(target=work, daemon=True, name="mise-a-jour").start()

    def _show_progress(self, pct: int, msg: str) -> None:
        self.bar.setValue(pct)
        self.status.setText(msg)

    def _finished(self, error) -> None:
        if error is not None:
            self.status.setText(f"La mise à jour n'a pas abouti : {error}")
            self.btn_go.setText("Réessayer")
            self.btn_go.setEnabled(True)
            self.btn_later.setText("Fermer")
            self.btn_later.show()
            return
        self.close()
        self.on_done(None)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(24, 24, 29, 246))
        p.drawRoundedRect(r, 14, 14)
