"""Fenêtres de la lecture : sélection de zone, surlignage par-dessus l'écran,
barre de contrôle et fenêtre de lecture pour le texte sélectionné."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QPainter, QPen,
                           QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QTextEdit, QToolButton, QVBoxLayout,
                               QWidget)

_TOPMOST = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool


# --------------------------------------------------------------------------- sélection
class SelectionOverlay(QWidget):
    """Assombrit l'écran sous la souris et laisse tracer un rectangle."""

    selected = Signal(QRect)       # rectangle tracé (coordonnées écran logiques)
    picked = Signal(QPoint)        # simple clic : paragraphe sous ce point
    cancelled = Signal()

    def __init__(self):
        super().__init__(None, _TOPMOST)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.origin: QPoint | None = None
        self.current: QPoint | None = None
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())

    def start(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _rect(self) -> QRect:
        if not self.origin or not self.current:
            return QRect()
        return QRect(self.origin, self.current).normalized()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        r = self._rect()
        if not r.isEmpty():
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.fillRect(r, QColor(255, 255, 255, 2))   # garde la souris sur la fenêtre
            p.setPen(QPen(QColor("#FFE600"), 2))
            p.drawRect(r.adjusted(0, 0, -1, -1))
        else:
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Segoe UI", 14, QFont.DemiBold))
            p.drawText(self.rect().adjusted(0, 60, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                       "Clique sur un paragraphe pour le lire, ou trace un rectangle"
                       "  —  Échap pour annuler")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.RightButton:
            self._cancel()
            return
        self.origin = self.current = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self.origin:
            self.current = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if not self.origin:
            return
        self.current = e.position().toPoint()
        r = self._rect()
        self.hide()
        if r.width() < 12 and r.height() < 12:
            self.picked.emit(self.current + self.geometry().topLeft())
        elif r.width() < 12 or r.height() < 8:
            self.cancelled.emit()
        else:
            self.selected.emit(r.translated(self.geometry().topLeft()))
        self.close()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self._cancel()

    def _cancel(self) -> None:
        self.hide()
        self.cancelled.emit()
        self.close()


# --------------------------------------------------------------------------- icônes
def _icon(kind: str, color: str = "#F0F0F0"):
    """Icônes dessinées (les symboles ⏸ ⏹ s'affichent en émojis colorés sous Windows)."""
    from PySide6.QtGui import QIcon, QPixmap, QPolygonF
    pix = QPixmap(48, 48)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    if kind == "pause":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(11, 8, 9, 32), 3, 3)
        p.drawRoundedRect(QRectF(28, 8, 9, 32), 3, 3)
    elif kind == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(13, 7), QPointF(40, 24), QPointF(13, 41)]))
    elif kind == "stop":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(10, 10, 28, 28), 4, 4)
    elif kind == "close":
        p.setPen(QPen(c, 7, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(8, 8), QPointF(40, 40))
        p.drawLine(QPointF(40, 8), QPointF(8, 40))
    p.end()
    return QIcon(pix)


# --------------------------------------------------------------------------- contrôles
class Controls(QWidget):
    """Barre de lecture, sur le modèle de la pastille de dictée : badge de langue à
    gauche, état (point bleu), pause/lecture, vitesse, croix rouge à droite.
    Flottante, elle se déplace en la faisant glisser."""

    playPause = Signal()
    stopRequested = Signal()          # conservé pour compatibilité (plus de bouton)
    speedDelta = Signal(float)
    langToggle = Signal()             # clic sur le badge : menu langue / traduction
    suggestionClicked = Signal()      # « Lire en français » (texte en langue étrangère)
    moved = Signal()
    closeRequested = Signal()

    STYLE = """
    QToolButton { color: #F0F0F0; background: transparent; border: none;
                  font: 600 13px 'Segoe UI'; padding: 3px 7px; border-radius: 12px; }
    QToolButton:hover { background: rgba(255,255,255,40); }
    QToolButton#badge { border: 1px solid rgba(255,255,255,90);
                        background: rgba(255,255,255,28); border-radius: 7px;
                        font: 700 9pt 'Segoe UI'; padding: 0; }
    QToolButton#badge:hover { background: rgba(255,255,255,60); }
    QToolButton#suggest { background: #4DA3FF; color: white; border-radius: 11px;
                          font: 600 9pt 'Segoe UI'; padding: 2px 9px; }
    QToolButton#suggest:hover { background: #2F8CF0; }
    QToolButton#close { background: rgba(255,255,255,35); border-radius: 12px; padding: 0; }
    QToolButton#close:hover { background: rgba(229,72,77,90); }
    QLabel { color: #F0F0F0; font: 600 10pt 'Segoe UI'; padding: 0 2px; }
    QLabel#dot { font-size: 13pt; padding: 0 2px 2px 2px; }
    """

    def __init__(self, floating: bool):
        if floating:
            super().__init__(None, _TOPMOST)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setCursor(Qt.OpenHandCursor)
        else:
            super().__init__()
        self.floating = floating
        self._drag = None
        self.setObjectName("controls")
        self.setStyleSheet(self.STYLE)
        self.setFixedHeight(40)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 0, 8, 0)
        lay.setSpacing(4)
        self.btn_lang = self._btn("FR", "Langue : changer de voix ou lire une traduction",
                                  self.langToggle.emit)
        self.btn_lang.setObjectName("badge")
        self.btn_lang.setFixedSize(36, 24)
        self.dot = QLabel("●")
        self.dot.setObjectName("dot")
        lay.addWidget(self.dot)
        self.btn_play = self._btn("", "Pause / reprendre", self.playPause.emit)
        self.btn_play.setIcon(_icon("pause"))
        lay.addSpacing(4)
        self._btn("−", "Moins vite", lambda: self.speedDelta.emit(-0.1))
        self.lbl_speed = QLabel("1.0×")
        lay.addWidget(self.lbl_speed)
        self._btn("+", "Plus vite", lambda: self.speedDelta.emit(0.1))
        self.lbl_status = QLabel("")
        lay.addWidget(self.lbl_status)
        self.btn_suggest = self._btn("", "Traduire ce texte et le lire",
                                     self.suggestionClicked.emit)
        self.btn_suggest.setObjectName("suggest")
        self.btn_suggest.hide()
        lay.addStretch(1)
        lay.addSpacing(6)
        close = self._btn("", "Tout fermer (Échap)", self.closeRequested.emit)
        close.setObjectName("close")
        close.setFixedSize(24, 24)
        close.setIcon(_icon("close", "#FF6B6E"))
        close.setIconSize(QSize(11, 11))
        self.set_state("playing")

    def paintEvent(self, e) -> None:
        if self.floating:     # même fond que la pastille de dictée
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(QColor(22, 22, 26, 235))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            p.end()
        super().paintEvent(e)

    def _btn(self, text: str, tip: str, slot) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setIconSize(QSize(13, 13))
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        self.layout().addWidget(b)
        return b

    # déplacement (barre flottante)
    def mousePressEvent(self, e) -> None:
        if self.floating and e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            self.moved.emit()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.OpenHandCursor)

    def set_state(self, state: str) -> None:
        playing = state == "playing"
        self.btn_play.setIcon(_icon("pause" if playing else "play"))
        self.dot.setStyleSheet(f"color: {'#4DA3FF' if playing else '#8E8E96'};")

    def set_speed(self, speed: float) -> None:
        self.lbl_speed.setText(f"{speed:.1f}×")

    def set_lang(self, lang: str, target: str | None = None) -> None:
        if target:
            self.btn_lang.setText(f"{lang.upper()}→{target.upper()}" if lang
                                  else f"→ {target.upper()}")
            self.btn_lang.setFixedSize(64, 24)
            self.btn_lang.setStyleSheet("QToolButton { background: #4DA3FF; color: white; "
                                        "border: none; border-radius: 7px; "
                                        "font: 700 9pt 'Segoe UI'; padding: 0; }")
        else:
            self.btn_lang.setText(lang.upper())
            self.btn_lang.setFixedSize(36 if len(lang) <= 2 else 48, 24)
            self.btn_lang.setStyleSheet("")
        if self.floating:
            self.adjustSize()

    def show_suggestion(self, text: str) -> None:
        self.btn_suggest.setText(text)
        self.btn_suggest.setVisible(bool(text))
        if self.floating:
            self.adjustSize()

    def set_status(self, text: str) -> None:
        self.lbl_status.setText(text)


