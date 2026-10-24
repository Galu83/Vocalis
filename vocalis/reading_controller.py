"""Chef d'orchestre de la lecture à voix haute.

Une touche (Ctrl + Alt + Maj + J par défaut) :
1. un texte est sélectionné dans une application qui donne son texte à Windows
   (navigateur, Word…) : il est lu SUR PLACE, le cadre suit le défilement ;
2. une sélection ailleurs : elle est copiée et lue dans une fenêtre de lecture ;
3. rien de sélectionné : on clique sur un paragraphe (ou on trace un rectangle).
   - texte de l'application si possible (accessibilité Windows) ;
   - sinon reconnaissance de texte (OCR) de la fenêtre, regroupée en paragraphes,
     et suivi du défilement par comparaison d'images.
Pendant une lecture, Alt + clic relance la lecture au paragraphe (et au mot) cliqué.
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage

from . import langdetect, ocr, screenmap, winutil
from .ocr_layout import group_paragraphs, paragraph_at, reanchor, same_column, word_at
from .reading import Player, from_ocr_paragraphs, from_paragraphs, from_text
from .scrolltrack import estimate_shift, image_to_gray, profile
from .tts import Tts
from .uia_text import UiaReader
from .translator import Translator
from .ui_reading import (Controls, FollowOverlay, ReaderWindow, SelectionOverlay,
                         TranslationPanel)

log = logging.getLogger(__name__)

POLL_MS = 100              # rafraîchissement de la position du mot (accessibilité)
TRACK_MS = 160             # suivi du défilement (OCR)
UIA_TIMEOUT_S = 1.5


class _Relay(QObject):
    call = Signal(object)


# =========================================================================== sources
class OcrSource:
    """Positions issues de l'OCR, décalées du défilement mesuré."""

    def __init__(self, doc, region: QRectF, ref_gray, dpr: float):
        self.doc = doc
        self.region = QRectF(region)
        self.ref = profile(ref_gray)
        self.dpr = dpr
        self.dy = 0.0              # défilement (pixels logiques, positif = contenu remonté)
        self.misses = 0
        self.reocr_running = False

    def _shifted(self, rect) -> QRectF | None:
        if rect is None:
            return None
        x, y, w, h = rect
        r = QRectF(x, y - self.dy, w, h)
        return r if self.region.intersects(r) else None

    def word_rects(self, i: int) -> list[QRectF]:
        if not (0 <= i < len(self.doc.words)):
            return []
        r = self._shifted(self.doc.words[i].rect)
        return [r] if r is not None else []

    def para_rects(self, p: int) -> list[QRectF]:
        out = []
        for w in self.doc.words:
            if w.para == p:
                r = self._shifted(w.rect)
                if r is not None:
                    out.append(r)
        return out


class UiaSource:
    """Positions demandées en direct à l'application (elles suivent le défilement)."""

    def __init__(self, reader: UiaReader):
        self.reader = reader
        self.word: list[QRectF] = []
        self.para: list[QRectF] = []
        self.pending = False


def _logical(rects) -> list[QRectF]:
    return [screenmap.to_logical_rect(*r) for r in rects]


# =========================================================================== contrôleur
from .languages import ALL as LANGS, active as active_languages, lower_name  # noqa: E402

MENU_STYLE = """
QMenu { background: #1C1C21; color: #F0F0F0; border: 1px solid rgba(255,255,255,40);
        border-radius: 10px; padding: 6px; font: 10pt 'Segoe UI'; }
QMenu::item { padding: 7px 18px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background: rgba(77,163,255,70); }
QMenu::item:disabled { color: #8E8E96; font: 600 8pt 'Segoe UI'; padding: 6px 12px 2px 12px; }
QMenu::separator { height: 1px; background: rgba(255,255,255,30); margin: 5px 8px; }
"""


