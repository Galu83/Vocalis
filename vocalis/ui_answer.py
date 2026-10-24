"""Carte de réponse de « Demander » : la réponse s'écrit au fur et à mesure et la
voix la lit dès la première phrase prête, avec le mot lu surligné."""
from __future__ import annotations

import logging
import re
import threading

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QGuiApplication, QPainter, QPainterPath,
                           QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
                               QWidget)

from . import assistant, winutil
from .reading import Player, from_text

log = logging.getLogger(__name__)

STYLE = """
QLabel { color: #F0F0F0; font: 10pt 'Segoe UI'; background: transparent; }
QLabel#head { font: 600 10pt 'Segoe UI'; color: #FFFFFF; }
QLabel#question { color: #A8A8B0; font: italic 10pt 'Segoe UI'; }
QLabel#status { color: #8E8E96; font: 9pt 'Segoe UI'; }
QTextEdit { background: transparent; border: none; color: #FFFFFF;
            font: 11.5pt 'Segoe UI'; selection-background-color: #2F6FB8; }
QPushButton { color: #E6E6EA; background: rgba(255,255,255,16); border: none;
              border-radius: 7px; padding: 6px 12px; font: 600 9.5pt 'Segoe UI'; }
QPushButton:hover { background: rgba(255,255,255,32); }
QPushButton:disabled { color: #6E6E73; }
QPushButton#primary { background: #4DA3FF; color: white; }
QPushButton#primary:hover { background: #2F8CF0; }
QPushButton#close { background: transparent; color: #B0B3B8; font: 12pt 'Segoe UI';
                    padding: 0 6px; }
QPushButton#close:hover { color: white; }
"""
CARD_ROOM = 260          # hauteur à prévoir au-dessus de la sélection
BOUNDARY = re.compile(r"[.!?…:;](?=\s)|\n")


