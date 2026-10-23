"""Fenêtre de paramètres de Vocalis."""
from __future__ import annotations

import copy
import logging
import os
import subprocess
import sys
import threading
import time

import requests
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget,
                               QAbstractItemView, QHeaderView, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import dictionary, winutil
from .hardware import ENGINES, has_nvidia_gpu, resolve_engine
from .languages import ALL as ALL_LANGS, active as active_languages, name as lang_name
from .config import (DEFAULTS, LOG_PATH, MODELS_DIR, ROOT, key_label, load_config,
                     save_config)
from .icon import make_icon

log = logging.getLogger(__name__)

ACCENT = "#E5484D"

STYLE = f"""
QWidget#settings {{ background: #F4F4F6; }}
QWidget {{ font-family: 'Segoe UI'; font-size: 10pt; color: #1D1D1F; }}
QListWidget#nav {{ background: #ECECEF; border: none; padding: 10px 6px; outline: 0; }}
QListWidget#nav::item {{ padding: 9px 12px; border-radius: 8px; margin: 1px 0; }}
QListWidget#nav::item:selected {{ background: white; color: {ACCENT}; font-weight: 600; }}
QListWidget#nav::item:hover:!selected {{ background: #E2E2E6; }}
QFrame#card {{ background: white; border: 1px solid #E3E3E8; border-radius: 10px; }}
QLabel#cardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#pageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#hint {{ color: #6E6E73; font-size: 9pt; }}
QPushButton {{ background: white; border: 1px solid #CFCFD4; border-radius: 7px;
               padding: 6px 14px; }}
QPushButton:hover {{ background: #F2F2F5; }}
QPushButton:disabled {{ color: #A0A0A5; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #D33C41; }}
QPushButton#hotkey {{ min-width: 170px; font-weight: 600; font-family: 'Segoe UI'; }}
QPushButton#hotkey[capturing="true"] {{ border: 2px solid {ACCENT}; color: {ACCENT}; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: white; border: 1px solid #CFCFD4; border-radius: 6px; padding: 4px 6px; }}
QProgressBar {{ border: 1px solid #CFCFD4; border-radius: 5px; background: #F2F2F5;
                height: 10px; text-align: center; font-size: 8pt; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
"""

WHISPER_MODELS = [
    ("large-v3-turbo", "Large v3 Turbo — recommandé (rapide et précis)"),
    ("large-v3", "Large v3 — un peu plus précis, 2 fois plus lent"),
    ("medium", "Medium — plus léger"),
    ("small", "Small — très léger, moins précis"),
]
CLEAN_MODELS = [
    ("qwen2.5:7b", "Qwen 2.5 7B — recommandé (4,7 Go)"),
    ("qwen2.5:3b", "Qwen 2.5 3B — plus léger (1,9 Go)"),
    ("llama3.1:8b", "Llama 3.1 8B (4,9 Go)"),
    ("mistral:7b", "Mistral 7B (4,1 Go)"),
    ("gemma2:9b", "Gemma 2 9B (5,4 Go)"),
]
HOTKEYS = [
    ("dictee_bascule", "Dictée marche / arrêt",
     "Un appui pour commencer, un appui (ou la croix sur la pastille) pour finir."),
    ("lecture", "Lecture à voix haute",
     "Lit la sélection. Sans sélection : clique sur un paragraphe ou trace un rectangle."),
    ("arreter_lecture", "Arrêter la lecture", "Actif seulement pendant une lecture."),
    ("demander", "Demander",
     "Sélectionne un texte (ou pointe un paragraphe), appuie, pose ta question à voix haute "
     "puis appuie de nouveau. La réponse s'affiche et elle est lue."),
]
SAMPLES = {
    "fr": "Bonjour ! Voici un essai de lecture à voix haute, à la vitesse choisie.",
    "pt": "Olá! Este é um teste de leitura em voz alta, à velocidade escolhida.",
    "en": "Hello! This is a read-aloud test at the selected speed.",
    "de": "Hallo! Das ist ein Test zum Vorlesen in der gewählten Geschwindigkeit.",
}


# =========================================================================== tâches de fond
class _Relay(QObject):
    done = Signal(object)
    progress = Signal(object)


_live: set = set()


def run_bg(fn, on_done=None, on_progress=None) -> None:
    """Exécute fn dans un fil séparé ; on_done(résultat ou exception) dans l'interface."""
    relay = _Relay()
    _live.add(relay)

    def finish(res):
        _live.discard(relay)
        if on_done:
            on_done(res)

    relay.done.connect(finish)
    if on_progress:
        relay.progress.connect(on_progress)

    def work():
        try:
            res = fn(relay.progress.emit) if on_progress else fn()
        except Exception as exc:  # renvoyé à l'interface
            log.warning("Tâche de fond : %s", exc)
            res = exc
        relay.done.emit(res)

    threading.Thread(target=work, daemon=True, name="parametres").start()


# =========================================================================== capture de touche
def _qt_key_name(event) -> str | None:
    k = int(event.key())
    special = {
        Qt.Key_Space: "space", Qt.Key_Escape: "esc", Qt.Key_Tab: "tab", Qt.Key_Backtab: "tab",
        Qt.Key_Insert: "insert", Qt.Key_Delete: "delete", Qt.Key_Home: "home",
        Qt.Key_End: "end", Qt.Key_PageUp: "page up", Qt.Key_PageDown: "page down",
        Qt.Key_Pause: "pause", Qt.Key_ScrollLock: "scroll lock", Qt.Key_Print: "print screen",
        Qt.Key_CapsLock: "caps lock", Qt.Key_Up: "up", Qt.Key_Down: "down",
        Qt.Key_Left: "left", Qt.Key_Right: "right", Qt.Key_Return: "enter",
        Qt.Key_Enter: "enter", Qt.Key_Backspace: "backspace", Qt.Key_Menu: "menu",
    }
    for qk, name in special.items():
        if k == int(qk):
            return name
    if int(Qt.Key_F1) <= k <= int(Qt.Key_F24):
        return f"f{k - int(Qt.Key_F1) + 1}"
    if int(Qt.Key_A) <= k <= int(Qt.Key_Z) or int(Qt.Key_0) <= k <= int(Qt.Key_9):
        return chr(k).lower()
    text = event.text()
    if text and text.isprintable() and not text.isspace():
        return text.lower()
    return None


_MODIFIER_KEYS = {int(Qt.Key_Shift), int(Qt.Key_Control), int(Qt.Key_Alt), int(Qt.Key_Meta),
                  int(Qt.Key_AltGr)}


class HotkeyButton(QPushButton):
    changed = Signal(str)
    capturing_changed = Signal(bool)     # capture d'une touche en cours / terminée

    def __init__(self, value: str):
        super().__init__()
        self.setObjectName("hotkey")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Clique, puis appuie sur la touche ou la combinaison voulue")
        self.value = value
        self.capturing = False
        self.clicked.connect(self._start)
        self._refresh()

    def set_value(self, value: str) -> None:
        self.value = value
        self._refresh()

    def _refresh(self, text: str | None = None) -> None:
        self.setProperty("capturing", self.capturing)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(text or key_label(self.value))

    def _start(self) -> None:
        self.capturing = True
        self.capturing_changed.emit(True)      # Vocalis suspend ses raccourcis
        self._refresh("Appuie sur une touche…")
        self.setFocus()
        self.grabKeyboard()

    def _stop(self) -> None:
        self.capturing = False
        self.releaseKeyboard()
        self._refresh()
        self.capturing_changed.emit(False)     # … et les réactive

    def keyPressEvent(self, e) -> None:
        if not self.capturing:
            return super().keyPressEvent(e)
        mods = []
        m = e.modifiers()
        if m & Qt.ControlModifier:
            mods.append("ctrl")
        if m & Qt.AltModifier:
            mods.append("alt")
        if m & Qt.ShiftModifier:
            mods.append("shift")
        if m & Qt.MetaModifier:
            mods.append("windows")
        if int(e.key()) in _MODIFIER_KEYS:
            self._refresh(key_label("+".join(mods)) + " + …")
            return
        name = _qt_key_name(e)
        if not name:
            return
        self.value = "+".join(mods + [name])
        self._stop()
        self.changed.emit(self.value)

    def focusOutEvent(self, e) -> None:
        if self.capturing:
            self._stop()
        super().focusOutEvent(e)