# --------------------------------------------------------------------------- zone
class ReadingOverlay(QWidget):
    """Fenêtre transparente posée sur la zone lue : surligne le mot en cours,
    un clic sur un mot relance la lecture à cet endroit."""

    wordClicked = Signal(int)
    stopRequested = Signal()

    def __init__(self, region: QRect, word_rects: list[tuple[float, float, float, float]],
                 color: str, opacity: float):
        super().__init__(None, _TOPMOST)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(region)
        self.rects = [QRectF(x - region.x(), y - region.y(), w, h) for x, y, w, h in word_rects]
        c = QColor(color)
        c.setAlphaF(max(0.05, min(1.0, opacity)))
        self.fill = c
        self.current = -1
        self.setCursor(Qt.PointingHandCursor)

    def set_word(self, idx: int) -> None:
        if idx != self.current:
            self.current = idx
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # alpha minimal : la fenêtre reçoit les clics sans masquer le contenu
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        p.setPen(QPen(QColor(255, 230, 0, 150), 1, Qt.DashLine))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if 0 <= self.current < len(self.rects):
            r = self.rects[self.current].adjusted(-3, -2, 3, 2)
            p.setPen(Qt.NoPen)
            p.setBrush(self.fill)
            p.drawRoundedRect(r, 3, 3)

    def _hit(self, pt) -> int:
        best, best_d = -1, 1e9
        for i, r in enumerate(self.rects):
            if r.adjusted(-4, -3, 4, 3).contains(pt):
                return i
            if r.top() - 6 <= pt.y() <= r.bottom() + 6:
                d = min(abs(pt.x() - r.left()), abs(pt.x() - r.right()))
                if d < best_d:
                    best, best_d = i, d
        return best if best_d < 60 else -1

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            idx = self._hit(e.position())
            if idx >= 0:
                self.wordClicked.emit(idx)

    def wheelEvent(self, _e) -> None:
        self.stopRequested.emit()          # la page défile : la zone n'est plus valable

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.stopRequested.emit()


