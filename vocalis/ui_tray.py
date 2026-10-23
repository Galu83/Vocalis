"""Panneau de l'icône (près de l'horloge) : un seul panneau clair et moderne à la
place du menu classique. S'ouvre au clic (gauche ou droit) sur l'icône."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

ACCENT = "#4DA3FF"

STYLE = f"""
QWidget#panel {{ background: transparent; }}
QLabel {{ color: #F0F0F0; font: 10pt 'Segoe UI'; background: transparent; }}
QLabel#title {{ font: 600 13pt 'Segoe UI'; }}
QLabel#status {{ color: #A8A8B0; font: 9pt 'Segoe UI'; }}
QLabel#section {{ color: #8E8E96; font: 700 8pt 'Segoe UI'; padding-top: 4px; }}
QLabel#keycap {{ color: #F0F0F0; background: rgba(255,255,255,22);
                 border: 1px solid rgba(255,255,255,45); border-bottom-width: 2px;
                 border-radius: 5px; font: 600 8pt 'Segoe UI'; padding: 1px 6px; }}
QLabel#action {{ font: 600 10pt 'Segoe UI'; }}
QFrame#card {{ background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,22);
               border-radius: 10px; }}
QPushButton#seg {{ color: #D8D8DE; background: transparent; border: none;
                   border-radius: 7px; padding: 6px 10px; font: 600 9pt 'Segoe UI'; }}
QPushButton#seg:hover {{ background: rgba(255,255,255,18); }}
QPushButton#seg:checked {{ background: {ACCENT}; color: white; }}
QFrame#segbar {{ background: rgba(255,255,255,12); border-radius: 9px; }}
QPushButton#foot {{ color: #E6E6EA; background: rgba(255,255,255,14); border: none;
                    border-radius: 8px; padding: 7px 10px; font: 600 9pt 'Segoe UI'; }}
QPushButton#foot:hover {{ background: rgba(255,255,255,30); }}
QPushButton#primary {{ color: white; background: #E5484D; border: none; border-radius: 8px;
                       padding: 7px 12px; font: 600 9pt 'Segoe UI'; }}
QPushButton#primary:hover {{ background: #D33C41; }}
QPushButton#quit {{ color: #FF8A8D; background: transparent; border: none;
                    border-radius: 8px; padding: 7px 10px; font: 600 9pt 'Segoe UI'; }}
QPushButton#quit:hover {{ background: rgba(229,72,77,45); }}
"""


class Switch(QAbstractButton):
    """Interrupteur moderne (style Windows 11)."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 20)

    def sizeHint(self) -> QSize:
        return QSize(38, 20)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setPen(Qt.NoPen if on else QPen(QColor(255, 255, 255, 120), 1))
        p.setBrush(QColor(ACCENT) if on else QColor(255, 255, 255, 18))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 8
        x = r.right() - d - 4 if on else r.left() + 4
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255) if on else QColor(220, 220, 225))
        p.drawEllipse(QRectF(x, r.top() + 4, d, d))


def _segmented(options: list[tuple[str, str]], current: str,
               on_pick: Callable[[str], None]) -> QFrame:
    bar = QFrame()
    bar.setObjectName("segbar")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    group = QButtonGroup(bar)
    group.setExclusive(True)
    for key, label in options:
        b = QPushButton(label)
        b.setObjectName("seg")
        b.setCheckable(True)
        b.setChecked(key == current)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, k=key: on_pick(k))
        group.addButton(b)
        lay.addWidget(b, 1)
    bar._group = group          # garde le groupe en vie
    return bar