# =========================================================================== petits éléments
def card(title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(10)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    lay.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setObjectName("hint")
        h.setWordWrap(True)
        lay.addWidget(h)
    return frame, lay


def hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(True)
    return lbl


def row(*widgets, stretch: bool = True) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(8)
    for w in widgets:
        if isinstance(w, int):
            lay.addSpacing(w)
        else:
            lay.addWidget(w)
    if stretch:
        lay.addStretch(1)
    return lay


def dot(state: str) -> QLabel:
    colors = {"ok": "#1F9D55", "warn": "#D98E04", "ko": "#D93036", "wait": "#A0A0A5"}
    lbl = QLabel("●")
    lbl.setStyleSheet(f"color: {colors.get(state, '#A0A0A5')}; font-size: 14pt;")
    return lbl


def combo_set(combo: QComboBox, value: str) -> None:
    idx = combo.findData(value)
    if idx < 0 and combo.isEditable():
        combo.setEditText(value)
    elif idx < 0:
        combo.addItem(value, value)
        combo.setCurrentIndex(combo.count() - 1)
    else:
        combo.setCurrentIndex(idx)


def combo_get(combo: QComboBox) -> str:
    if combo.isEditable():
        text = combo.currentText().strip()
        idx = combo.findText(text)
        return combo.itemData(idx) if idx >= 0 and combo.itemData(idx) else text
    return combo.currentData()


def python_check_voices(langs: list[str]):
    """Étape « voix » : télécharge la voix Piper de secours des nouvelles langues
    (s'il en existe une) ; les voix en ligne n'ont rien à télécharger."""
    def run(report):
        from . import voices as V
        for lang in langs:
            v = V.from_config(V.DEFAULT.get(lang, ""), lang) if lang in V.DEFAULT else None
            if v is None:
                report(f"{lang_name(lang)} : lecture avec la voix multilingue")
            elif V.installed(v):
                report(f"{lang_name(lang)} : voix {v.name} prête")
            else:
                V.download(v, lambda pct: report(f"Voix {v.name} : {pct} %"))
                report(f"{lang_name(lang)} : voix {v.name} prête")
    return run


# =========================================================================== vérifications
def check_components(cfg: dict) -> list[tuple[str, str, str]]:
    """(nom, état ok/warn/ko, détail) pour chaque composant."""
    out = []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    engine = resolve_engine(cfg)
    # carte graphique
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True,
                           timeout=10, creationflags=flags)
        name, driver, mem = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        ok = int(driver.split(".")[0]) >= 570
        out.append(("Carte graphique", "ok" if ok else "warn",
                    f"{name} · {mem} · pilote {driver}" +
                    ("" if ok else " — mets le pilote à jour (570 ou plus)")))
    except Exception:
        out.append(("Carte graphique", "warn" if engine == "whisper" else "ok",
                    "Aucune carte NVIDIA : utile seulement pour Whisper" if engine != "whisper"
                    else "Aucune carte NVIDIA : Whisper sera lent, choisis plutôt Parakeet"))
    if engine == "parakeet":
        from . import parakeet
        ok = parakeet.installed()
        out.append(("Dictée : Parakeet", "ok" if ok else "warn",
                    "Modèle téléchargé" if ok else
                    f"Pas encore téléchargé ({parakeet.SIZE_MB} Mo) : il le sera au prochain "
                    "démarrage"))
    elif engine == "windows":
        try:
            from .windows_stt import pick_tag, supported_tags
            tags = supported_tags()
            have = [l for l in active_languages(cfg) if pick_tag(l, tags)]
            out.append(("Dictée : Windows (en ligne)", "ok" if have else "ko",
                        "Langues : " + ", ".join(tags) if tags else
                        "Aucune langue de dictée installée dans Windows"))
        except Exception as exc:
            out.append(("Dictée : Windows (en ligne)", "ko", f"Indisponible : {exc}"))
    # Whisper
    model = cfg["dictee"]["modele_whisper"]
    if not has_nvidia_gpu() and cfg["dictee"]["appareil"] == "cuda":
        model = cfg["dictee"].get("modele_secours_cpu", "small")
    present = MODELS_DIR.exists() and any(p.name.endswith(f"faster-whisper-{model}")
                                          for p in MODELS_DIR.iterdir())
    if engine == "whisper":
        out.append((f"Dictée : Whisper ({model})", "ok" if present else "warn",
                    "Modèle téléchargé" if present else
                    "Pas encore téléchargé : il le sera au prochain démarrage"))
    # Ollama
    c = cfg["nettoyage"]
    if not c["actif"]:
        out.append(("Nettoyage du texte", "wait", "Désactivé"))
    else:
        try:
            tags = requests.get(c["url"].rstrip("/") + "/api/tags", timeout=3).json()
            names = [m["name"] for m in tags.get("models", [])]
            has = any(n == c["modele"] or n == c["modele"] + ":latest" for n in names)
            out.append(("Nettoyage du texte", "ok" if has else "ko",
                        f"Ollama actif · modèle {c['modele']} " +
                        ("prêt" if has else "absent : télécharge-le dans l'onglet Nettoyage")))
        except Exception:
            out.append(("Nettoyage du texte", "ko", "Ollama ne répond pas : lance Ollama "
                                                    "ou relance l'installateur"))
    # assistant « Demander »
    try:
        from .assistant import assistant_model, model_present, ollama_up
        model = assistant_model(cfg)
        url = c["url"].rstrip("/")
        if not ollama_up(url):
            out.append(("Assistant « Demander »", "warn",
                        "Ollama ne répond pas : bouton « Réparer » ci-dessous"))
        elif model_present(url, model):
            out.append(("Assistant « Demander »", "ok", f"Modèle {model} prêt"))
        else:
            out.append(("Assistant « Demander »", "warn",
                        f"Modèle {model} absent : bouton « Réparer » ci-dessous"))
    except Exception as exc:
        out.append(("Assistant « Demander »", "warn", str(exc)))
    # voix
    from . import voices as V
    for lang in active_languages(cfg):
        if lang not in V.DEFAULT:
            continue
        label = f"Voix : {lang_name(lang).lower()}"
        v = V.from_config(cfg["lecture"].get(f"voix_{lang}", ""), lang)
        ok = V.installed(v)
        out.append((label, "ok" if ok else "ko",
                    f"{v.name} ({v.gender}, {v.accent})" if ok else
                    f"{v.name} pas encore téléchargée : choisis-la dans l'onglet Lecture"))
    # OCR
    try:
        from . import ocr
        langs = ocr.available_languages()
        missing = [lang_name(pre).lower() for pre in active_languages(cfg)
                   if not any(l.lower().startswith(pre) for l in langs)]
        out.append(("Lecture d'écran (OCR)", "warn" if missing else "ok",
                    ("Manque : " + ", ".join(missing) + " — bouton « Réparer » ci-dessous")
                    if missing else ", ".join(langs)))
    except Exception as exc:
        out.append(("Lecture d'écran (OCR)", "ko", str(exc)))
    # micro
    try:
        import sounddevice as sd
        out.append(("Micro", "ok", sd.query_devices(kind="input")["name"]))
    except Exception:
        out.append(("Micro", "ko", "Aucun micro détecté"))
    return out


def input_devices() -> list[str]:
    try:
        import sounddevice as sd
        default = sd.query_devices(kind="input")
        api = default["hostapi"]
        names = []
        for dev in sd.query_devices():
            if dev["max_input_channels"] > 0 and dev["hostapi"] == api \
                    and dev["name"] not in names:
                names.append(dev["name"])
        return names
    except Exception:
        return []