class ReadingController(QObject):
    def __init__(self, cfg: dict, bridge, pill):
        super().__init__()
        self.cfg = cfg
        self.lec = cfg["lecture"]
        self.bridge = bridge
        self.pill = pill
        self.tts = Tts(cfg)
        self.translator = Translator(cfg)
        self.panel: TranslationPanel | None = None      # texte traduit en cours
        self._source = "fr"                               # langue détectée du texte lu
        self.uia = UiaReader()
        self.speed = float(self.lec["vitesse"])
        self.player: Player | None = None
        self._click_timer: QTimer | None = None
        self._paused_by_click = False
        self.view = None                 # FollowOverlay ou ReaderWindow
        self.controls: Controls | None = None
        self.selector: SelectionOverlay | None = None
        self.source = None               # OcrSource ou UiaSource
        self.current = 0
        self.busy = False
        self.generation = 0              # invalide les réponses tardives
        self.on_active_changed = lambda active: None
        self._relay = _Relay()
        self._relay.call.connect(lambda fn: fn())
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh)
        self._track = QTimer(self)
        self._track.timeout.connect(self._track_scroll)

    def _ui(self, fn) -> None:
        """Exécute fn dans le fil de l'interface."""
        self._relay.call.emit(fn)

    def _await(self, fut, done, timeout: float = UIA_TIMEOUT_S) -> None:
        """Attend une réponse du fil d'accessibilité sans bloquer l'interface."""
        gen = self.generation

        def wait():
            try:
                res = fut.result(timeout=timeout)
            except Exception as exc:
                log.info("Accessibilité : pas de réponse (%s)", exc.__class__.__name__)
                res = None
            self._ui(lambda: done(res) if gen == self.generation else None)

        threading.Thread(target=wait, daemon=True, name="attente").start()

    # ------------------------------------------------------------------ entrée
    def start_reading(self) -> None:
        log.info("Lecture demandée (occupé=%s, en cours=%s)", self.busy, self.active)
        # La touche fonctionne comme un interrupteur : si une lecture est en cours
        # (ou si on est en train de choisir un paragraphe), elle arrête tout.
        if self.active or self.selector is not None:
            log.info("Lecture arrêtée par la touche de lecture")
            self.stop()
            return
        if self.busy:
            return
        self.stop()
        self.busy = True
        self._await(self.uia.selection(), self._on_uia_selection)

    def _on_uia_selection(self, doc) -> None:
        if doc is not None and doc.paragraphs:
            self.busy = False
            from PySide6.QtGui import QCursor
            screenmap.calibrate()
            self._start_uia(from_paragraphs(doc.paragraphs), QPointF(QCursor.pos()))
            return

        def copy():                       # application sans accessibilité : copier
            text = ""
            try:
                text = winutil.copy_selection()
            except Exception:
                log.exception("Copie de la sélection impossible")
            self._ui(lambda: self._on_copied(text))

        threading.Thread(target=copy, daemon=True, name="copie").start()

    def _on_copied(self, text: str) -> None:
        self.busy = False
        text = (text or "").replace("\r\n", "\n").strip()
        log.info("Lecture : %d caractère(s) copiés", len(text))
        if text and len(text.split()) < 3:
            # Un ou deux mots sélectionnés (sans doute par erreur) : on lit le
            # paragraphe sous la souris, comme pour un clic.
            from PySide6.QtGui import QCursor
            self._pick_at(QPointF(QCursor.pos()), at_word=False)
        elif text:
            self._start_window(text)
        else:
            self.start_picker()

    # ------------------------------------------------------------------ choix à l'écran
    def start_picker(self) -> None:
        self.stop()
        self.selector = SelectionOverlay()
        self.selector.picked.connect(
            lambda p: QTimer.singleShot(150, lambda: self._pick_at(QPointF(p), at_word=False)))
        self.selector.selected.connect(
            lambda r: QTimer.singleShot(150, lambda: self._ocr_region(QRectF(r), None, False)))
        self.selector.cancelled.connect(self._clear_selector)
        self.selector.start()

    def _clear_selector(self) -> None:
        self.selector = None

    def alt_click(self, x: int, y: int) -> None:
        """Alt + clic pendant une lecture (coordonnées physiques)."""
        if self.player is None:
            return
        # La souris est encore sur le point cliqué : Qt donne directement sa
        # position logique, et on en profite pour recaler les coordonnées.
        from PySide6.QtGui import QCursor
        screenmap.calibrate((x, y))
        point = QPointF(QCursor.pos())
        self.stop()
        QTimer.singleShot(150, lambda: self._pick_at(point, at_word=True))

    def plain_click(self, x: int, y: int, double: bool) -> None:
        """Essai : pendant une lecture, un clic n'importe où l'arrête ; un double-clic
        sur un mot la reprend à ce mot. Le premier clic met aussitôt en pause : on
        attend de savoir si c'est un double-clic avant d'arrêter."""
        if self.player is None and self._click_timer is None:
            return
        if self._on_own_window(x, y):
            return                              # barre de lecture, pastille, fenêtres
        if winutil.taking_screenshot():
            return                              # clic dans l'outil de capture d'écran
        if double:
            if self._click_timer is not None:
                self._click_timer.stop()
                self._click_timer = None
            log.info("Double-clic pendant la lecture : reprise au mot cliqué")
            self.alt_click(x, y)
            return
        if self.player is None:
            return
        if self.player.playing and not self.player._paused.is_set():
            self.player.toggle_pause()
            self._paused_by_click = True
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._click_timeout)
        timer.start(self._double_click_ms() + 60)
        self._click_timer = timer

    def _click_timeout(self) -> None:
        self._click_timer = None
        log.info("Clic pendant la lecture : arrêt")
        self.stop()

    @staticmethod
    def _double_click_ms() -> int:
        try:
            import ctypes
            return int(ctypes.windll.user32.GetDoubleClickTime() or 500)
        except Exception:
            return 500

    @staticmethod
    def _on_own_window(x: int, y: int) -> bool:
        from PySide6.QtWidgets import QApplication
        root = screenmap.root_window_at(x, y)
        if not root:
            return False
        for w in QApplication.topLevelWidgets():
            try:
                if w.isVisible() and int(w.winId()) == root:
                    return True
            except Exception:
                pass
        return False

    def _pick_at(self, point: QPointF, at_word: bool) -> None:
        self.selector = None
        self.busy = True
        screenmap.calibrate()               # la souris est sur le point cliqué
        nx, ny = screenmap.to_native(point.toPoint())
        log.info("Point choisi : logique (%d, %d), physique (%d, %d)",
                 point.x(), point.y(), nx, ny)
        self.pill.show_state("cleaning", "Recherche du texte…")

        def done(doc):
            self.busy = False
            if doc is not None and doc.paragraphs:
                d = from_paragraphs(doc.paragraphs, 0, doc.start_offset if at_word else 0)
                self.pill.show_state("done", "", autohide_ms=200)
                self._start_uia(d, point)
            else:
                # Tout l'écran du clic ; on ne garde ensuite que la colonne cliquée.
                screen = QGuiApplication.screenAt(point.toPoint()) or \
                    QGuiApplication.primaryScreen()
                self._ocr_region(QRectF(screen.geometry()), point, at_word)

        self._await(self.uia.paragraphs_at(nx, ny), done)

    # ------------------------------------------------------------------ OCR
    def _grab(self, region: QRectF) -> tuple[QImage, float] | None:
        """Capture de la zone (coordonnées logiques). On capture l'écran ENTIER puis
        on découpe : la correspondance pixels <-> coordonnées est alors certaine,
        même avec plusieurs écrans à des échelles différentes."""
        screen = QGuiApplication.screenAt(region.center().toPoint()) or \
            QGuiApplication.primaryScreen()
        geo = screen.geometry()
        full = screen.grabWindow(0).toImage()
        if full.isNull() or full.width() == 0:
            return None
        sx, sy = full.width() / geo.width(), full.height() / geo.height()
        r = region.intersected(QRectF(geo))
        crop = QRect(round((r.x() - geo.x()) * sx), round((r.y() - geo.y()) * sy),
                     max(1, round(r.width() * sx)), max(1, round(r.height() * sy)))
        img = full.copy(crop).convertToFormat(QImage.Format_ARGB32)
        if img.isNull() or img.width() == 0:
            return None
        if not getattr(self, "_logged_screens", False):
            self._logged_screens = True
            for sc in QGuiApplication.screens():
                log.info("Écran %s : %s, échelle %.2f", sc.name(), sc.geometry(),
                         sc.devicePixelRatio())
            log.info("Capture : écran %s, image %dx%d, zone %s", screen.name(),
                     full.width(), full.height(), r)
        return img, sx

    def _ocr_region(self, region: QRectF, click: QPointF | None, at_word: bool) -> None:
        self.selector = None
        grabbed = self._grab(region)
        if grabbed is None:
            self.pill.show_state("error", "Capture d'écran impossible", autohide_ms=3000)
            return
        img, dpr = grabbed
        ref_gray = image_to_gray(img)
        self.busy = True
        self.pill.show_state("cleaning", "Lecture du texte…")
        gen = self.generation

        def work():
            try:
                lines = self._recognize(img, region)
                paras = group_paragraphs(lines)
                start_word = 0
                if click is not None and paras:
                    p = paragraph_at(paras, click.x(), click.y())
                    paras = same_column(paras, p)
                doc = from_ocr_paragraphs(paras) if paras else None
                if doc and click is not None and at_word:
                    flat = [w for para in paras for line in para for w in line]
                    hit = word_at(flat, click.x(), click.y())
                    start_word = hit or 0
                if doc:
                    doc.start_word = start_word
                log.info("Lecture OCR : %d mot(s), %d paragraphe(s)",
                         len(doc.words) if doc else 0, len(paras))
                self._ui(lambda: self._on_ocr(gen, doc, region, ref_gray, dpr, None))
            except Exception as exc:
                log.exception("OCR impossible")
                err = exc
                self._ui(lambda: self._on_ocr(gen, None, region, ref_gray, dpr, err))

        threading.Thread(target=work, daemon=True, name="ocr").start()

    @staticmethod
    def _recognize(img: QImage, region: QRectF):
        """OCR de l'image ; renvoie les lignes en coordonnées écran logiques."""
        biggest = max(img.width(), img.height())
        factor = 2.0 if biggest < 1400 else 1.0
        factor = min(factor, (ocr.max_dimension() - 10) / biggest)
        if abs(factor - 1.0) > 0.01:
            img = img.scaled(int(img.width() * factor), int(img.height() * factor),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        data = bytes(img.constBits())[: img.sizeInBytes()]
        w, h = img.width(), img.height()
        lines = ocr.recognize(data, w, h, "fr")
        text = " ".join(word.text for line in lines for word in line)
        found = langdetect.detect_any(text)
        if found not in ("fr", "??") and ocr.has_language(found):
            lines = ocr.recognize(data, w, h, found)
        sx, sy = region.width() / w, region.height() / h
        for line in lines:
            for word in line:
                word.x = region.x() + word.x * sx
                word.y = region.y() + word.y * sy
                word.w *= sx
                word.h *= sy
        return lines

    def _on_ocr(self, gen, doc, region, ref_gray, dpr, error) -> None:
        if gen != self.generation:
            return
        self.busy = False
        if error is not None:
            self.pill.show_state("error", f"OCR impossible : {error}", autohide_ms=5000)
            return
        if doc is None or not doc.words:
            self.pill.show_state("info", "Aucun texte trouvé", autohide_ms=2500)
            return
        self.pill.show_state("done", "", autohide_ms=200)
        self.source = OcrSource(doc, region, ref_gray, dpr)
        anchor = QPointF(region.center().x(), region.bottom())
        first = doc.words[doc.start_word].rect
        if first:
            anchor = QPointF(first[0], first[1] + first[3])
        self._start_follow(doc, anchor)
        self._track.start(TRACK_MS)

    def _track_scroll(self) -> None:
        src = self.source
        if not isinstance(src, OcrSource) or src.reocr_running:
            return
        grabbed = self._grab(src.region)
        if grabbed is None:
            return
        img, dpr = grabbed
        dy, score = estimate_shift(src.ref, profile(image_to_gray(img)))
        if score >= 0.8:
            src.misses = 0
            src.dy = dy / dpr
            far = abs(dy) > 0.55 * img.height()
            if not far:
                self._refresh()
                return
        else:
            src.misses += 1
            if src.misses < 3:
                return
        # Trop défilé ou contenu changé : on refait l'OCR et on recale les mots.
        self._reocr(img, dpr)

    def _reocr(self, img: QImage, dpr: float) -> None:
        src = self.source
        src.reocr_running = True
        gen = self.generation
        doc_words = [w.text for w in src.doc.words]
        start = self.current
        region = src.region

        def work():
            try:
                lines = self._recognize(img, region)
                flat = [w for line in lines for w in line]
                found = reanchor(doc_words, start, flat)
            except Exception:
                log.exception("Recalage OCR impossible")
                found = {}
            self._ui(lambda: self._on_reocr(gen, img, dpr, start, found))

        threading.Thread(target=work, daemon=True, name="recalage").start()

    def _on_reocr(self, gen, img, dpr, start, found) -> None:
        src = self.source
        if gen != self.generation or not isinstance(src, OcrSource):
            return
        log.info("Recalage après défilement : %d mot(s) retrouvés", len(found))
        for i in range(start, len(src.doc.words)):
            w = found.get(i)
            src.doc.words[i].rect = (w.x, w.y, w.w, w.h) if w else None
        src.ref = profile(image_to_gray(img))
        src.dy = 0.0
        src.misses = 0
        src.reocr_running = False
        self._refresh()

    # ------------------------------------------------------------------ accessibilité
    def _start_uia(self, doc, anchor: QPointF) -> None:
        self.source = UiaSource(self.uia)
        self.uia.set_words([(w.para, w.text) for w in doc.words])
        self._start_follow(doc, anchor)
        self._poll.start(POLL_MS)

    # ------------------------------------------------------------------ lecture
    def _start_follow(self, doc, anchor: QPointF) -> None:
        screen = QGuiApplication.screenAt(anchor.toPoint()) or QGuiApplication.primaryScreen()
        overlay = FollowOverlay(screen.geometry(), self.lec["couleur_surlignage"],
                                float(self.lec["opacite_surlignage"]))
        overlay.show()
        winutil.exclude_from_capture(overlay)
        controls = Controls(floating=True)
        controls.adjustSize()
        avail = screen.availableGeometry()
        x = int(max(avail.left() + 8, min(anchor.x() - controls.width() / 2,
                                          avail.right() - controls.width() - 8)))
        # Au-dessus du curseur, avec assez d'écart pour ne cacher ni la souris ni la
        # ligne pointée ; en dessous seulement s'il n'y a pas la place en haut.
        y = int(anchor.y() - controls.height() - 34)
        if y < avail.top() + 8:
            y = int(anchor.y() + 40)
        controls.move(x, min(max(avail.top() + 8, y), avail.bottom() - controls.height() - 8))
        controls.show()
        winutil.exclude_from_capture(controls)
        self._start(doc, overlay, controls)

    def _start_window(self, text: str) -> None:
        doc = from_text(text)
        win = ReaderWindow(text, self.lec["couleur_surlignage"],
                           float(self.lec["opacite_surlignage"]),
                           int(self.lec["taille_police_fenetre"]))
        win.set_spans([(w.start, w.end) for w in doc.words])
        win.wordClicked.connect(self._jump)
        win.closed.connect(self.stop)
        win.show()
        win.raise_()
        win.activateWindow()
        self._start(doc, win, win.controls)

    @property
    def listen(self) -> str:
        """Langue d'écoute choisie : « auto » (langue du texte) ou fr / pt / en.
        Elle reste la même d'une lecture à l'autre, jusqu'à ce qu'on la change."""
        v = self.lec.get("langue_ecoute", "auto")
        return v if v in ("auto",) + LANGS else "auto"

    def _pick_lang(self, text: str) -> str | None:
        """Voix utilisée : la langue d'écoute choisie, ou en automatique celle du texte
        (voix multilingue pour les langues sans voix dédiée)."""
        if self.listen != "auto" and self.tts.available(self.listen):
            return self.listen
        src = self._source
        if src in LANGS and self.tts.available(src):
            return src
        if src == "??" and self.tts.available("multi"):
            return "multi"
        for lang in (self.cfg["dictee"].get("langue_preferee", "fr") or "fr", "fr", "pt"):
            if self.tts.available(lang):
                return lang
        return None

    @staticmethod
    def _paragraph_languages(doc, prefer: str = "") -> set[str]:
        """Langues des paragraphes lus (depuis le point de départ)."""
        langs, current, words = set(), None, []
        for w in doc.words[doc.start_word:doc.start_word + 1500]:
            if w.para != current and words:
                if len(words) >= 6:
                    langs.add(langdetect.detect_any(" ".join(words), prefer=prefer))
                words = []
            current = w.para
            words.append(w.text)
        if len(words) >= 6:
            langs.add(langdetect.detect_any(" ".join(words), prefer=prefer))
        return langs

    def _needs_translation(self, doc, lang: str | None) -> bool:
        """Langue d'écoute imposée et texte (ou une partie) dans une autre langue ?"""
        if self.listen == "auto" or lang not in LANGS:
            return False
        if lang != self._source:
            return True
        return any(l not in ("??", lang) for l in self._paragraph_languages(doc, lang))

    @staticmethod
    def _detect_source(text: str) -> str:
        """Langue du texte (code du catalogue), ou « ?? » si elle n'est pas reconnue."""
        return langdetect.detect_any(text)

    def _start(self, doc, view, controls: Controls) -> None:
        # Langue du texte À PARTIR d'où l'on lit (pas des paragraphes autour).
        first = [w for w in doc.words[doc.start_word:doc.start_word + 150]
                 if w.para == doc.words[doc.start_word].para] if doc.words else []
        if len(first) < 12:                   # paragraphe très court : un peu plus loin
            first = doc.words[doc.start_word:doc.start_word + 60]
        self._source = langdetect.detect_any(" ".join(w.text for w in first),
                                             prefer=self.listen if self.listen != "auto"
                                             else "")
        lang = self._pick_lang(doc.text)
        log.info("Lecture : démarrage en %s, %d mot(s), à partir du mot %d",
                 lang, len(doc.words), doc.start_word)
        if lang is None:
            self.pill.show_state("error", "Aucune voix installée (relance l'installateur)",
                                 autohide_ms=5000)
            self._close_widgets(view, controls)
            self.source = None
            return
        self.view, self.controls = view, controls
        self.player = Player(self.tts, doc, lang, self.speed, self.translator)
        self.player.wordChanged.connect(self._on_word)
        self.player.translation.connect(self._on_translation)
        self.player.translatedWord.connect(
            lambda i: self.panel.set_word(i) if self.panel is not None else None)
        self.player.stateChanged.connect(controls.set_state)
        self.player.error.connect(
            lambda m: self.pill.show_state("error", f"Lecture : {m}", autohide_ms=5000))
        if isinstance(view, FollowOverlay):
            # À la fin du texte, le cadre et la barre disparaissent.
            player = self.player
            self.player.finished.connect(
                lambda: QTimer.singleShot(600, lambda: self.stop()
                                          if self.player is player else None))
        controls.playPause.connect(self.player.toggle_pause)
        controls.stopRequested.connect(self._stop_audio)
        controls.closeRequested.connect(self.stop)
        controls.speedDelta.connect(self._speed)
        controls.langToggle.connect(self._lang_menu)
        controls.moved.connect(lambda: self.panel.place() if self.panel is not None else None)
        controls.set_speed(self.speed)
        controls.set_lang(lang)
        self.on_active_changed(True)
        self.current = doc.start_word
        self.player.auto_voice = self.listen == "auto"
        if self._needs_translation(doc, lang):
            # Texte dans une autre langue : lu traduit dans la langue d'écoute.
            log.info("Lecture : texte en %s, lu traduit en %s", self._source, lang)
            self._set_target(lang, start=False)
        else:
            self._show_badge()
        self.player.play_from(doc.start_word)
        self._refresh()

    def _on_word(self, idx: int) -> None:
        if idx < 0:
            return
        self.current = idx
        if isinstance(self.view, ReaderWindow):
            # lecture traduite : on ne surligne pas un mot de l'original
            self.view.set_word(-1 if self.player and self.player.target
                               and self.player.translating_now else idx)
        else:
            self._refresh()

    def _refresh(self) -> None:
        """Met à jour le cadre et le mot surligné à leur position actuelle."""
        view, src, player = self.view, self.source, self.player
        if not isinstance(view, FollowOverlay) or player is None or src is None:
            return
        i = self.current
        para = player.doc.words[i].para if 0 <= i < len(player.doc.words) else 0
        translating = bool(player.target) and player.translating_now
        if isinstance(src, OcrSource):
            view.set_rects([] if translating else src.word_rects(i), src.para_rects(para))
            return
        if src.pending:
            return
        src.pending = True
        gen = self.generation
        fut = src.reader.rects(i, para, True)

        def done(res):
            src.pending = False
            if res and self.source is src:
                w, p = res
                src.word, src.para = ([] if translating else _logical(w)), _logical(p)
                if self.view is not None:
                    self.view.set_rects(src.word, src.para)

        def wait():
            try:
                res = fut.result(timeout=2)
            except Exception:
                res = None
            self._ui(lambda: done(res) if gen == self.generation else None)

        threading.Thread(target=wait, daemon=True).start()

    def _jump(self, idx: int) -> None:
        if self.player:
            self.player.play_from(idx)

    def _stop_audio(self) -> None:
        """Arrête la voix et revient au début du passage ; le cadre reste affiché."""
        if self.player:
            start = self.player.doc.start_word
            self.player.stop()
            self.player.current = start
            self.current = start
            self._refresh()

    def _speed(self, delta: float) -> None:
        if self.player:
            self.player.set_speed(self.player.speed + delta)
            self.speed = self.player.speed
            if self.controls:
                self.controls.set_speed(self.speed)
            # La vitesse choisie est gardée pour les lectures suivantes.
            self.lec["vitesse"] = self.speed
            from .config import update_settings
            update_settings("lecture", {"vitesse": self.speed})

    # ------------------------------------------------------------------ traduction
    def _lang_menu(self) -> None:
        """Menu du badge : la langue du texte est détectée ; on choisit seulement la
        langue d'écoute (gardée pour les lectures suivantes)."""
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu
        if not self.player or not self.controls:
            return
        def names_get(code, default):
            return "autre langue" if code == "??" else lower_name(code)
        menu = QMenu()
        menu.setStyleSheet(MENU_STYLE)
        head = menu.addAction(f"Texte détecté : {names_get(self._source, self._source)}")
        head.setEnabled(False)
        menu.addSeparator()
        head = menu.addAction("Écouter en")
        head.setEnabled(False)
        from .languages import name as lang_name
        choices = [("auto", "Automatique (langue du texte)")] + [
            (c, lang_name(c)) for c in active_languages(self.cfg)]
        for code, label in choices:
            note = "  (traduit)" if code not in ("auto", self._source) else ""
            act = QAction(("✓  " if code == self.listen else "     ") + label + note, menu)
            act.setEnabled(code == "auto" or self.tts.available(code))
            act.triggered.connect(lambda _=False, c=code: self._set_listen(c))
            menu.addAction(act)
        menu.addSeparator()
        act = QAction("     Changer de narrateur…", menu)
        act.triggered.connect(lambda: self.bridge.openSettings.emit("voix"))
        menu.addAction(act)
        act = QAction("     Corriger une prononciation…", menu)
        act.triggered.connect(lambda: self.bridge.openSettings.emit("prononciation"))
        menu.addAction(act)
        btn = self.controls.btn_lang
        # Menu non bloquant : il se ferme tout seul avec la barre de lecture.
        self._close_menu()
        self._menu = menu
        menu.popup(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _close_menu(self) -> None:
        menu, self._menu = getattr(self, "_menu", None), None
        if menu is not None:
            try:
                menu.close()
                menu.deleteLater()
            except RuntimeError:
                pass

    def _set_listen(self, choice: str) -> None:
        """Nouvelle langue d'écoute : appliquée tout de suite et mémorisée."""
        self.lec["langue_ecoute"] = choice
        from .config import update_settings
        update_settings("lecture", {"langue_ecoute": choice})
        log.info("Langue d'écoute : %s", choice)
        if not self.player:
            return
        lang = self._pick_lang(self.player.doc.text)
        if lang is None:
            return
        self.player.auto_voice = choice == "auto"
        target = lang if self._needs_translation(self.player.doc, lang) else None
        self.player.lang = lang if not target else self.player.lang
        self._set_target(target, start=False)
        self._show_badge()
        self.player.play_from(self.player.current)

    def _show_badge(self) -> None:
        if not self.controls or not self.player:
            return
        src = "" if self._source == "??" else self._source
        if self.player.target:
            self.controls.set_lang(src, self.player.target)
        else:
            self.controls.set_lang("auto" if self.player.lang == "multi" else self.player.lang)

    def _set_target(self, target: str | None, start: bool = True) -> None:
        if not self.player:
            return
        log.info("Lecture : %s", f"traduction en {target}" if target else "texte original")
        if self.controls:
            self.controls.show_suggestion("")
        if target:
            if self.panel is None:
                self.panel = TranslationPanel(self.lec["couleur_surlignage"],
                                              float(self.lec["opacite_surlignage"]))
                self.panel.anchor = self.controls
            self.panel.set_target(target)
            self.panel.set_text("Traduction en cours…")
            # La bulle n'apparaît qu'avec le premier passage vraiment traduit.
            winutil.exclude_from_capture(self.panel)
        elif self.panel is not None:
            self._close_panel()
        if start:
            self.player.set_target(target)
        else:
            self.player.target = target or None
        self._show_badge()
        self._refresh()

    def _on_translation(self, text: str) -> None:
        if self.panel is None:
            return
        if not text:
            self.panel.hide()                   # passage déjà dans la langue d'écoute
            self._refresh()
            return
        self.panel.set_text(text)
        if not self.panel.isVisible():
            self.panel.show()
            winutil.exclude_from_capture(self.panel)

    def _close_panel(self) -> None:
        panel, self.panel = self.panel, None
        if panel is not None:
            try:
                panel.close()
                panel.deleteLater()
            except RuntimeError:
                pass

    def _toggle_lang(self) -> None:
        if not self.player:
            return
        new = "pt" if self.player.lang == "fr" else "fr"
        if not self.tts.available(new):
            self.pill.show_state("info", f"Voix {new.upper()} non installée", autohide_ms=2500)
            return
        self.player.set_lang(new)
        if self.controls:
            self.controls.set_lang(new)

    # ------------------------------------------------------------------ arrêt
    def _close_widgets(self, *widgets) -> None:
        for w in widgets:
            if w is None:
                continue
            try:
                if isinstance(w, ReaderWindow):
                    w.closed.disconnect(self.stop)
            except (RuntimeError, TypeError):
                pass
            try:
                w.close()
                w.deleteLater()
            except RuntimeError:
                pass

    def stop(self) -> None:
        self.generation += 1
        if self._click_timer is not None:
            self._click_timer.stop()
            self._click_timer = None
        self._poll.stop()
        self._track.stop()
        if self.selector is not None:
            try:
                self.selector.close()
            except RuntimeError:
                pass
            self.selector = None
        self._close_panel()
        self._close_menu()
        player, view, controls = self.player, self.view, self.controls
        self.player = self.view = self.controls = None
        self.source = None
        self.busy = False
        if player:
            player.shutdown()
        self._close_widgets(view, controls if controls is not view else None)
        if player:
            self.on_active_changed(False)

    @property
    def active(self) -> bool:
        return self.player is not None