def _keycaps(text: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    for part in [t.strip() for t in text.split("+") if t.strip()]:
        k = QLabel(part)
        k.setObjectName("keycap")
        lay.addWidget(k)
    return w


class TrayPanel(QWidget):
    closed = Signal()

    def __init__(self, state: dict, actions: dict[str, Callable]):
        """state : title, status, ready, hotkeys [(nom, touches)], engine, engines
        [(clé, libellé)], click_stop.
        actions : engine(k), click_stop(b), settings(), log(),
        restart(), quit()."""
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("panel")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(360)
        self.actions = actions

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)

        # en-tête : nom + état
        head = QHBoxLayout()
        from .icon import make_icon
        logo = QLabel()
        logo.setPixmap(make_icon().pixmap(30, 30))
        head.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel(state.get("title", "Vocalis"))
        t.setObjectName("title")
        dot = "#3DDC84" if state.get("ready") else "#FFB020"
        s = QLabel(f"<span style='color:{dot}'>●</span>&nbsp; {state.get('status', '')}")
        s.setObjectName("status")
        titles.addWidget(t)
        titles.addWidget(s)
        head.addLayout(titles, 1)
        lay.addLayout(head)

        # raccourcis
        lay.addWidget(self._section("RACCOURCIS"))
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 8, 12, 8)
        cl.setSpacing(6)
        for name, keys in state.get("hotkeys", []):
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setObjectName("action")
            row.addWidget(lbl, 1)
            row.addWidget(_keycaps(keys))
            cl.addLayout(row)
        lay.addWidget(card)

        # moteur
        lay.addWidget(self._section("MOTEUR DE DICTÉE"))
        lay.addWidget(_segmented(state.get("engines", []), state.get("engine", ""),
                                 self._pick("engine")))
        # interrupteurs
        lay.addWidget(self._section("OPTIONS"))
        lay.addLayout(self._switch_row("Clic : arrêter la lecture\nDouble-clic : reprendre au mot",
                                       state.get("click_stop", True), "click_stop"))
        # pied
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,28);")
        lay.addSpacing(4)
        lay.addWidget(line)
        top = QHBoxLayout()
        top.setSpacing(6)
        for label, key, name in (("Paramètres", "settings", "primary"),
                                 ("Dictionnaire", "dictionary", "foot")):
            b = QPushButton(label)
            b.setObjectName(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._run(k))
            top.addWidget(b, 1)
        lay.addLayout(top)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        for label, key, name in (("Journal", "log", "foot"),
                                 ("Redémarrer", "restart", "foot")):
            b = QPushButton(label)
            b.setObjectName(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._run(k))
            foot.addWidget(b)
        foot.addStretch(1)
        q = QPushButton("Quitter")
        q.setObjectName("quit")
        q.setCursor(Qt.PointingHandCursor)
        q.clicked.connect(lambda: self._run("quit"))
        foot.addWidget(q)
        lay.addLayout(foot)

    # ------------------------------------------------------------------ éléments
    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("section")
        return lbl

    def _switch_row(self, text: str, value: bool, key: str) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        sw = Switch(value)
        sw.toggled.connect(lambda on: self.actions[key](on))
        row.addWidget(lbl, 1)
        row.addWidget(sw, 0, Qt.AlignVCenter)
        return row

    def _pick(self, key: str) -> Callable[[str], None]:
        return lambda value: self.actions[key](value)

    def _run(self, key: str) -> None:
        self.close()
        self.actions[key]()

    # ------------------------------------------------------------------ affichage
    def show_near(self, icon_rect) -> None:
        """Au-dessus de l'icône (comme les panneaux de Windows 11)."""
        self.adjustSize()
        center = icon_rect.center() if icon_rect is not None and icon_rect.isValid() else None
        screen = (QGuiApplication.screenAt(center) if center is not None else None) or \
            QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        cx = center.x() if center is not None else avail.right() - self.width() // 2 - 12
        x = max(avail.left() + 12, min(cx - self.width() // 2, avail.right() - self.width() - 12))
        if center is not None and center.y() < avail.top() + 60:      # barre des tâches en haut
            y = avail.top() + 12
        else:
            y = avail.bottom() - self.height() - 12
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(24, 24, 29, 246))
        p.drawRoundedRect(r, 14, 14)

    def closeEvent(self, e) -> None:
        self.closed.emit()
        super().closeEvent(e)