# =========================================================================== fenêtre
class SettingsWindow(QWidget):
    saved = Signal()
    closedWithoutSave = Signal()
    captureChanged = Signal(bool)
    checkUpdates = Signal()
    restartRequested = Signal()

    def __init__(self):
        super().__init__(None, Qt.Window)
        self.setObjectName("settings")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setWindowTitle("Vocalis — Paramètres")
        self.setWindowIcon(make_icon())
        self.setStyleSheet(STYLE)
        self.resize(940, 680)
        self.cfg = load_config()
        self.original = copy.deepcopy(self.cfg)
        self._saved = False
        self._mic_stream = None
        self._mic_level = 0.0

        nav = self.nav = QListWidget()
        nav.setObjectName("nav")
        nav.setFixedWidth(200)
        self.stack = QStackedWidget()
        pages = [("État", self._page_state), ("Raccourcis", self._page_hotkeys),
                 ("Dictée", self._page_dictation), ("Dictionnaire", self._page_dictionary),
                 ("Nettoyage", self._page_cleaning),
                 ("Lecture", self._page_reading), ("Général", self._page_general)]
        self.page_index = {}
        for name, builder in pages:
            self.page_index[name] = nav.count()
            QListWidgetItem(name, nav)
            self.stack.addWidget(self._scroll(builder()))
        nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        nav.setCurrentRow(0)

        self.lbl_footer = QLabel("")
        self.lbl_footer.setObjectName("hint")
        btn_defaults = QPushButton("Valeurs par défaut")
        btn_defaults.clicked.connect(self._defaults)
        btn_cancel = QPushButton("Annuler")
        btn_cancel.clicked.connect(self.close)
        btn_save = QPushButton("Enregistrer")
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self._save)
        footer = QHBoxLayout()
        footer.setContentsMargins(20, 10, 20, 14)
        footer.addWidget(self.lbl_footer, 1)
        footer.addWidget(btn_defaults)
        footer.addWidget(btn_cancel)
        footer.addWidget(btn_save)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.stack, 1)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #E3E3E8;")
        right.addWidget(line)
        right.addLayout(footer)
        main = QHBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(nav)
        main.addLayout(right, 1)

        self._load(self.cfg)
        self._refresh_state()
        self._refresh_ollama_models()

    def show_page(self, page: str) -> None:
        """Ouvre une page (« voix » : Lecture, sur le choix des narrateurs ;
        « dictionnaire », « prononciation »)."""
        if page in ("dictionnaire", "prononciation"):
            i = self.page_index["Dictionnaire"]
            self.nav.setCurrentRow(i)
            if page == "prononciation":
                area = self.stack.widget(i)
                QTimer.singleShot(50, lambda: area.ensureWidgetVisible(self.pron_frame, 0, 20))
            return
        if page == "voix":
            i = self.page_index["Lecture"]
            self.nav.setCurrentRow(i)
            area = self.stack.widget(i)
            QTimer.singleShot(50, lambda: area.ensureWidgetVisible(self.voice_frame, 0, 20))
            self.voice_frame.setStyleSheet("QFrame#card { border: 2px solid #4DA3FF; }")
            QTimer.singleShot(2500, lambda: self.voice_frame.setStyleSheet(""))

    # ------------------------------------------------------------------ utilitaires
    def _scroll(self, page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(page)
        return area

    def _page(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        w.setStyleSheet("background: #F4F4F6;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(14)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        lay.addWidget(t)
        if subtitle:
            lay.addWidget(hint(subtitle))
        return w, lay

    # ------------------------------------------------------------------ page État
    def _page_state(self) -> QWidget:
        w, lay = self._page("État", "Vérifie que chaque composant est installé et prêt.")
        frame, c = card("Composants")
        self.state_grid = QGridLayout()
        self.state_grid.setHorizontalSpacing(12)
        self.state_grid.setVerticalSpacing(10)
        self.state_grid.setColumnStretch(2, 1)
        c.addLayout(self.state_grid)
        btn_refresh = QPushButton("Revérifier")
        btn_refresh.clicked.connect(self._refresh_state)
        btn_install = self.btn_repair = QPushButton("Réparer")
        btn_install.setToolTip("Retélécharge ce qui manque (modèles, voix, lecture d'écran, "
                               "assistant)")
        btn_install.clicked.connect(self._repair)
        self.lbl_repair = QLabel("")
        self.lbl_repair.setStyleSheet(f"color: {ACCENT}; font-weight: 600;")
        c.addLayout(row(btn_refresh, btn_install, self.lbl_repair))
        lay.addWidget(frame)

        frame2, c2 = card("Comment l'utiliser")
        self.lbl_usage = QLabel()
        self.lbl_usage.setWordWrap(True)
        self.lbl_usage.setTextFormat(Qt.RichText)
        c2.addWidget(self.lbl_usage)
        lay.addWidget(frame2)
        lay.addStretch(1)
        return w

    def _refresh_state(self) -> None:
        while self.state_grid.count():
            item = self.state_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.state_grid.addWidget(dot("wait"), 0, 0)
        self.state_grid.addWidget(QLabel("Vérification en cours…"), 0, 1, 1, 2)
        cfg = self._collect()

        def show(result):
            while self.state_grid.count():
                item = self.state_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if isinstance(result, Exception):
                self.state_grid.addWidget(dot("ko"), 0, 0)
                self.state_grid.addWidget(QLabel(f"Vérification impossible : {result}"), 0, 1)
                return
            needs_repair = False
            for i, (name, state, detail) in enumerate(result):
                self.state_grid.addWidget(dot(state), i, 0)
                lbl = QLabel(name)
                lbl.setStyleSheet("font-weight: 600;")
                self.state_grid.addWidget(lbl, i, 1)
                if "Réparer" in detail:
                    needs_repair = True
                    import html as _html
                    detail = _html.escape(detail).replace(
                        "« Réparer »", "Réparer").replace(
                        "Réparer", f"<b style='color:{ACCENT}'>« Réparer »</b>")
                d = QLabel(detail)
                d.setTextFormat(Qt.RichText if "<b" in detail else Qt.PlainText)
                d.setWordWrap(True)
                d.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self.state_grid.addWidget(d, i, 2)

            self._show_repair(needs_repair)

        run_bg(lambda: check_components(cfg), show)

    def _show_repair(self, needed: bool) -> None:
        """Quelque chose manque : le bouton Réparer passe en rouge vif."""
        self.btn_repair.setText("⚠  Réparer" if needed else "Réparer")
        self.btn_repair.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: white; border: none; "
            "font-weight: 700; padding: 7px 16px; }"
            "QPushButton:hover { background: #D33C41; }" if needed else "")
        self.lbl_repair.setText("← un composant manque, clique ici" if needed else "")
        item = self.nav.item(self.page_index["État"])
        item.setText("État  ⚠" if needed else "État")
        item.setForeground(QColor(ACCENT) if needed else QColor("#1D1D1F"))

    def _update_usage(self) -> None:
        k = {key: btn.value for key, btn in self.hotkey_buttons.items()}
        self.lbl_usage.setText(
            f"<b>{key_label(k['dictee_bascule'])}</b> : commencer / finir une dictée. Le texte "
            f"s'affiche en direct, puis il est corrigé et collé à la fin.<br>"
            f"<b>{key_label(k['lecture'])}</b> : lire le texte sélectionné ; sans sélection, "
            f"clique sur un paragraphe (ou trace un rectangle).<br>"
            f"<b>{key_label(k['arreter_lecture'])}</b> : arrêter la lecture. Pendant la lecture, "
            f"<b>Alt + clic</b> reprend au paragraphe cliqué.<br>"
            f"<b>{key_label(k['demander'])}</b> : poser une question à voix haute sur le texte "
            f"sélectionné (ou cliquer sur Résumer, Expliquer…) ; la réponse est affichée et lue.")

    # ------------------------------------------------------------------ page Raccourcis
    def _page_hotkeys(self) -> QWidget:
        w, lay = self._page("Raccourcis", "Clique sur un bouton puis appuie sur la touche "
                                           "ou la combinaison voulue (par ex. Ctrl + Alt + Maj + K).")
        frame, c = card("Touches")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        self.hotkey_buttons: dict[str, HotkeyButton] = {}
        for i, (key, label, desc) in enumerate(HOTKEYS):
            box = QVBoxLayout()
            box.setSpacing(1)
            t = QLabel(label)
            t.setStyleSheet("font-weight: 600;")
            box.addWidget(t)
            box.addWidget(hint(desc))
            grid.addLayout(box, i, 0)
            btn = HotkeyButton("")
            btn.changed.connect(self._hotkeys_changed)
            btn.capturing_changed.connect(self.captureChanged.emit)
            self.hotkey_buttons[key] = btn
            grid.addWidget(btn, i, 2)
            reset = QPushButton("Défaut")
            reset.setToolTip(key_label(DEFAULTS["raccourcis"][key]))
            reset.clicked.connect(lambda _=False, k=key: (
                self.hotkey_buttons[k].set_value(DEFAULTS["raccourcis"][k]),
                self._hotkeys_changed()))
            grid.addWidget(reset, i, 3)
        c.addLayout(grid)
        self.lbl_hotkey_warn = QLabel("")
        self.lbl_hotkey_warn.setStyleSheet(f"color: {ACCENT};")
        self.lbl_hotkey_warn.setWordWrap(True)
        c.addWidget(self.lbl_hotkey_warn)
        lay.addWidget(frame)
        lay.addWidget(hint("Conseil : Ctrl + Alt + Maj + une lettre n'est utilisé par presque "
                           "aucun logiciel. Évite Ctrl + Alt + E (€ sur clavier AZERTY) et "
                           "Ctrl + Maj seul, très utilisé par les navigateurs. Les raccourcis "
                           "sont actifs après l'enregistrement."))
        lay.addStretch(1)
        return w

    def _hotkeys_changed(self, *_):
        values = [b.value for b in self.hotkey_buttons.values()]
        dup = {v for v in values if v and values.count(v) > 1}
        self.lbl_hotkey_warn.setText(
            ("Attention : " + ", ".join(key_label(d) for d in dup) +
             " est utilisé deux fois.") if dup else "")
        self._update_usage()

    # ------------------------------------------------------------------ page Dictée
    def _page_dictation(self) -> QWidget:
        w, lay = self._page("Dictée")

        frame, c = card("Moteur de reconnaissance",
                        "Tu peux aussi changer de moteur sans ouvrir cette fenêtre : clic "
                        "droit sur l'icône > Moteur de dictée.")
        self.cmb_engine = QComboBox()
        self.cmb_engine.addItem("Automatique : Whisper avec une carte NVIDIA, sinon Parakeet",
                                "auto")
        for key, label in ENGINES.items():
            self.cmb_engine.addItem(label, key)
        self.cmb_engine.currentIndexChanged.connect(lambda _: self._update_engine_hint())
        c.addLayout(row(QLabel("Moteur :"), self.cmb_engine))
        self.lbl_engine = hint("")
        c.addWidget(self.lbl_engine)
        lay.addWidget(frame)

        frame, c = card("Langues", "Une dictée = une langue. Le badge de la pastille passe "
                                   "d'une langue cochée à l'autre.")
        self.chk_lang = {code: QCheckBox(lang_name(code)) for code in ALL_LANGS}
        grid_l = QGridLayout()
        grid_l.setHorizontalSpacing(18)
        for i, chk in enumerate(self.chk_lang.values()):
            grid_l.addWidget(chk, i // 4, i % 4)
        c.addLayout(grid_l)
        c.addWidget(hint("Coche une nouvelle langue puis Enregistrer : Vocalis prépare tout "
                         "seul la dictée, la traduction, les voix et la lecture d'écran, puis "
                         "redémarre."))
        self.cmb_forced = QComboBox()
        for code in ALL_LANGS:
            self.cmb_forced.addItem(lang_name(code), code)
        self.cmb_forced.addItem("Aucune (détection automatique)", "")
        c.addLayout(row(QLabel("Langue de départ :"), self.cmb_forced))
        c.addWidget(hint("C'est la langue affichée sur la pastille pendant la dictée : clique "
                         "dessus pour en changer. L'outil ne passe à une autre langue que s'il "
                         "en est sûr à 85 % ; le badge devient alors orange."))
        lay.addWidget(frame)

        frame, c = card("Micro")
        self.cmb_mic = QComboBox()
        self.cmb_mic.addItem("Micro par défaut de Windows", "")
        for name in input_devices():
            self.cmb_mic.addItem(name, name)
        self.cmb_mic.setMinimumWidth(320)
        self.btn_mic = QPushButton("Tester le micro")
        self.btn_mic.clicked.connect(self._toggle_mic_test)
        c.addLayout(row(self.cmb_mic, self.btn_mic))
        self.bar_mic = QProgressBar()
        self.bar_mic.setRange(0, 100)
        self.bar_mic.setTextVisible(False)
        self.bar_mic.setFixedHeight(10)
        c.addWidget(self.bar_mic)
        self.lbl_mic = hint("Parle pendant le test : la barre doit bouger.")
        c.addWidget(self.lbl_mic)
        self._mic_timer = QTimer(self)
        self._mic_timer.timeout.connect(self._mic_tick)
        lay.addWidget(frame)

        frame, c = card("Écriture")
        self.chk_live = QCheckBox("Afficher le texte en direct dans la fenêtre de dictée")
        self.chk_replace = QCheckBox("À la fin, corriger tout le texte (ponctuation, « euh », "
                                     "cohérence) avec le modèle de nettoyage")
        c.addWidget(self.chk_live)
        c.addWidget(self.chk_replace)
        self.sld_interval = QSlider(Qt.Horizontal)
        self.sld_interval.setRange(2, 15)
        self.lbl_interval = QLabel()
        self.sld_interval.valueChanged.connect(
            lambda v: self.lbl_interval.setText(f"{v / 10:.1f} s"))
        self.sld_interval.setFixedWidth(220)
        c.addLayout(row(QLabel("Mise à jour du texte en direct toutes les"),
                        self.sld_interval, self.lbl_interval))
        c.addWidget(hint("Pendant la dictée, rien n'est écrit dans ta case : la fenêtre de "
                         "dictée montre la phrase en cours (blanc = sûr, gris = provisoire) et "
                         "les phrases validées en dessous. À l'arrêt, tout l'enregistrement est "
                         "retranscrit, corrigé, puis collé à l'endroit du curseur."))
        lay.addWidget(frame)

        frame, c = card("Vocabulaire")
        c.addWidget(hint("Les noms propres et mots techniques se règlent dans la page "
                         "« Dictionnaire »."))
        btn = QPushButton("Ouvrir le dictionnaire")
        btn.clicked.connect(lambda: self.show_page("dictionnaire"))
        c.addLayout(row(btn))
        lay.addWidget(frame)

        frame, c = card("Réglages de Whisper")
        self.cmb_whisper = QComboBox()
        for code, name in WHISPER_MODELS:
            self.cmb_whisper.addItem(name, code)
        self.cmb_device = QComboBox()
        self.cmb_device.addItem("Carte graphique (recommandé)", "cuda")
        self.cmb_device.addItem("Processeur (lent)", "cpu")
        c.addLayout(row(QLabel("Modèle :"), self.cmb_whisper))
        c.addLayout(row(QLabel("Calcul sur :"), self.cmb_device))
        c.addWidget(hint("Un nouveau modèle est téléchargé automatiquement au redémarrage."))
        lay.addWidget(frame)
        lay.addStretch(1)
        return w

    ENGINE_HINTS = {
        "whisper": "Le plus précis. Rapide avec une carte graphique NVIDIA, très lent sans. "
                   "Tout reste sur ce PC.",
        "parakeet": "Rapide sur n'importe quel processeur, ponctuation incluse. Tout reste sur "
                    "ce PC. Téléchargé au premier usage (465 Mo). Devine la langue tout seul ; "
                    "le dictionnaire sert seulement à corriger le texte final.",
        "windows": "La reconnaissance en ligne de Microsoft (celle de Win+H) : rien à "
                   "télécharger, mais le texte part chez Microsoft. Il faut Internet et "
                   "« Reconnaissance vocale en ligne » activée (Paramètres Windows > "
                   "Confidentialité et sécurité > Voix). Utilise le micro par défaut de "
                   "Windows ; la langue se choisit avant de commencer.",
    }

    def _update_engine_hint(self) -> None:
        key = combo_get(self.cmb_engine)
        if key == "auto":
            real = "whisper" if has_nvidia_gpu() else "parakeet"
            text = f"Sur ce PC : {ENGINES[real]}. " + self.ENGINE_HINTS[real]
        else:
            text = self.ENGINE_HINTS[key]
        self.lbl_engine.setText(text)

    def _toggle_mic_test(self) -> None:
        if self._mic_stream is not None:
            self._stop_mic()
            return
        try:
            import numpy as np
            import sounddevice as sd

            from .dictation import find_input_device
            device = find_input_device(combo_get(self.cmb_mic) or "")

            def cb(indata, frames, t, status):
                rms = float(np.sqrt(np.mean(indata[:, 0] ** 2)))
                self._mic_level = min(1.0, rms * 12)

            self._mic_stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                                              device=device, callback=cb)
            self._mic_stream.start()
            self._mic_peak = 0.0
            self._mic_t0 = time.monotonic()
            self.btn_mic.setText("Arrêter le test")
            self._mic_timer.start(40)
        except Exception as exc:
            self.lbl_mic.setText(f"Micro inaccessible : {exc}")

    def _mic_tick(self) -> None:
        self.bar_mic.setValue(int(self._mic_level * 100))
        self._mic_peak = max(self._mic_peak, self._mic_level)
        if time.monotonic() - self._mic_t0 > 8:
            self._stop_mic()

    def _stop_mic(self) -> None:
        self._mic_timer.stop()
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
        self._mic_stream = None
        self.bar_mic.setValue(0)
        self.btn_mic.setText("Tester le micro")
        if getattr(self, "_mic_peak", 0) > 0.08:
            self.lbl_mic.setText("✔ Le micro fonctionne.")
        elif hasattr(self, "_mic_peak"):
            self.lbl_mic.setText("Son très faible : vérifie le micro choisi et son volume "
                                 "dans les paramètres son de Windows.")

    # ------------------------------------------------------------------ page Nettoyage
    # ------------------------------------------------------------------ page Dictionnaire
    def _page_dictionary(self) -> QWidget:
        w, lay = self._page("Dictionnaire", "Tes mots à toi : comment les écrire en "
                            "dictée, et comment la voix doit les prononcer en lecture.")
        frame, c = card("Mots de la dictée", "Noms, marques, mots techniques… Vocalis les "
                        "écrit toujours comme ici. Si la reconnaissance entend autre chose, "
                        "note-le dans la deuxième colonne (plusieurs possibilités séparées "
                        "par des virgules).")
        t = self.tbl_words = self._table(["Écrire ainsi", "Souvent entendu comme (facultatif)",
                                          ""])
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        t.setColumnWidth(2, 80)
        c.addWidget(t)
        add = QPushButton("+ Ajouter un mot")
        add.clicked.connect(lambda: self._add_row(self.tbl_words, ["", "", ""]))
        rem = QPushButton("Supprimer")
        rem.clicked.connect(lambda: self._remove_rows(self.tbl_words))
        c.addLayout(row(add, rem))
        self.chk_learn = QCheckBox("Apprendre de mes corrections")
        c.addWidget(self.chk_learn)
        c.addWidget(hint("Juste après une dictée, si tu corriges à la main un nom ou un mot "
                         "technique mal écrit, Vocalis le retient (une notification "
                         "s'affiche ; ces mots sont marqués « appris »). Tout reste sur ce PC."))
        lay.addWidget(frame)

        frame, c = card("Prononciation", "Pour que la voix dise bien un sigle, un nom "
                        "étranger ou un mot technique. Écris la prononciation comme elle se "
                        "lit, par exemple « SNCF » → « èsse ène cé èfe ».")
        self.pron_frame = frame
        t = self.tbl_pron = self._table(["Mot écrit", "Prononcer comme", "Langue"])
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        t.setColumnWidth(2, 130)
        c.addWidget(t)
        add = QPushButton("+ Ajouter une prononciation")
        add.clicked.connect(lambda: self._add_pron("", "", ""))
        rem = QPushButton("Supprimer")
        rem.clicked.connect(lambda: self._remove_rows(self.tbl_pron))
        self.btn_pron_test = QPushButton("▶ Écouter")
        self.btn_pron_test.clicked.connect(self._test_pron)
        self.lbl_pron = QLabel("")
        self.lbl_pron.setObjectName("hint")
        c.addLayout(row(add, rem, self.btn_pron_test, self.lbl_pron))
        c.addWidget(hint("Les sigles écrits en majuscules (« US ») ne changent pas le mot "
                         "en minuscules (« us »). La langue « Toutes » s'applique à chaque "
                         "lecture."))
        lay.addWidget(frame)
        lay.addStretch(1)
        self._load_dictionary(dictionary.load())
        self.dict_original = self._collect_dictionary()
        return w

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.AllEditTriggers)
        t.setMinimumHeight(190)
        t.setStyleSheet("QTableWidget { background: white; border: 1px solid #E3E3E8; "
                        "border-radius: 6px; gridline-color: #EEEEF1; }")
        return t

    @staticmethod
    def _add_row(t: QTableWidget, values: list[str], focus: bool = True) -> int:
        r = t.rowCount()
        t.insertRow(r)
        for col, v in enumerate(values):
            item = QTableWidgetItem(v)
            if col == 2 and t.columnCount() == 3 and t.cellWidget(r, 2) is None \
                    and t.horizontalHeaderItem(2).text() == "":
                item.setFlags(Qt.ItemIsEnabled)          # colonne « appris »
                item.setForeground(QColor("#6E6E73"))
            t.setItem(r, col, item)
        if focus:
            t.setCurrentCell(r, 0)
            t.editItem(t.item(r, 0))
        return r

    def _add_pron(self, ecrit: str, dit: str, langue: str, focus: bool = True) -> None:
        t = self.tbl_pron
        r = self._add_row(t, [ecrit, dit], focus)
        cmb = QComboBox()
        cmb.addItem("Toutes", "")
        for code in ALL_LANGS:
            cmb.addItem(lang_name(code), code)
        combo_set(cmb, langue)
        t.setCellWidget(r, 2, cmb)

    @staticmethod
    def _remove_rows(t: QTableWidget) -> None:
        rows = sorted({i.row() for i in t.selectedIndexes()} or
                      ({t.currentRow()} if t.currentRow() >= 0 else set()), reverse=True)
        for r in rows:
            t.removeRow(r)

    @staticmethod
    def _cell(t: QTableWidget, r: int, c: int) -> str:
        item = t.item(r, c)
        return item.text().strip() if item else ""

    def _load_dictionary(self, data: dict) -> None:
        self.tbl_words.setRowCount(0)
        for e in data["dictee"]:
            self._add_row(self.tbl_words, [e["mot"], ", ".join(e["entendu"]),
                                           "appris" if e.get("appris") else ""], False)
        self.tbl_pron.setRowCount(0)
        for e in data["prononciation"]:
            self._add_pron(e["ecrit"], e["dit"], e.get("langue", ""), False)
        self.chk_learn.setChecked(bool(data.get("apprendre", True)))

    def _collect_dictionary(self) -> dict:
        data = dictionary.empty()
        data["apprendre"] = self.chk_learn.isChecked()
        t = self.tbl_words
        for r in range(t.rowCount()):
            mot = self._cell(t, r, 0)
            if mot:
                data["dictee"].append({
                    "mot": mot,
                    "entendu": [x.strip() for x in self._cell(t, r, 1).split(",") if x.strip()],
                    "appris": self._cell(t, r, 2) == "appris"})
        t = self.tbl_pron
        for r in range(t.rowCount()):
            ecrit, dit = self._cell(t, r, 0), self._cell(t, r, 1)
            if ecrit and dit:
                cmb = t.cellWidget(r, 2)
                data["prononciation"].append({"ecrit": ecrit, "dit": dit,
                                              "langue": combo_get(cmb) if cmb else ""})
        return dictionary._clean(data)

    def _test_pron(self) -> None:
        """Lit le mot de la ligne choisie avec sa prononciation."""
        from .tts import Tts
        t = self.tbl_pron
        r = t.currentRow()
        if r < 0 or not self._cell(t, r, 1):
            self.lbl_pron.setText("Choisis une ligne remplie.")
            return
        cmb = t.cellWidget(r, 2)
        lang = (combo_get(cmb) if cmb else "") or (active_languages(self.cfg) or ["fr"])[0]
        said = self._cell(t, r, 1)
        cfg = self._collect()
        self.btn_pron_test.setEnabled(False)
        self.lbl_pron.setText("Lecture…")

        def speak():
            import sounddevice as sd
            speech = Tts(cfg)._synth_raw(said, lang, 1.0)
            sd.play(speech.audio, speech.sample_rate)
            sd.wait()

        def done(res):
            self.btn_pron_test.setEnabled(True)
            self.lbl_pron.setText(f"Erreur : {res}" if isinstance(res, Exception) else "")

        run_bg(speak, done)

    def _dictionary_changed(self) -> bool:
        return self._collect_dictionary() != self.dict_original

    def _page_cleaning(self) -> QWidget:
        w, lay = self._page("Nettoyage", "Quand tu as fini de parler, un petit modèle de "
                                         "langage local corrige la ponctuation et enlève les "
                                         "hésitations.")
        frame, c = card("Nettoyage du texte")
        self.chk_clean = QCheckBox("Activer le nettoyage (ponctuation, « euh », reprises)")
        c.addWidget(self.chk_clean)
        self.cmb_clean = QComboBox()
        self.cmb_clean.setEditable(True)
        self.cmb_clean.setMinimumWidth(330)
        for code, name in CLEAN_MODELS:
            self.cmb_clean.addItem(name, code)
        self.cmb_clean.currentIndexChanged.connect(lambda _: self._update_model_status())
        self.cmb_clean.editTextChanged.connect(lambda _: self._update_model_status())
        self.btn_pull = QPushButton("Télécharger")
        self.btn_pull.clicked.connect(self._pull_model)
        c.addLayout(row(QLabel("Modèle :"), self.cmb_clean, self.btn_pull))
        self.lbl_model = hint("")
        c.addWidget(self.lbl_model)
        self.bar_pull = QProgressBar()
        self.bar_pull.setVisible(False)
        c.addWidget(self.bar_pull)
        self.spn_timeout = QSpinBox()
        self.spn_timeout.setRange(5, 120)
        self.spn_timeout.setSuffix(" s")
        c.addLayout(row(QLabel("Délai maximum :"), self.spn_timeout,
                        hint("au-delà, le texte brut est gardé")))
        lay.addWidget(frame)

        frame, c = card("Essayer", "Écris (ou colle) une phrase comme si tu l'avais dictée.")
        self.txt_test = QPlainTextEdit("euh bonjour je voulais je voulais te dire que euh "
                                       "la réunion de demain est à dix heures non plutôt à "
                                       "onze heures")
        self.txt_test.setFixedHeight(70)
        c.addWidget(self.txt_test)
        self.btn_test_clean = QPushButton("Nettoyer ce texte")
        self.btn_test_clean.clicked.connect(self._test_clean)
        c.addLayout(row(self.btn_test_clean))
        self.lbl_clean_out = QLabel("")
        self.lbl_clean_out.setWordWrap(True)
        self.lbl_clean_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_clean_out.setStyleSheet("background: #F7F7F9; border-radius: 6px; "
                                         "padding: 8px;")
        self.lbl_clean_out.setVisible(False)
        c.addWidget(self.lbl_clean_out)
        lay.addWidget(frame)
        lay.addStretch(1)
        self._ollama_models: list[str] | None = None
        return w

    def _refresh_ollama_models(self) -> None:
        url = self.cfg["nettoyage"]["url"].rstrip("/")

        def fetch():
            r = requests.get(url + "/api/tags", timeout=3)
            return [m["name"] for m in r.json().get("models", [])]

        def done(res):
            self._ollama_models = None if isinstance(res, Exception) else res
            if self._ollama_models:
                for name in self._ollama_models:
                    if self.cmb_clean.findData(name) < 0 and \
                            self.cmb_clean.findData(name.removesuffix(":latest")) < 0:
                        self.cmb_clean.addItem(f"{name} (installé)", name)
            self._update_model_status()

        run_bg(fetch, done)

    def _model_installed(self, model: str) -> bool:
        names = self._ollama_models or []
        return model in names or f"{model}:latest" in names

    def _update_model_status(self) -> None:
        model = combo_get(self.cmb_clean) or ""
        if self._ollama_models is None:
            self.lbl_model.setText("Ollama ne répond pas : impossible de vérifier le modèle.")
            self.btn_pull.setEnabled(False)
        elif self._model_installed(model):
            self.lbl_model.setText(f"✔ {model} est installé.")
            self.btn_pull.setEnabled(False)
        else:
            self.lbl_model.setText(f"{model} n'est pas encore téléchargé.")
            self.btn_pull.setEnabled(True)

    def _pull_model(self) -> None:
        import json
        model = combo_get(self.cmb_clean)
        url = self.cfg["nettoyage"]["url"].rstrip("/")
        self.btn_pull.setEnabled(False)
        self.bar_pull.setVisible(True)
        self.bar_pull.setRange(0, 0)

        def pull(report):
            with requests.post(url + "/api/pull", json={"model": model}, stream=True,
                               timeout=3600) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    report(msg)
            return True

        def progress(msg):
            if msg.get("total"):
                self.bar_pull.setRange(0, 100)
                self.bar_pull.setValue(int(msg.get("completed", 0) * 100 / msg["total"]))
            self.lbl_model.setText(f"Téléchargement de {model} : {msg.get('status', '')}")

        def done(res):
            self.bar_pull.setVisible(False)
            if isinstance(res, Exception):
                self.lbl_model.setText(f"Échec du téléchargement : {res}")
                self.btn_pull.setEnabled(True)
            else:
                self._refresh_ollama_models()

        run_bg(pull, done, progress)

    def _test_clean(self) -> None:
        from .cleaner import Cleaner
        cfg = self._collect()
        cfg["nettoyage"]["actif"] = True
        cleaner = Cleaner(cfg)
        text = self.txt_test.toPlainText()
        self.btn_test_clean.setEnabled(False)
        self.lbl_clean_out.setVisible(True)
        self.lbl_clean_out.setText("Nettoyage en cours… (le premier essai charge le modèle, "
                                   "quelques secondes)")
        t0 = time.monotonic()

        def done(res):
            self.btn_test_clean.setEnabled(True)
            if isinstance(res, Exception):
                self.lbl_clean_out.setText(f"Erreur : {res}")
            elif res.strip() == text.strip():
                self.lbl_clean_out.setText("Pas de résultat : vérifie qu'Ollama est lancé et "
                                           "que le modèle est téléchargé.")
            else:
                self.lbl_clean_out.setText(f"{res}\n\n({time.monotonic() - t0:.1f} s)")

        run_bg(lambda: cleaner.clean(text, None), done)

    # ------------------------------------------------------------------ page Lecture
    def _page_reading(self) -> QWidget:
        w, lay = self._page("Lecture à voix haute")
        frame, c = card("Vitesse", "Vitesse au démarrage d'une lecture. Elle se règle aussi "
                                   "pendant la lecture avec les boutons − et +.")
        self.sld_speed = QSlider(Qt.Horizontal)
        self.sld_speed.setRange(5, 25)
        self.sld_speed.setFixedWidth(300)
        self.lbl_speed = QLabel()
        self.lbl_speed.setStyleSheet("font-weight: 600;")
        self.sld_speed.valueChanged.connect(lambda v: self.lbl_speed.setText(f"{v / 10:.1f}×"))
        c.addLayout(row(QLabel("Lente"), self.sld_speed, QLabel("Rapide"), 12, self.lbl_speed))
        self.sld_pause = QSlider(Qt.Horizontal)
        self.sld_pause.setRange(2, 30)            # centièmes de seconde x 2 : 0,04 à 0,60 s
        self.sld_pause.setFixedWidth(300)
        self.lbl_pause = QLabel()
        self.lbl_pause.setStyleSheet("font-weight: 600;")
        self.sld_pause.valueChanged.connect(
            lambda v: self.lbl_pause.setText(f"{v * 0.02:.2f} s"))
        c.addLayout(row(QLabel("Pause après un point"), self.sld_pause, 12, self.lbl_pause))
        c.addWidget(hint("Durée maximale des silences entre les phrases et les paragraphes "
                         "(à vitesse normale ; plus courte quand la lecture est plus rapide)."))
        lay.addWidget(frame)

        frame, c = card("Pendant la lecture")
        self.chk_click_stop = QCheckBox("Un clic n'importe où arrête la lecture ; un "
                                        "double-clic sur un mot la reprend à ce mot")
        c.addWidget(self.chk_click_stop)
        c.addWidget(hint("Alt + clic sur un paragraphe reprend aussi la lecture à cet "
                         "endroit. Le badge de langue de la barre de lecture choisit la "
                         "langue d'écoute (traduite si besoin)."))
        lay.addWidget(frame)

        frame, c = card("Voix", "En ligne (Microsoft Edge) : les plus naturelles, gratuites, "
                                "Internet nécessaire (sans connexion, Siwis ou Tugão prend le "
                                "relais). Piper : sur ce PC, téléchargées à la demande "
                                "(environ 60 Mo). Windows : déjà installées, plus robotiques.")
        from . import voices as V
        self.cmb_voice: dict[str, QComboBox] = {}
        self.btn_voice: dict[str, QPushButton] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        langs_v = [l for l in active_languages(self.cfg) if l in V.DEFAULT]
        for r, (lang, title) in enumerate((l, lang_name(l)) for l in langs_v):
            combo = QComboBox()
            combo.setMinimumWidth(380)
            self.cmb_voice[lang] = combo
            play = QPushButton("▶  Écouter")
            play.clicked.connect(lambda _=False, l=lang: self._test_voice(l))
            self.btn_voice[lang] = play
            lbl = QLabel(title)
            lbl.setStyleSheet("font-weight: 600;")
            grid.addWidget(lbl, r, 0)
            grid.addWidget(combo, r, 1)
            grid.addWidget(play, r, 2)
            combo.currentIndexChanged.connect(lambda _=0, l=lang: self._voice_selected(l))
        grid.setColumnStretch(3, 1)
        c.addLayout(grid)
        self.bar_voice = QProgressBar()
        self.bar_voice.setRange(0, 100)
        self.bar_voice.setVisible(False)
        c.addWidget(self.bar_voice)
        self.lbl_voice = hint("")
        c.addWidget(self.lbl_voice)
        btn_win = QPushButton("Ajouter des voix Windows…")
        btn_win.setToolTip("Paramètres Windows > Heure et langue > Voix > Ajouter des voix "
                           "(par ex. portugais du Portugal : Helia)")
        btn_win.clicked.connect(lambda: os.startfile("ms-settings:speech")
                                if hasattr(os, "startfile") else None)
        c.addLayout(row(btn_win, hint("Pour Helia (portugais du Portugal) : Ajouter des "
                                      "voix > Português (Portugal), puis rouvre les "
                                      "paramètres.")))
        self._voice_mod = V
        self._downloading: set[str] = set()
        self.voice_frame = frame
        lay.addWidget(frame)

        frame, c = card("Surlignage du mot lu")
        self.btn_color = QPushButton()
        self.btn_color.setFixedWidth(110)
        self.btn_color.clicked.connect(self._pick_color)
        self.sld_opacity = QSlider(Qt.Horizontal)
        self.sld_opacity.setRange(10, 100)
        self.sld_opacity.setFixedWidth(220)
        self.sld_opacity.valueChanged.connect(lambda _: self._update_preview())
        c.addLayout(row(QLabel("Couleur :"), self.btn_color, 20, QLabel("Intensité :"),
                        self.sld_opacity))
        self.lbl_preview = QLabel()
        self.lbl_preview.setTextFormat(Qt.RichText)
        self.lbl_preview.setStyleSheet("font-size: 13pt; padding: 6px 0;")
        c.addWidget(self.lbl_preview)
        lay.addWidget(frame)

        frame, c = card("Fenêtre de lecture (texte sélectionné)")
        self.spn_font = QSpinBox()
        self.spn_font.setRange(10, 40)
        self.spn_font.setSuffix(" pt")
        c.addLayout(row(QLabel("Taille du texte :"), self.spn_font))
        lay.addWidget(frame)
        lay.addStretch(1)
        self._color = "#FFE600"
        return w

    def _pick_color(self) -> None:
        col = QColorDialog.getColor(QColor(self._color), self, "Couleur du surlignage")
        if col.isValid():
            self._color = col.name()
            self._update_preview()

    def _update_preview(self) -> None:
        self.btn_color.setStyleSheet(f"background: {self._color}; border: 1px solid #CFCFD4;")
        self.btn_color.setText("")
        c = QColor(self._color)
        a = self.sld_opacity.value() / 100
        bg = f"rgba({c.red()},{c.green()},{c.blue()},{a:.2f})"
        self.lbl_preview.setText(f"Le mot <span style='background:{bg}'>&nbsp;lu&nbsp;</span> "
                                 f"est surligné ainsi.")

    def _fill_voices(self, cfg: dict) -> None:
        V = self._voice_mod
        for lang, combo in self.cmb_voice.items():
            chosen = V.from_config(cfg["lecture"].get(f"voix_{lang}", ""), lang)
            combo.blockSignals(True)
            combo.clear()
            for v in V.catalog(lang):
                combo.addItem(v.label, v.id)
            idx = combo.findData(chosen.id)
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

    def _voice(self, lang: str):
        return self._voice_mod.find(self.cmb_voice[lang].currentData() or "", lang)

    def _voice_selected(self, lang: str) -> None:
        """Une voix Piper pas encore téléchargée est téléchargée tout de suite."""
        v = self._voice(lang)
        if v is not None and not self._voice_mod.installed(v):
            self._download_voice(v)

    def _download_voice(self, v, then=None) -> None:
        V = self._voice_mod
        if v.id in self._downloading:
            return
        self._downloading.add(v.id)
        self.bar_voice.setVisible(True)
        self.bar_voice.setValue(0)
        self.lbl_voice.setText(f"Téléchargement de la voix {v.name}…")

        def done(res):
            self._downloading.discard(v.id)
            self.bar_voice.setVisible(False)
            if isinstance(res, Exception):
                self.lbl_voice.setText(f"Téléchargement impossible : {res}")
                return
            self.lbl_voice.setText(f"✔ Voix {v.name} prête.")
            for lang, combo in self.cmb_voice.items():     # libellés à jour
                for i in range(combo.count()):
                    item = V.find(combo.itemData(i), lang)
                    if item:
                        combo.setItemText(i, item.label)
            if then:
                then()

        run_bg(lambda report: V.download(v, report), done,
               lambda pct: self.bar_voice.setValue(int(pct)))

    def _test_voice(self, lang: str) -> None:
        from .tts import Tts
        v = self._voice(lang)
        if v is None:
            return
        if not self._voice_mod.installed(v):
            self._download_voice(v, then=lambda: self._test_voice(lang))
            return
        cfg = self._collect()
        speed = self.sld_speed.value() / 10
        for b in self.btn_voice.values():
            b.setEnabled(False)
        self.lbl_voice.setText(f"Lecture avec {v.name}…")

        def speak():
            import sounddevice as sd
            speech = Tts(cfg).synth(SAMPLES[lang], lang, speed)
            sd.play(speech.audio, speech.sample_rate)
            sd.wait()

        def done(res):
            for b in self.btn_voice.values():
                b.setEnabled(True)
            self.lbl_voice.setText(f"Erreur : {res}" if isinstance(res, Exception) else "")

        run_bg(speak, done)

    # ------------------------------------------------------------------ page Général
    def _page_general(self) -> QWidget:
        w, lay = self._page("Général")
        frame, c = card("Démarrage")
        self.chk_autostart = QCheckBox("Lancer Vocalis au démarrage de Windows")
        c.addWidget(self.chk_autostart)
        lay.addWidget(frame)

        from . import __version__
        frame, c = card("Mises à jour", f"Version installée : Vocalis {__version__}")
        self.chk_updates = QCheckBox("Proposer automatiquement les nouvelles versions")
        c.addWidget(self.chk_updates)
        btn_upd = QPushButton("Rechercher une mise à jour maintenant")
        btn_upd.clicked.connect(self.checkUpdates.emit)
        c.addLayout(row(btn_upd))
        lay.addWidget(frame)

        frame, c = card("Dossiers et journal")
        b1 = QPushButton("Ouvrir le dossier de Vocalis")
        b1.clicked.connect(lambda: self._open(ROOT))
        b2 = QPushButton("Ouvrir le journal")
        b2.clicked.connect(lambda: self._open(LOG_PATH))
        b3 = QPushButton("Modifier config.toml (avancé)")
        b3.clicked.connect(lambda: self._open(ROOT / "config.toml"))
        c.addLayout(row(b1, b2, b3))
        c.addWidget(hint("Le journal détaille les erreurs : envoie-le si quelque chose ne "
                         "marche pas."))
        lay.addWidget(frame)
        lay.addStretch(1)
        return w

    @staticmethod
    def _open(path) -> None:
        if hasattr(os, "startfile") and os.path.exists(path):
            os.startfile(str(path))

    # ------------------------------------------------------------------ valeurs
    def _load(self, cfg: dict) -> None:
        r, d, n, lec = cfg["raccourcis"], cfg["dictee"], cfg["nettoyage"], cfg["lecture"]
        for key, btn in self.hotkey_buttons.items():
            btn.set_value(r.get(key, DEFAULTS["raccourcis"][key]))
        self._hotkeys_changed()
        for code, chk in self.chk_lang.items():
            chk.setChecked(code in d["langues"])
        combo_set(self.cmb_forced, d.get("langue_preferee", "fr"))
        combo_set(self.cmb_mic, d["micro"])
        self.chk_live.setChecked(bool(d["ecrire_en_direct"]))
        self.chk_replace.setChecked(bool(d["remplacer_par_version_nettoyee"]))
        self.sld_interval.setValue(int(round(float(d["intervalle_secondes"]) * 10)))
        self.sld_interval.valueChanged.emit(self.sld_interval.value())
        combo_set(self.cmb_whisper, d["modele_whisper"])
        combo_set(self.cmb_device, d["appareil"])
        combo_set(self.cmb_engine, d.get("moteur", "auto"))
        self.chk_click_stop.setChecked(bool(lec.get("clic_arrete", True)))
        self._update_engine_hint()
        self.chk_clean.setChecked(bool(n["actif"]))
        combo_set(self.cmb_clean, n["modele"])
        self.spn_timeout.setValue(int(n["delai_max_secondes"]))
        self.sld_speed.setValue(int(round(float(lec["vitesse"]) * 10)))
        self.sld_pause.setValue(int(round(float(lec.get("pause_phrases", 0.2)) / 0.02)))
        self.sld_pause.valueChanged.emit(self.sld_pause.value())
        self._fill_voices(cfg)
        self.sld_speed.valueChanged.emit(self.sld_speed.value())
        self._color = lec["couleur_surlignage"]
        self.sld_opacity.setValue(int(round(float(lec["opacite_surlignage"]) * 100)))
        self._update_preview()
        self.spn_font.setValue(int(lec["taille_police_fenetre"]))
        self.chk_autostart.setChecked(winutil.is_autostart())
        self.chk_updates.setChecked(bool(cfg.get("general", {}).get("mises_a_jour_auto", True)))

    def _collect(self) -> dict:
        cfg = copy.deepcopy(self.cfg)
        r, d, n, lec = cfg["raccourcis"], cfg["dictee"], cfg["nettoyage"], cfg["lecture"]
        for key, btn in self.hotkey_buttons.items():
            r[key] = btn.value
        d["langues"] = [c for c, chk in self.chk_lang.items() if chk.isChecked()]
        d["langue_preferee"] = combo_get(self.cmb_forced)
        d["micro"] = combo_get(self.cmb_mic) or ""
        d["ecrire_en_direct"] = self.chk_live.isChecked()
        d["remplacer_par_version_nettoyee"] = self.chk_replace.isChecked()
        d["intervalle_secondes"] = self.sld_interval.value() / 10
        d["modele_whisper"] = combo_get(self.cmb_whisper)
        d["appareil"] = combo_get(self.cmb_device)
        d["type_calcul"] = "float16" if d["appareil"] == "cuda" else "int8"
        d["moteur"] = combo_get(self.cmb_engine)
        lec["clic_arrete"] = self.chk_click_stop.isChecked()
        cfg.setdefault("general", {})["mises_a_jour_auto"] = self.chk_updates.isChecked()
        n["actif"] = self.chk_clean.isChecked()
        n["modele"] = combo_get(self.cmb_clean) or DEFAULTS["nettoyage"]["modele"]
        n["delai_max_secondes"] = self.spn_timeout.value()
        lec["vitesse"] = self.sld_speed.value() / 10
        lec["pause_phrases"] = round(self.sld_pause.value() * 0.02, 2)
        for lang in self.cmb_voice:
            vid = self.cmb_voice[lang].currentData()
            if vid:
                lec[f"voix_{lang}"] = vid
        lec["couleur_surlignage"] = self._color
        lec["opacite_surlignage"] = self.sld_opacity.value() / 100
        lec["taille_police_fenetre"] = self.spn_font.value()
        return cfg

    def _defaults(self) -> None:
        cfg = copy.deepcopy(DEFAULTS)
        cfg["nettoyage"]["url"] = self.cfg["nettoyage"]["url"]
        # Les narrateurs choisis ne sont jamais remis à zéro.
        for key, value in self.cfg["lecture"].items():
            if key.startswith("voix_"):
                cfg["lecture"][key] = value
        self._load(cfg)
        self.chk_autostart.setChecked(winutil.is_autostart())
        self.lbl_footer.setText("Valeurs par défaut chargées : clique sur Enregistrer.")

    def _validate(self, cfg: dict) -> str:
        if not cfg["dictee"]["langues"]:
            return "Coche au moins une langue de dictée."
        pref = cfg["dictee"].get("langue_preferee", "")
        if pref and pref not in cfg["dictee"]["langues"]:
            cfg["dictee"]["langues"].append(pref)
        keys = [v for v in cfg["raccourcis"].values()]
        if any(not k for k in keys):
            return "Chaque raccourci doit avoir une touche."
        if len(set(keys)) != len(keys):
            return "Deux actions utilisent la même touche."
        return ""

    def _save(self) -> None:
        cfg = self._collect()
        error = self._validate(cfg)
        if error:
            QMessageBox.warning(self, "Vocalis", error)
            return
        try:
            # On n'écrit que ce qui a changé dans cette fenêtre, par-dessus le fichier
            # actuel : un réglage modifié ailleurs entre-temps (narrateur choisi dans
            # la barre de lecture, vitesse…) n'est jamais écrasé.
            fresh = load_config()
            config_changed = False
            for section, values in cfg.items():
                if not isinstance(values, dict):
                    continue
                before = self.original.get(section, {})
                for key, value in values.items():
                    if before.get(key) != value:
                        fresh.setdefault(section, {})[key] = value
                        config_changed = True
            if config_changed:
                save_config(fresh)
            cfg = fresh
            if self._dictionary_changed():
                dictionary.save(self._collect_dictionary())
        except Exception as exc:
            QMessageBox.critical(self, "Vocalis", f"Enregistrement impossible :\n{exc}")
            return
        if self.chk_autostart.isChecked() != winutil.is_autostart():
            if not winutil.set_autostart(self.chk_autostart.isChecked()):
                QMessageBox.warning(self, "Vocalis",
                                    "Le démarrage automatique n'a pas pu être modifié.")
        self._saved = True
        self.cfg = cfg
        added = [l for l in cfg["dictee"]["langues"]
                 if l not in self.original["dictee"].get("langues", [])]
        self.close()
        if added:
            self._prepare_languages(added)
        elif config_changed:
            self.saved.emit()
        else:
            # Seul le dictionnaire a changé : il est pris en compte tout de suite,
            # sans redémarrer.
            self.closedWithoutSave.emit()

    def _prepare_languages(self, added: list[str]) -> None:
        """Nouvelle(s) langue(s) : tout est préparé automatiquement, avec une fenêtre de
        progression, puis Vocalis redémarre."""
        from .ui_tasks import Task, TaskWindow, install_step
        names = ", ".join(lang_name(l).lower() for l in added)
        tasks = [
            Task(f"lecture d'écran en {names} (Windows va demander ton accord : « Oui »)",
                 install_step("ocr", ",".join(added)), optional=True),
            Task("voix et dictée : vérification", func=python_check_voices(added),
                 optional=True),
        ]
        self._task_window = TaskWindow(f"Ajout : {names}", tasks, restart_after=True,
                                       on_restart=self.saved.emit)
        self._task_window.start()

    def _repair(self) -> None:
        """Réparer : retélécharge ce qui manque (modèles, voix, lecture d'écran)."""
        from .ui_tasks import Task, TaskWindow, install_step, python_module
        langs = ",".join(active_languages(self.cfg))
        tasks = [
            Task("reconnaissance vocale et voix", python_module("vocalis.setup_models",
                                                                "whisper", "voix")),
            Task("lecture d'écran (Windows peut demander ton accord : « Oui »)",
                 install_step("ocr", langs), optional=True),
            Task("assistant « Demander »", install_step("assistant"), optional=True),
        ]
        self._task_window = TaskWindow("Réparation de Vocalis", tasks, restart_after=True,
                                       on_restart=self.restartRequested.emit)
        self._task_window.start()

    def closeEvent(self, e) -> None:
        self._stop_mic()
        if not self._saved and (self._collect() != self.original
                                or self._dictionary_changed()):
            answer = QMessageBox.question(
                self, "Vocalis", "Enregistrer les modifications ?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel:
                e.ignore()
                return
            if answer == QMessageBox.Save:
                e.ignore()
                self._save()
                return
        super().closeEvent(e)
        if not self._saved:
            self.closedWithoutSave.emit()


def run_standalone() -> None:
    """Lancement direct (raccourci « Vocalis - Paramètres »)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Vocalis")
    win = SettingsWindow()

    def after_save():
        answer = QMessageBox.question(None, "Vocalis",
                                      "Paramètres enregistrés. Lancer Vocalis maintenant ?")
        if answer == QMessageBox.Yes:
            from PySide6.QtCore import QProcess
            QProcess.startDetached(sys.executable, ["-m", "vocalis"], str(ROOT))

    win.saved.connect(after_save)
    win.show()
    app.exec()