# --------------------------------------------------------------------------- fenêtre
class ReaderWindow(QWidget):
    """Fenêtre de lecture pour un texte sélectionné (textes longs)."""

    wordClicked = Signal(int)
    closed = Signal()

    def __init__(self, text: str, color: str, opacity: float, font_size: int):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Vocalis — Lecture")
        self.resize(760, 560)
        self.controls = Controls(floating=False)
        self.edit = QTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlainText(text)
        self.edit.setFont(QFont("Segoe UI", font_size))
        self.edit.viewport().setCursor(Qt.PointingHandCursor)
        self.edit.viewport().installEventFilter(self)
        self.edit.setStyleSheet("QTextEdit { background: #FBFAF6; color: #1E1E1E; "
                                "border: none; padding: 18px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QWidget()
        top.setStyleSheet("background: #16161A;")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(8, 6, 8, 6)
        tl.addWidget(self.controls)
        tl.addStretch(1)
        lay.addWidget(top)
        lay.addWidget(self.edit, 1)
        c = QColor(color)
        c.setAlphaF(max(0.15, min(1.0, opacity + 0.2)))
        self.fmt = QTextCharFormat()
        self.fmt.setBackground(c)
        self.spans: list[tuple[int, int]] = []
        self.current = -1

    def set_spans(self, spans: list[tuple[int, int]]) -> None:
        self.spans = spans

    def set_word(self, idx: int) -> None:
        if idx == self.current:
            return
        self.current = idx
        if not (0 <= idx < len(self.spans)):
            self.edit.setExtraSelections([])
            return
        a, b = self.spans[idx]
        cur = QTextCursor(self.edit.document())
        cur.setPosition(a)
        cur.setPosition(b, QTextCursor.KeepAnchor)
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cur
        sel.format = self.fmt
        self.edit.setExtraSelections([sel])
        view = QTextCursor(self.edit.document())
        view.setPosition(b)
        self.edit.setTextCursor(view)
        self.edit.ensureCursorVisible()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.edit.viewport() and event.type() == event.Type.MouseButtonRelease:
            if not self.edit.textCursor().hasSelection():
                pos = self.edit.cursorForPosition(event.position().toPoint()).position()
                for i, (a, b) in enumerate(self.spans):
                    if pos <= b:
                        self.wordClicked.emit(i)
                        break
        return super().eventFilter(obj, event)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e) -> None:
        self.closed.emit()
        super().closeEvent(e)