class AnswerCard(QWidget):
    closeClicked = Signal()
    playClicked = Signal()
    copyClicked = Signal()
    insertClicked = Signal()
    installClicked = Signal()
    replaceClicked = Signal()
    keepClicked = Signal()

    def __init__(self, hotkey_label: str = ""):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(520)
        self._drag: QPoint | None = None
        self.placed = False
        self.near = None             # sélection (ou curseur) : la carte s'y colle
        self.near_above = True
        self.dragged = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 12, 14)
        lay.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("✦  Demander")
        title.setObjectName("head")
        head.addWidget(title)
        head.addStretch(1)
        from .ui_spinner import Spinner
        self.spinner = Spinner(12)
        head.addWidget(self.spinner)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("status")
        head.addWidget(self.lbl_status)
        close = QPushButton("✕")
        close.setObjectName("close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.closeClicked.emit)
        head.addWidget(close)
        lay.addLayout(head)

        self.lbl_question = QLabel("")
        self.lbl_question.setObjectName("question")
        self.lbl_question.setWordWrap(True)
        lay.addWidget(self.lbl_question)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setFrameStyle(0)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text.document().setDocumentMargin(2)
        lay.addWidget(self.text)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.btn_replace = QPushButton("Remplacer")
        self.btn_replace.setObjectName("primary")
        self.btn_play = QPushButton("⏸ Pause")
        self.btn_copy = QPushButton("Copier")
        self.btn_insert = QPushButton("Insérer")
        self.btn_keep = QPushButton("☆ Garder comme action")
        self.btn_install = QPushButton("Installer l'assistant")
        self.btn_install.setObjectName("primary")
        for b, sig in ((self.btn_replace, self.replaceClicked),
                       (self.btn_play, self.playClicked), (self.btn_copy, self.copyClicked),
                       (self.btn_insert, self.insertClicked), (self.btn_keep, self.keepClicked),
                       (self.btn_install, self.installClicked)):
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(sig.emit)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.lbl_hint = QLabel(f"{hotkey_label} : question de suite" if hotkey_label else "")
        self.lbl_hint.setObjectName("status")
        lay.addWidget(self.lbl_hint)
        self.set_mode("answer")

    # ------------------------------------------------------------------ contenu
    def set_mode(self, mode: str, can_replace: bool = False, speak: bool = True) -> None:
        """answer : réponse lue ; transform : texte réécrit (Remplacer) ;
        install : l'assistant manque."""
        self.mode = mode
        shown = mode in ("answer", "transform")
        self.btn_replace.setVisible(mode == "transform" and can_replace)
        self.btn_play.setVisible(shown and speak)
        self.btn_copy.setVisible(shown)
        self.btn_insert.setVisible(shown and not (mode == "transform" and can_replace))
        self.btn_keep.setVisible(False)
        self.btn_install.setVisible(mode == "install")

    def show_keep(self, show: bool) -> None:
        self.btn_keep.setVisible(show)
        self.adjustSize()

    def start(self, question: str, mode: str = "answer", can_replace: bool = False,
              speak: bool = True) -> None:
        self.set_mode(mode, can_replace, speak)
        self.lbl_question.setText(question)
        self.lbl_question.setVisible(bool(question))
        self.text.clear()
        self.set_status("Réécriture…" if mode == "transform" else "Réflexion…")
        self.set_playing(speak)
        self._fit()
        self.show_near()

    def set_text(self, text: str) -> None:
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.text.setPlainText(text)
        self._fit()
        if at_end:
            bar.setValue(bar.maximum())

    def set_status(self, text: str) -> None:
        self.lbl_status.setText(text)
        # « Réflexion… », « Réécriture… », « Nouvel essai… » : le cercle tourne.
        if text.endswith("…"):
            self.spinner.start()
        else:
            self.spinner.stop()

    def set_playing(self, playing: bool) -> None:
        self.btn_play.setText("⏸ Pause" if playing else "▶ Lire")

    def highlight(self, start: int, end: int) -> None:
        if start < 0:
            self.text.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        cur = QTextCursor(self.text.document())
        n = self.text.document().characterCount() - 1
        cur.setPosition(min(start, n))
        cur.setPosition(min(end, n), QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 230, 0, 110))
        sel.cursor, sel.format = cur, fmt
        self.text.setExtraSelections([sel])
        plain = QTextCursor(cur)
        plain.clearSelection()
        self.text.setTextCursor(plain)
        self.text.ensureCursorVisible()

    def show_message(self, text: str, install: bool = False) -> None:
        self.text.setPlainText(text)
        self.set_mode("install" if install else "answer")
        self.set_status("")
        self._fit()
        self.show_near()

    def _fit(self) -> None:
        doc = self.text.document()
        doc.setTextWidth(self.text.viewport().width() or 480)
        h = int(min(max(doc.size().height() + 8, 60), 340))
        self.text.setFixedHeight(h)
        self.adjustSize()
        if self.isVisible():
            self._reposition()          # au-dessus du texte : le bas reste en place

    def place_near(self, rect) -> None:
        """Juste au-dessus du texte sélectionné ; en dessous s'il n'y a pas la place."""
        self.near = rect
        self.dragged = False
        self.placed = False
        if rect is not None:
            screen = QGuiApplication.screenAt(rect.center().toPoint()) or \
                QGuiApplication.primaryScreen()
            self.near_above = rect.top() - 12 - CARD_ROOM >= screen.availableGeometry().top()

    def _reposition(self) -> None:
        if self.dragged:
            return
        self.adjustSize()
        rect = self.near
        if rect is None:
            from .screenmap import mouse_rect
            rect = self.near = mouse_rect()
        screen = QGuiApplication.screenAt(rect.center().toPoint()) or \
            QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = rect.left() if rect.width() > w else rect.center().x() - w / 2
        y = rect.top() - 12 - h if self.near_above else rect.bottom() + 12
        x = max(geo.left() + 4, min(int(x), geo.right() - w - 4))
        y = max(geo.top() + 4, min(int(y), geo.bottom() - h - 4))
        self.move(int(x), int(y))

    def show_near(self) -> None:
        if not self.placed or not self.dragged:
            self._reposition()
            self.placed = True
        if not self.isVisible():
            self.show()
        self.raise_()

    # ------------------------------------------------------------------ dessin, déplacement
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, 16)
        p.fillPath(path, QColor(28, 28, 32, 246))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self.dragged = True
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None


