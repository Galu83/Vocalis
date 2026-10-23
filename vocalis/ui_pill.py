"""Pastille flottante (sans voler le focus clavier).

Pendant une dictée, elle s'agrandit en petite fenêtre :
- en haut, la phrase en cours, mot par mot (blanc = sûr, gris = provisoire) ;
- en dessous, les phrases déjà validées.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable

import logging

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
import html

from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter,
                           QPainterPath, QPen, QTextDocument)
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

RED = QColor("#FF4D4F")
BLUE = QColor("#4DA3FF")
GREEN = QColor("#3DDC84")
GREY = QColor("#B0B3B8")
AMBER = QColor("#FFB020")

HEADER_H = 40
PANEL_W = 540
DONE_MAX_H = 190         # hauteur maximale des phrases validées (on voit les dernières)
PAD = 16
CHIPS_H = 34             # rangée des langues de traduction
SEL_MAX_H = 66           # texte sélectionné : 3 lignes au plus
TR_MAX_H = 110           # aperçu de la traduction
NEAR_ROOM = 330          # place à prévoir sous le texte pour l'encadré de dictée


class Pill(QWidget):
    closeClicked = Signal()
    moved = Signal(int, int)        # nouveau centre (x, y) après un glisser-déposer
    langClicked = Signal()          # clic sur le badge de langue
    targetClicked = Signal(str)     # clic sur une langue de traduction (fr, pt, en)

    def __init__(self, level_source: Callable[[], float]):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.level_source = level_source
        self.state = "hidden"
        self.text = ""
        self.color = RED
        self.closable = False
        self.levels: deque[float] = deque([0.0] * 9, maxlen=9)
        self.t0 = time.monotonic()
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide)
        self.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        self.setCursor(Qt.OpenHandCursor)
        self.anchor: QPoint | None = None       # centre choisi en la déplaçant (cette fois)
        self.near: QRectF | None = None         # là où l'on écrit : la pastille s'y colle
        self.near_above = False
        self._drag_offset: QPoint | None = None
        self._dragged = False
        self.lang = ""                  # badge de langue (vide = masqué)
        self.lang_auto = False          # vrai si l'outil a changé de langue tout seul
        self.target = ""                # langue de traduction choisie (vide = aucune)
        self.languages: tuple[str, ...] = ("fr", "pt", "en", "de")   # langues proposées
        self.session = False            # dictée en cours : l'encadré reste ouvert
        self.actions: list[tuple[str, str]] = []   # « Demander » : boutons tout prêts
        self.selection = ""             # texte sélectionné au début de la dictée
        self.translation = ""           # aperçu de la traduction
        self._doc_sel = QTextDocument()
        self._doc_tr = QTextDocument()
        self.live_sure = ""             # phrase en cours : partie sûre
        self.live_maybe = ""            # phrase en cours : partie provisoire
        self.live_done: list[str] = []  # phrases validées
        self._doc_cur = QTextDocument()
        self._doc_done = QTextDocument()
        for doc in (self._doc_cur, self._doc_done, self._doc_sel, self._doc_tr):
            doc.setDocumentMargin(0)
            doc.setDefaultFont(QFont("Segoe UI", 11))

    # ----------------------------------------------------------------- texte en direct
    @property
    def has_body(self) -> bool:
        return self.session or bool(self.live_sure or self.live_maybe or self.live_done)

    def begin_session(self) -> None:
        """Début d'une dictée : l'encadré s'ouvre (langues cliquables en bas)."""
        self.session = True
        self.target = ""
        self.selection = self.translation = ""
        self._layout()
        self.update()

    def end_session(self) -> None:
        self.session = False
        self.actions = []
        self.target = ""
        self.selection = self.translation = ""
        self.clear_live()
        self._layout()
        self.update()

    def set_selection(self, text: str) -> None:
        self.selection = text
        self._doc_sel.setHtml("<div style='color:#B9B9C2'>" + html.escape(text) + "</div>")
        self._refresh_layout()

    def set_translation(self, text: str) -> None:
        self.translation = text
        self._doc_tr.setHtml("<div style='color:#FFFFFF'>" + html.escape(text) + "</div>")
        self._refresh_layout()

    def _refresh_layout(self) -> None:
        if self.isVisible():
            self._layout()
            self.update()

    def set_live(self, sure: str, maybe: str, done: list[str]) -> None:
        if (sure, maybe, done) == (self.live_sure, self.live_maybe, self.live_done):
            return
        self.live_sure, self.live_maybe, self.live_done = sure, maybe, list(done)
        sp = " " if sure and maybe else ""
        self._doc_cur.setHtml(
            f"<span style='color:#FFFFFF'>{html.escape(sure)}</span>{sp}"
            f"<span style='color:#8E8E96'>{html.escape(maybe)}</span>")
        self._doc_done.setHtml("<div style='color:#E6E6EA'>" +
                               " ".join(html.escape(s) for s in done) + "</div>")
        if self.isVisible():
            self._layout()
            self.update()

    def clear_live(self) -> None:
        self.set_live("", "", [])

    def set_language(self, lang: str, auto: bool = False) -> None:
        self.lang = (lang or "").upper()
        self.lang_auto = auto
        self._layout()
        self.update()

    def set_target(self, target: str) -> None:
        self.target = (target or "").lower()
        if not self.target:
            self.translation = ""
        self._refresh_layout()

    def _badge_rect(self) -> QRectF:
        return QRectF(7, 8, 36, 24)

    @staticmethod
    def _chip_font() -> QFont:
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        return f

    def _chip_rects(self) -> list[tuple[str, QRectF]]:
        """Langues cliquables en bas de l'encadré (ou boutons de « Demander »)."""
        if self.actions:
            fm = QFontMetrics(self._chip_font())
            widths = [fm.horizontalAdvance(label) + 20 for _, label in self.actions]
            y = self.height() - CHIPS_H + 2
            x = self.width() - PAD - sum(widths) - 6 * (len(widths) - 1) + 6
            out = []
            for (key, _), w in zip(self.actions, widths):
                out.append((key, QRectF(x, y, w, 22)))
                x += w + 6
            return out
        codes = self.languages
        y = self.height() - CHIPS_H + 2
        x = self.width() - PAD - len(codes) * 46 + 6
        return [(code, QRectF(x + i * 46, y, 40, 22)) for i, code in enumerate(codes)]

    @property
    def _offset(self) -> int:
        return 40 if self.lang else 0

    def set_position(self, x: int, y: int) -> None:
        self.anchor = QPoint(x, y) if x >= 0 and y >= 0 else None

    def place_near(self, rect: QRectF | None) -> None:
        """À chaque dictée : juste sous l'endroit où l'on écrit (au-dessus s'il n'y a
        pas la place en bas). Un déplacement à la main vaut pour cette fois seulement."""
        self.anchor = None
        self.near = rect
        if rect is not None:
            screen = QGuiApplication.screenAt(rect.center().toPoint()) or \
                QGuiApplication.primaryScreen()
            self.near_above = rect.bottom() + 14 + NEAR_ROOM > screen.availableGeometry().bottom()

    # ----------------------------------------------------------------- API
    def show_state(self, state: str, text: str = "", closable: bool = False,
                   autohide_ms: int = 0) -> None:
        if state in ("info", "error") and text:
            log.info("Pastille : %s", text)
        self.state = state
        self.text = text
        self.closable = closable
        self.color = {"listening": RED, "finishing": GREY, "cleaning": BLUE,
                      "done": GREEN, "error": RED, "info": GREY}.get(state, GREY)
        self._hide_timer.stop()
        self._layout()
        if not self.isVisible():
            self.show()
        self.raise_()
        if not self._anim.isActive():
            self._anim.start(33)
        if autohide_ms:
            self._hide_timer.start(autohide_ms)
        self.update()

    def _hide(self) -> None:
        self.state = "hidden"
        self._anim.stop()
        self.hide()

    def _extra_heights(self, width: int) -> tuple[float, float]:
        inner = width - 2 * PAD
        sel_h = tr_h = 0.0
        if self.selection:
            self._doc_sel.setTextWidth(inner)
            sel_h = min(self._doc_sel.size().height(), SEL_MAX_H)
        if self.translation:
            self._doc_tr.setTextWidth(inner)
            tr_h = min(self._doc_tr.size().height(), TR_MAX_H)
        return sel_h, tr_h

    def _body_heights(self, width: int) -> tuple[float, float]:
        inner = width - 2 * PAD
        cur_h = done_h = 0.0
        if self.live_sure or self.live_maybe:
            self._doc_cur.setTextWidth(inner)
            cur_h = min(self._doc_cur.size().height(), 3 * 24)
        if self.live_done:
            self._doc_done.setTextWidth(inner)
            done_h = min(self._doc_done.size().height(), DONE_MAX_H)
        return cur_h, done_h

    def _layout(self) -> None:
        fm = self.fontMetrics()
        width = 70 + fm.horizontalAdvance(self.text) + (30 if self.closable else 0)
        if self.state == "listening":
            width += 60
        width = max(width, 120) + self._offset
        height = HEADER_H
        if self.has_body:
            width = max(width, PANEL_W)
            cur_h, done_h = self._body_heights(width)
            sel_h, tr_h = self._extra_heights(width)
            height += 6 + (cur_h or 22) + (28 + done_h if done_h else 0) + 14
            if sel_h:
                height += 20 + sel_h + 10
            if tr_h:
                height += 28 + tr_h
            if self.session:
                height += CHIPS_H
        if self.anchor is None and self.near is not None:
            screen = QGuiApplication.screenAt(self.near.center().toPoint()) or \
                QGuiApplication.primaryScreen()
            geo = screen.availableGeometry()
            cx = self.near.center().x() if self.near.width() < width else \
                self.near.left() + width / 2
            if self.near_above:            # le bas reste collé au-dessus du texte
                y = self.near.top() - 14 - height
            else:                          # le haut reste collé sous le texte
                y = self.near.bottom() + 14
            x = max(geo.left(), min(int(cx - width / 2), geo.right() - int(width)))
            y = max(geo.top(), min(int(y), geo.bottom() - int(height)))
            self.setGeometry(x, int(y), int(width), int(height))
            return
        # L'ancre est le centre de la barre du haut : la fenêtre grandit vers le bas.
        if self.anchor is not None and QGuiApplication.screenAt(self.anchor) is not None:
            geo = QGuiApplication.screenAt(self.anchor).availableGeometry()
            cx, cy = self.anchor.x(), self.anchor.y()
        else:
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry()
            cx, cy = geo.center().x(), geo.bottom() - HEADER_H / 2 - 70
        x = max(geo.left(), min(int(cx - width / 2), geo.right() - int(width)))
        y = max(geo.top(), min(int(cy - HEADER_H / 2), geo.bottom() - int(height)))
        self.setGeometry(x, y, int(width), int(height))

    def _tick(self) -> None:
        if self.state == "listening":
            self.levels.append(float(self.level_source() or 0.0))
        self.update()

    def _close_rect(self) -> QRectF:
        return QRectF(self.width() - 34, 8, 24, 24)

    # ----------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        radius = 16 if self.has_body else r.height() / 2
        path.addRoundedRect(r, radius, radius)
        p.fillPath(path, QColor(22, 22, 26, 235))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawPath(path)

        t = time.monotonic() - self.t0
        cx, cy = 22 + self._offset, HEADER_H / 2
        if self.lang:
            br = self._badge_rect()
            if self.lang_auto:      # changement automatique : badge orange
                p.setPen(Qt.NoPen)
                p.setBrush(AMBER)
                text_color = QColor(22, 22, 26)
            else:
                p.setPen(QPen(QColor(255, 255, 255, 90), 1))
                p.setBrush(QColor(255, 255, 255, 28))
                text_color = QColor(240, 240, 240)
            p.drawRoundedRect(br, 7, 7)
            p.setPen(text_color)
            f = p.font()
            f.setPointSizeF(9)
            f.setBold(True)
            p.setFont(f)
            p.drawText(br, Qt.AlignCenter, self.lang)
            p.setFont(self.font())
        if self.state in ("finishing", "cleaning"):
            # petit indicateur qui tourne
            p.setPen(QPen(self.color, 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(cx - 7, cy - 7, 14, 14), int(-t * 360 * 16) % (360 * 16), 270 * 16)
        else:
            pulse = 1.0 + 0.25 * math.sin(t * 6) if self.state == "listening" else 1.0
            rad = 5.5 * pulse
            p.setPen(Qt.NoPen)
            p.setBrush(self.color)
            p.drawEllipse(QPointF(cx, cy), rad, rad)

        x = 38 + self._offset
        if self.state == "listening":
            p.setBrush(QColor(255, 255, 255, 220))
            for i, lvl in enumerate(self.levels):
                h = 4 + 20 * min(1.0, lvl)
                p.drawRoundedRect(QRectF(x + i * 6, cy - h / 2, 3.5, h), 1.5, 1.5)
            x += 9 * 6 + 8

        p.setPen(QColor(240, 240, 240))
        p.drawText(QRectF(x, 0, self.width() - x - (36 if self.closable else 12), HEADER_H),
                   Qt.AlignVCenter | Qt.AlignLeft, self.text)

        if self.has_body:
            self._paint_body(p)

        if self.closable:
            cr = self._close_rect()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 35))
            p.drawEllipse(cr)
            p.setPen(QPen(QColor("#FF6B6E"), 2, Qt.SolidLine, Qt.RoundCap))
            c = cr.center()
            p.drawLine(QPointF(c.x() - 5, c.y() - 5), QPointF(c.x() + 5, c.y() + 5))
            p.drawLine(QPointF(c.x() + 5, c.y() - 5), QPointF(c.x() - 5, c.y() + 5))

    def _label(self, p: QPainter, y: float, text: str, color: QColor) -> None:
        p.setPen(color)
        f = QFont("Segoe UI", 8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(PAD, y, 300, 14), Qt.AlignLeft | Qt.AlignVCenter, text)

    def _paint_body(self, p: QPainter) -> None:
        cur_h, done_h = self._body_heights(self.width())
        sel_h, tr_h = self._extra_heights(self.width())
        y = HEADER_H + 6
        if sel_h:
            # texte sélectionné (la dictée s'y ajoute)
            self._label(p, y, "QUESTION SUR CE TEXTE" if self.actions else "TEXTE SÉLECTIONNÉ",
                        QColor(142, 142, 150))
            y += 18
            p.save()
            p.setClipRect(QRectF(PAD, y, self.width() - 2 * PAD, sel_h))
            p.translate(PAD, y)
            self._doc_sel.drawContents(p)
            p.restore()
            y += sel_h + 10
        p.save()
        # phrase en cours (on montre la fin si elle dépasse 3 lignes)
        if cur_h:
            full = self._doc_cur.size().height()
            p.setClipRect(QRectF(PAD, y, self.width() - 2 * PAD, cur_h))
            p.translate(PAD, y - (full - cur_h))
            self._doc_cur.drawContents(p)
            p.restore()
            p.save()
        else:
            p.setPen(QColor(142, 142, 150))
            f = QFont("Segoe UI", 11)
            f.setItalic(True)
            p.setFont(f)
            p.drawText(QRectF(PAD, y, self.width() - 2 * PAD, 22),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       "Pose ta question à voix haute…" if self.actions else
                       "Parle pour le remplacer, ou choisis une langue pour le traduire…"
                       if self.selection else "Parle, le texte s'affiche ici…")
        y += (cur_h or 22)
        # phrases validées (on montre les dernières)
        if done_h:
            y += 8
            p.setPen(QPen(QColor(255, 255, 255, 45), 1))
            p.drawLine(QPointF(PAD, y), QPointF(self.width() - PAD, y))
            p.setPen(QColor(61, 220, 132))
            f = QFont("Segoe UI", 8)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(PAD, y + 4, 200, 12), Qt.AlignLeft | Qt.AlignVCenter,
                       "✔ PHRASES VALIDÉES")
            y += 20
            full = self._doc_done.size().height()
            p.setClipRect(QRectF(PAD, y, self.width() - 2 * PAD, done_h))
            p.translate(PAD, y - (full - done_h))
            self._doc_done.drawContents(p)
            y += done_h
        p.restore()
        y += 14
        if tr_h:
            p.setPen(QPen(QColor(255, 255, 255, 45), 1))
            p.drawLine(QPointF(PAD, y - 6), QPointF(self.width() - PAD, y - 6))
            self._label(p, y, f"TRADUCTION · {self.target.upper()}", BLUE)
            y += 18
            p.save()
            p.setClipRect(QRectF(PAD, y, self.width() - 2 * PAD, tr_h))
            p.translate(PAD, y)
            self._doc_tr.drawContents(p)
            p.restore()
        if self.session:
            self._paint_chips(p)

    def _paint_chips(self, p: QPainter) -> None:
        chips = self._chip_rects()
        f = QFont("Segoe UI", 8)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(142, 142, 150))
        left = chips[0][1].left()
        if self.actions:
            if left - PAD > 150:
                p.drawText(QRectF(PAD, chips[0][1].top(), left - PAD - 8, 22),
                           Qt.AlignLeft | Qt.AlignVCenter, "ou en un clic :")
        else:
            if left - PAD - 78 > 190:          # assez de place pour les explications
                p.drawText(QRectF(PAD, chips[0][1].top(), left - PAD - 8, 22),
                           Qt.AlignLeft | Qt.AlignVCenter,
                           "Touche = valider  ·  ✕ = annuler")
            if left - PAD > 80:
                p.drawText(QRectF(left - 70, chips[0][1].top(), 64, 22),
                           Qt.AlignRight | Qt.AlignVCenter, "TRADUIRE")
        p.setFont(self._chip_font())
        labels = dict(self.actions)
        for code, r in chips:
            on = code == self.target and not self.actions
            p.setPen(Qt.NoPen if on else QPen(QColor(255, 255, 255, 70), 1))
            p.setBrush(BLUE if on else QColor(255, 255, 255, 18))
            p.drawRoundedRect(r, 7, 7)
            p.setPen(QColor(255, 255, 255) if on else QColor(220, 220, 225))
            p.drawText(r, Qt.AlignCenter, labels.get(code, code.upper()))

    # ----------------------------------------------------------------- souris
    def mousePressEvent(self, event) -> None:
        if self.lang and self._badge_rect().adjusted(-3, -4, 1, 4).contains(event.position()):
            self.langClicked.emit()
            return
        if self.session:
            for code, r in self._chip_rects():
                if r.adjusted(-3, -4, 3, 4).contains(event.position()):
                    self.targetClicked.emit(code)
                    return
        if self.closable and self._close_rect().adjusted(-4, -4, 4, 4).contains(event.position()):
            self.closeClicked.emit()
            return
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._dragged = False
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._dragged = True
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.OpenHandCursor)
            if self._dragged:
                g = self.geometry()
                c = QPoint(g.center().x(), g.top() + HEADER_H // 2)   # centre de la barre
                self.anchor = c
                self.moved.emit(c.x(), c.y())