# --------------------------------------------------------------------------- suivi
class FollowOverlay(QWidget):
    """Calque transparent AUX CLICS posé sur tout l'écran : il dessine le cadre du
    paragraphe lu et surligne le mot en cours, à leur position actuelle (le calque
    est redessiné quand la page défile). Les clics passent à l'application."""

    def __init__(self, screen_geometry: QRect, color: str, opacity: float):
        super().__init__(None, _TOPMOST | Qt.WindowTransparentForInput
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(screen_geometry)
        c = QColor(color)
        c.setAlphaF(max(0.05, min(1.0, opacity)))
        self.fill = c
        f = QColor(color)
        f.setAlphaF(0.95)
        self.frame_color = f
        self.word_rects: list[QRectF] = []
        self.para_rects: list[QRectF] = []

    def set_rects(self, word_rects: list[QRectF], para_rects: list[QRectF]) -> None:
        o = self.geometry().topLeft()
        w = [r.translated(-o.x(), -o.y()) for r in word_rects]
        p = [r.translated(-o.x(), -o.y()) for r in para_rects]
        if w != self.word_rects or p != self.para_rects:
            self.word_rects, self.para_rects = w, p
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.para_rects:
            box = self.para_rects[0]
            for r in self.para_rects[1:]:
                box = box.united(r)
            p.setPen(QPen(self.frame_color, 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box.adjusted(-8, -6, 8, 6), 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(self.fill)
        for r in self.word_rects:
            p.drawRoundedRect(r.adjusted(-3, -2, 3, 2), 3, 3)


# --------------------------------------------------------------------------- traduction
class TranslationPanel(QWidget):
    """Petite fenêtre au-dessus de la barre de lecture : le texte traduit en cours,
    avec le mot lu surligné."""

    WIDTH = 500

    def __init__(self, color: str, opacity: float):
        super().__init__(None, _TOPMOST | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        from PySide6.QtGui import QTextDocument
        self.doc = QTextDocument()
        self.doc.setDocumentMargin(0)
        self.doc.setDefaultFont(QFont("Segoe UI", 11))
        self.doc.setTextWidth(self.WIDTH - 32)
        c = QColor(color)
        self.mark = f"rgba({c.red()},{c.green()},{c.blue()},{max(0.35, min(1.0, opacity + 0.25)):.2f})"
        self.words: list[str] = []
        self.current = -1
        self.target = ""
        self.anchor: QWidget | None = None

    def set_target(self, target: str) -> None:
        self.target = target.upper()

    def set_text(self, text: str) -> None:
        self.words = text.split()
        self.current = -1
        self._render()

    def set_word(self, idx: int) -> None:
        if idx != self.current:
            self.current = idx
            self._render()

    def _render(self) -> None:
        import html
        parts = []
        for i, w in enumerate(self.words):
            e = html.escape(w)
            if i == self.current:
                e = (f"<span style='background:{self.mark}; color:#16161A; "
                     f"border-radius:3px'>{e}</span>")
            parts.append(e)
        self.doc.setHtml("<div style='color:#F0F0F0; line-height:140%'>" + " ".join(parts)
                         + "</div>")
        h = int(min(self.doc.size().height(), 9 * 26)) + 44
        self.resize(self.WIDTH, h)
        self.place()
        self.update()

    def place(self) -> None:
        """Juste au-dessus de la barre de lecture (en dessous s'il n'y a pas la place)."""
        if self.anchor is None:
            return
        try:
            top_left = self.anchor.mapToGlobal(QPoint(0, 0))
        except RuntimeError:
            return
        screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = max(avail.left() + 8, min(top_left.x(), avail.right() - self.width() - 8))
        y = top_left.y() - self.height() - 8
        if y < avail.top() + 8:
            y = top_left.y() + self.anchor.height() + 8
        self.move(x, y)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(22, 22, 26, 238))
        p.drawRoundedRect(r, 14, 14)
        p.setPen(QColor(77, 163, 255))
        f = QFont("Segoe UI", 8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(16, 8, 300, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   f"TRADUCTION · {self.target}")
        p.translate(16, 30)
        self.doc.drawContents(p, QRectF(0, 0, self.WIDTH - 32, self.height() - 40))