class AskController(QObject):
    """Une conversation « Demander » : question -> réponse écrite et lue."""
    _piece = Signal(int, str)
    _done = Signal(int, str)
    _failed = Signal(int, str)

    def __init__(self, cfg: dict, tts, speed_getter, hotkey_label: str = "",
                 install_cb=None, media=None):
        super().__init__()
        self.media = media
        self.cfg = cfg
        self.tts = tts
        self.speed_getter = speed_getter
        self.install_cb = install_cb
        self.card = AnswerCard(hotkey_label)
        self.card.closeClicked.connect(self.close)
        self.card.playClicked.connect(self._toggle_play)
        self.card.copyClicked.connect(self._copy)
        self.card.insertClicked.connect(self._insert)
        self.card.installClicked.connect(self._install)
        self.card.replaceClicked.connect(self._replace)
        self.card.keepClicked.connect(self._keep)
        self.action: dict | None = None
        self.question = ""
        self.speak = True
        self.can_replace = False
        self._piece.connect(self._on_piece)
        self._done.connect(self._on_done)
        self._failed.connect(self._on_failed)
        self.conv: assistant.Conversation | None = None
        self._gen = 0
        self._reset_answer()

    # ------------------------------------------------------------------ état
    @property
    def open(self) -> bool:
        return self.card.isVisible() and self.conv is not None and bool(self.conv.messages)

    def _reset_answer(self) -> None:
        self.raw = ""
        self.text = ""
        self.final = False
        self.spoken_upto = 0
        self.speak_lang = ""
        self.player: Player | None = None
        self.seg_offset = 0
        self.seg_doc = None
        self.muted = False

    def begin(self, context: str, lang: str) -> None:
        """Nouvelle question sur un nouveau texte (sinon : question de suite)."""
        self.stop_speaking()
        if self.conv is not None:
            self.conv.stop()
        self.conv = assistant.Conversation(self.cfg, context, lang)
        threading.Thread(target=self.conv.prefill, daemon=True, name="assistant-pre").start()

    def ask(self, question: str, shown: str, lang: str = "", action: dict | None = None,
            can_replace: bool = False) -> None:
        """Question libre (action None) ou action (question = précision dite en plus)."""
        if self.conv is None:
            self.begin("", lang)
        if lang:
            self.conv.lang = lang
        self.stop_speaking()
        self.action, self.question = action, question
        transform = action is not None and action["type"] == "transformer"
        self.speak = action["lire"] if action is not None else True
        self.can_replace = can_replace and transform
        if self.media is not None and self.speak:
            self.media.hold("demander")      # la musique s'arrête dès la question posée
        self._gen += 1
        gen = self._gen
        self._reset_answer()
        self.card.start(shown, "transform" if transform else "answer", self.can_replace,
                        self.speak)
        conv = self.conv
        log.info("Demander (%s, %d caractères de texte) : %s%s", conv.model,
                 len(conv.context), f"[{action['nom']}] " if action else "", question)

        def work():
            import time
            t0 = time.monotonic()
            first = []

            def piece(p):
                if not first:
                    first.append(time.monotonic() - t0)
                    log.info("Demander : premiers mots en %.1f s", first[0])
                self._piece.emit(gen, p)
            try:
                conv.check()
                text = assistant.run_question(conv, question, piece, action)
                log.info("Demander : réponse de %d caractères en %.1f s", len(text),
                         time.monotonic() - t0)
                self._done.emit(gen, text)
            except assistant.NotInstalled as exc:
                log.info("Assistant absent : %s", exc)
                self._failed.emit(gen, "install")
            except Exception as exc:
                log.exception("Erreur de l'assistant")
                self._failed.emit(gen, str(exc))

        threading.Thread(target=work, daemon=True, name="assistant").start()

    def stop_speaking(self) -> None:
        if self.player is not None:
            p, self.player = self.player, None
            p.shutdown()
        self.card.highlight(-1, -1)
        if self.media is not None:
            self.media.release("demander")

    def close(self) -> None:
        self._gen += 1
        if self.conv is not None:
            self.conv.stop()
        self.conv = None
        self.stop_speaking()
        self.card.hide()
        self.card.placed = False
        self.card.dragged = False

    # ------------------------------------------------------------------ réponse
    def _on_piece(self, gen: int, piece: str) -> None:
        if gen != self._gen:
            return
        if piece == assistant.RESET:          # le modèle a dérapé : on recommence
            self.stop_speaking()
            self.raw = self.text = ""
            self.spoken_upto, self.speak_lang = 0, ""
            self.card.set_text("")
            self.card.set_status("Nouvel essai…")
            if self.media is not None and self.speak:
                self.media.hold("demander")
            return
        self.raw += piece
        transform = self.action is not None and self.action["type"] == "transformer"
        self.text = self.raw.strip("\n") if transform else assistant.clean_for_speech(self.raw)
        self.card.set_text(self.text)
        self.card.set_status("")
        self._maybe_speak()

    def _on_done(self, gen: int, text: str) -> None:
        if gen != self._gen:
            return
        self.final = True
        if not self.text.strip():
            self.card.show_message("(Pas de réponse.)")
            return
        if self.action is not None:
            from . import actions
            actions.count_use(self.action["id"])
        elif self.question and len(self.conv.messages) <= 2:
            self.card.show_keep(True)        # question libre réussie : à garder ?
        if self.card.mode == "transform":
            self.card.set_status("Aperçu : rien n'est encore remplacé" if self.can_replace
                                 else "")
        self._maybe_speak()

    def _on_failed(self, gen: int, err: str) -> None:
        if gen != self._gen:
            return
        self.stop_speaking()
        if err == "install":
            size = "4,7 Go" if "7b" in assistant.assistant_model(self.cfg) else "2 Go"
            self.card.show_message(
                "L'assistant n'est pas encore installé sur ce PC. Il fonctionne sans "
                f"Internet ni compte : un modèle d'environ {size} est téléchargé une seule "
                "fois.", install=True)
        else:
            self.card.show_message(f"L'assistant n'a pas pu répondre : {err}")

    # ------------------------------------------------------------------ voix
    def _next_segment(self) -> tuple[int, int] | None:
        rest = self.text[self.spoken_upto:]
        if not rest:
            return None
        if self.final:
            return self.spoken_upto, len(self.text)
        cuts = [m.end() for m in BOUNDARY.finditer(rest)]
        if not cuts:
            return None
        cut = cuts[0] if self.spoken_upto == 0 else cuts[-1]
        return self.spoken_upto, self.spoken_upto + cut

    def _maybe_speak(self) -> None:
        if not self.speak:
            return
        if self.muted or (self.player is not None and self.player.playing):
            return
        while True:
            seg = self._next_segment()
            if seg is None:
                if self.final and self.player is None:
                    self.card.set_playing(False)
                    self.card.highlight(-1, -1)
                    if self.media is not None:
                        self.media.release("demander")
                return
            a, b = seg
            self.spoken_upto = b
            if self.text[a:b].strip():
                break
        piece = self.text[a:b]
        if not self.speak_lang:
            self.speak_lang = self._pick_lang(piece)
        doc = from_text(piece)
        if not doc.words:
            return
        old = self.player
        self.player = Player(self.tts, doc, self.speak_lang, float(self.speed_getter()))
        self.seg_offset, self.seg_doc = a, doc
        self.player.wordChanged.connect(self._on_word)
        self.player.finished.connect(self._on_finished)
        self.player.error.connect(self._on_error)
        if old is not None:
            old.shutdown()
        self.card.set_playing(True)
        if self.media is not None:
            self.media.hold("demander")
        self.player.play_from(0)

    def _pick_lang(self, text: str) -> str:
        from . import langdetect
        from .languages import LANGS
        base = self.conv.lang if self.conv else "fr"
        lang = langdetect.detect(text, base, tuple(LANGS))
        for cand in (lang, base, "multi", "fr"):
            if self.tts.available(cand):
                return cand
        return base

    def _on_word(self, i: int) -> None:
        if self.sender() is not self.player or self.seg_doc is None:
            return
        if 0 <= i < len(self.seg_doc.words):
            w = self.seg_doc.words[i]
            self.card.highlight(self.seg_offset + w.start, self.seg_offset + w.end)

    def _on_finished(self) -> None:
        if self.sender() is not self.player:
            return
        p, self.player = self.player, None
        p.shutdown()
        self._maybe_speak()

    def _on_error(self, err: str) -> None:
        if self.sender() is not self.player:
            return
        log.warning("Lecture de la réponse impossible : %s", err)
        self.card.set_status("Voix indisponible")
        self.player = None
        self.card.set_playing(False)
        if self.media is not None:
            self.media.release("demander")

    # ------------------------------------------------------------------ boutons
    def _toggle_play(self) -> None:
        if self.player is not None and self.player.playing:
            paused = self.player._paused.is_set()
            self.player.toggle_pause()
            self.card.set_playing(paused)
            return
        # Lecture terminée (ou arrêtée) : on relit depuis le début.
        self.muted = False
        self.spoken_upto = 0
        self.speak_lang = ""
        self._maybe_speak()

    def _copy(self) -> None:
        if winutil.set_clipboard_text(self.text.strip()):
            self.card.set_status("Copié")
            QTimer.singleShot(1500, lambda: self.card.set_status(""))

    def _insert(self) -> None:
        """Colle la réponse là où se trouve le curseur (la fenêtre d'avant garde le
        clavier : la carte ne le prend jamais)."""
        if self.text.strip():
            winutil.paste_text(self.text.strip())
            self.card.set_status("Inséré")
            QTimer.singleShot(1500, lambda: self.card.set_status(""))

    def _replace(self) -> None:
        """Remplace le texte sélectionné (toujours sélectionné dans l'application :
        la carte ne prend jamais le clavier). Ctrl + Z l'annule."""
        if not self.text.strip():
            return
        winutil.paste_text(self.text.strip("\n"))
        log.info("Demander : texte remplacé (%d caractères)", len(self.text))
        self.card.set_status("Remplacé · Ctrl + Z pour annuler")
        gen = self._gen
        QTimer.singleShot(1800, lambda: self.close() if self._gen == gen else None)

    def _keep(self) -> None:
        from .ui_actions import KeepDialog
        self._keep_dialog = KeepDialog(self.question)
        self._keep_dialog.saved.connect(
            lambda name: (self.card.show_keep(False),
                          self.card.set_status(f"Action « {name} » ajoutée")))
        self._keep_dialog.show_centered(self.card)

    def _install(self) -> None:
        if self.install_cb is not None:
            self.install_cb()
