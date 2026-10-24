"""Point d'entrée : icône dans la zone de notification, raccourcis clavier,
dictée et lecture à voix haute."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__, winutil
from .cleaner import Cleaner
from .config import LOG_PATH, ROOT, key_label, load_config
from .dictation import DictationSession
from .icon import ACCENT, GREY, make_icon
from .mousehook import AltClickHook
from .reading_controller import ReadingController
from .ui_pill import Pill

log = logging.getLogger("vocalis")


def setup_logging() -> None:
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    sys.excepthook = lambda t, v, tb: log.error("Erreur non gérée", exc_info=(t, v, tb))


class Bridge(QObject):
    """Les raccourcis et les fils de travail passent par ces signaux vers l'interface."""
    toggle = Signal()
    read = Signal()
    stopReading = Signal()
    dictState = Signal(str, str)
    loaded = Signal(str)
    updateFound = Signal(object)                 # nouvelle version disponible (ou None)
    altClick = Signal(int, int)                  # Alt + clic pendant une lecture
    plainClick = Signal(int, int, bool)          # essai : clic / double-clic en lecture
    openSettings = Signal(str)                   # ouvrir les paramètres à une page
    learned = Signal(str, str)                   # mot appris par le dictionnaire
    suggest = Signal(str, str)                   # correction à proposer (« Retenir ? »)
    ask = Signal()                               # touche « Demander »
    askContext = Signal(object, str, bool)       # texte trouvé (et s'il est sélectionné)


# =========================================================================== application
class App(QObject):
    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.cfg = load_config()
        winutil.ALLOW_CAPTURE = bool(self.cfg.get("general", {}).get("captures_visibles", False))
        self.bridge = Bridge()
        self.asr = None
        self.cleaner = Cleaner(self.cfg)
        from .translator import Translator
        self.translator = Translator(self.cfg)
        self.session: DictationSession | None = None
        self._ctx_pending = None          # « Demander » : texte de la question attendu
        self._pending_q = None            # question posée avant d'avoir le texte
        self.mode = ""
        self._hold_down = False
        self._last_toggle = 0.0
        self._esc_handle = None

        self.pill = Pill(lambda: self.session.level if self.session else 0.0)
        self.pill.closeClicked.connect(self._cancel_dictation)
        # La pastille s'ouvre à chaque fois près de l'endroit où l'on écrit (plus de
        # position mémorisée : on la retrouve toujours).
        self.pill.langClicked.connect(self._cycle_language)
        self.pill.targetClicked.connect(self._pick_target)
        from .languages import active as active_languages
        self.pill.languages = active_languages(self.cfg)
        self.reading = ReadingController(self.cfg, self.bridge, self.pill)
        self.reading.on_active_changed = self._reading_active
        from .media import MediaPauser
        self.media = MediaPauser(lambda: bool(self.cfg["lecture"].get("pause_medias", True)))
        from .ui_answer import AskController
        self.ask = AskController(self.cfg, self.reading.tts, lambda: self.reading.speed,
                                 key_label(self.cfg["raccourcis"].get("demander", "")),
                                 install_cb=self._install_assistant, media=self.media)
        from .learning import CorrectionWatcher
        self.learner = CorrectionWatcher(self.reading.uia,
                                         lambda old, new: self.bridge.learned.emit(old, new),
                                         lambda old, new: self.bridge.suggest.emit(old, new))
        from .ui_learn import LearnPrompt
        self.learn_prompt = LearnPrompt()
        self.learn_prompt.answered.connect(self._on_learn_answer)
        # Alt + clic pendant une lecture : relancer au paragraphe cliqué.
        self.alt_hook = AltClickHook(lambda x, y: self.bridge.altClick.emit(x, y),
                                     lambda x, y, d: self.bridge.plainClick.emit(x, y, d))
        self.alt_hook.plain_clicks = bool(self.cfg["lecture"].get("clic_arrete", True))
        self.alt_hook.start()

        b = self.bridge
        b.toggle.connect(self._on_toggle)
        b.read.connect(self._on_read)
        b.stopReading.connect(self.reading.stop)
        b.altClick.connect(self.reading.alt_click)
        b.plainClick.connect(self.reading.plain_click)
        b.openSettings.connect(self.open_settings)
        b.learned.connect(self._on_learned)
        b.suggest.connect(lambda old, new: self.learn_prompt.ask(old, new, self.pill.near))
        b.ask.connect(self._on_ask)
        b.askContext.connect(self._on_ask_context)
        b.updateFound.connect(self._on_update_found)
        self._update_dialog = None
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self.check_updates)
        self._update_timer.start(24 * 3600 * 1000)          # une fois par jour
        b.dictState.connect(self._on_dict_state)
        b.loaded.connect(self._on_loaded)

        self.settings = None
        self._make_tray()
        self._listen()
        self._register_hotkeys()
        threading.Thread(target=self._load_models, daemon=True, name="chargement").start()

    # ------------------------------------------------------------------ zone de notification
    def _make_tray(self) -> None:
        r = self.cfg["raccourcis"]
        self.tray = QSystemTrayIcon(make_icon(GREY), self)
        menu = QMenu()
        info = [
            f"Vocalis {__version__}",
            f"Dictée : {key_label(r['dictee_bascule'])} (marche/arrêt)",
            f"Lecture : {key_label(r['lecture'])} (sélection ou zone)",
        ]
        for line in info:
            act = QAction(line, menu)
            act.setEnabled(False)
            menu.addAction(act)
        menu.addSeparator()
        act = menu.addAction("Paramètres…", lambda: self.open_settings())
        f = act.font()
        f.setBold(True)
        act.setFont(f)
        self._make_engine_menu(menu)
        act = menu.addAction("Lecture : clic = arrêter · double-clic = reprendre ici (essai)")
        act.setCheckable(True)
        act.setChecked(bool(self.cfg["lecture"].get("clic_arrete", True)))
        act.toggled.connect(self._toggle_click_mode)
        menu.addAction("Ouvrir le journal", lambda: os.startfile(str(LOG_PATH)))
        menu.addAction("Redémarrer", self._restart)
        menu.addSeparator()
        menu.addAction("Quitter", self._quit)
        self.menu = menu
        self.panel = None
        # Nouveau panneau moderne ; l'ancien menu reste disponible
        # (interface > menu_classique = true dans config.toml).
        classic = bool(self.cfg.get("interface", {}).get("menu_classique", False))
        if classic:
            self.tray.setContextMenu(menu)
        self.tray.messageClicked.connect(lambda: self.open_settings())
        self.tray.activated.connect(lambda reason: self._tray_activated(reason, classic))
        self.tray.setToolTip("Vocalis — chargement des modèles…")
        self.tray.show()

    # ------------------------------------------------------------------ paramètres
    def _listen(self) -> None:
        """Un second lancement (ou le raccourci Paramètres) demande d'ouvrir les paramètres."""
        QLocalServer.removeServer(SERVER_NAME)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)
        self.server.listen(SERVER_NAME)

    def _on_connection(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return

        def read():
            msg = bytes(sock.readAll()).decode(errors="ignore").strip()
            if msg == "parametres":
                self.open_settings()
            elif msg == "quitter":              # mise à jour par un nouveau lancement
                log.info("Fermeture demandée pour une mise à jour")
                sock.disconnectFromServer()
                QTimer.singleShot(0, self._quit)
                return
            else:
                self.tray.showMessage("Vocalis", "Vocalis est déjà lancé.",
                                      QSystemTrayIcon.Information, 2500)
            sock.disconnectFromServer()

        sock.readyRead.connect(read)

    def _on_learned(self, old: str, new: str) -> None:
        log.info("Mot appris : %s -> %s", old, new)
        self.tray.showMessage(
            "Vocalis a appris un mot",
            f"« {new} » sera écrit ainsi (au lieu de « {old} »).\n"
            "Paramètres > Dictionnaire pour le modifier.",
            QSystemTrayIcon.Information, 5000)

    def _set_test(self, key: str, value: bool) -> None:
        """Interrupteurs du mode test : on enregistre, puis Vocalis redémarre (les
        moteurs dépendent de la carte graphique et d'Internet)."""
        from .config import update_settings
        update_settings("test", {key: value})
        on = not value
        msg = (("Carte graphique activée" if on else "Carte graphique désactivée")
               if key == "sans_carte_graphique" else
               ("Internet activé" if on else "Internet coupé"))
        log.info("Mode test : %s", msg)
        if self.panel is not None:
            self.panel.close()
        self.tray.showMessage("Vocalis", f"Mode test : {msg.lower()} — redémarrage…",
                              QSystemTrayIcon.Information, 2000)
        QTimer.singleShot(1200, self._restart)

    def _on_learn_answer(self, old: str, new: str, yes: bool) -> None:
        from . import dictionary
        log.info("Retenir « %s » -> « %s » : %s", old, new, "oui" if yes else "non")
        if yes and dictionary.learn(old, new, confirmed=True):
            self.pill.show_state("done", f"« {new} » ajouté au dictionnaire", autohide_ms=2000)

    def open_settings(self, page: str = "") -> None:
        from .settings_ui import SettingsWindow
        if getattr(self, "settings", None) is not None:
            if page:
                self.settings.show_page(page)
            self.settings.showNormal()
            self.settings.raise_()
            self.settings.activateWindow()
            return
        # Vocalis reste utilisable pendant les paramètres ; ses raccourcis ne sont
        # suspendus que le temps de capturer une nouvelle touche.
        self.settings = SettingsWindow()
        self.settings.saved.connect(self._settings_saved)
        self.settings.closedWithoutSave.connect(self._settings_closed)
        self.settings.captureChanged.connect(self._capture_mode)
        self.settings.updateReady.connect(self._update_downloaded)
        self.settings.restartRequested.connect(self._restart)
        if page:
            self.settings.show_page(page)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _settings_saved(self) -> None:
        self.settings = None
        self.tray.showMessage("Vocalis", "Paramètres enregistrés : redémarrage…",
                              QSystemTrayIcon.Information, 2000)
        QTimer.singleShot(300, self._restart)

    def _settings_closed(self) -> None:
        self.settings = None

    def _capture_mode(self, capturing: bool) -> None:
        """Pendant la capture d'une touche dans les paramètres, les raccourcis de
        Vocalis sont suspendus (sinon la combinaison tapée lancerait la dictée)."""
        import keyboard
        if capturing:
            try:
                keyboard.unhook_all()
            except Exception:
                pass
            self._esc_handle = None
        else:
            self._register_hotkeys()
            if self.reading.active:
                self._reading_active(True)

    # ------------------------------------------------------------------ mises à jour
    def check_updates(self) -> None:
        """Cherche une nouvelle version sur GitHub (en arrière-plan). Le bouton
        « Rechercher une mise à jour maintenant » a sa propre recherche (settings_ui)."""
        from . import updates
        from .hardware import offline
        if offline():
            # Mode test hors ligne : pas de recherche automatique ; le bouton
            # « Rechercher une mise à jour maintenant » reste possible.
            log.info("Mode test hors ligne : pas de recherche automatique de mise à jour")
            return
        if not updates.installed_copy() or \
                not self.cfg.get("general", {}).get("mises_a_jour_auto", True):
            return

        def work():
            try:
                info = updates.check()
            except Exception as exc:
                log.info("Vérification des mises à jour impossible : %s", exc)
                return
            self.bridge.updateFound.emit(info)

        threading.Thread(target=work, daemon=True, name="mises-a-jour").start()

    def _on_update_found(self, info) -> None:
        if info and self._update_dialog is None:
            self._show_update(info)

    def _update_downloaded(self, data: dict) -> None:
        """Nouvelle version déjà téléchargée depuis les paramètres : fenêtre habituelle,
        qui l'installe sans la télécharger une deuxième fois."""
        old = self._update_dialog
        if old is not None:
            if not old.btn_go.isEnabled():        # installation déjà en cours
                old.raise_()
                return
            old.close()
        self._show_update(data["info"], data["archive"])

    def _show_update(self, info: dict, archive: bytes | None = None) -> None:
        from . import updates
        from .ui_update import UpdateDialog
        log.info("Nouvelle version disponible : %s", info.get("version"))
        dlg = self._update_dialog = UpdateDialog(
            __version__, info, lambda i, progress: updates.install(i, progress, archive),
            lambda _err: self._restart())
        # seulement si c'est toujours la fenêtre en cours (une ancienne se ferme peut-être)
        dlg.destroyed.connect(lambda: self._update_dialog is dlg
                              and setattr(self, "_update_dialog", None))
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show_centered()

    def _restart(self) -> None:
        QProcess.startDetached(sys.executable, ["-m", "vocalis", "--restart"], str(ROOT))
        self._quit()

    def _quit(self) -> None:
        # Chaque étape est protégée : quoi qu'il arrive, l'application se ferme,
        # sinon un redémarrage laisserait l'ancienne instance en vie.
        for step in (self._unhook_all, self.reading.stop, self._stop_session, self.tray.hide,
                     self.alt_hook.stop):
            try:
                step()
            except Exception:
                log.exception("Erreur pendant la fermeture")
        self.qapp.quit()

    @staticmethod
    def _unhook_all() -> None:
        import keyboard
        keyboard.unhook_all()

    def _stop_session(self) -> None:
        if self.session and self.session.is_alive():
            self.session.request_stop()

    # ------------------------------------------------------------------ raccourcis
    def _register_hotkeys(self) -> None:
        import keyboard

        from .hotkeys import Combo

        r = self.cfg["raccourcis"]
        b = self.bridge

        self._combos = []
        try:
            self._combos = [
                Combo(keyboard, r["dictee_bascule"], self._toggle_cb, None, "dictée marche/arrêt"),
                Combo(keyboard, r["lecture"], self._debounced(b.read.emit), None, "lecture"),
            ]
            if r.get("demander"):
                self._combos.append(Combo(keyboard, r["demander"], self._debounced(b.ask.emit),
                                          None, "demander"))
        except Exception as exc:
            log.exception("Raccourcis impossibles à enregistrer")
            QMessageBox.warning(None, "Vocalis",
                                f"Un raccourci n'est pas valide :\n{exc}\n\n"
                                "Corrige-le dans Paramètres > Raccourcis.")

    def _toggle_cb(self) -> None:
        now = time.monotonic()
        if now - self._last_toggle > 0.35:
            self._last_toggle = now
            self.bridge.toggle.emit()

    @staticmethod
    def _debounced(fn):
        state = {"t": 0.0}

        def cb():
            now = time.monotonic()
            if now - state["t"] > 0.5:
                state["t"] = now
                fn()
        return cb

    def _reading_active(self, active: bool) -> None:
        import keyboard
        if active:
            self.media.hold("lecture")
        else:
            self.media.release("lecture")
        self.alt_hook.active = active
        if active and self._esc_handle is None:
            self._esc_handle = keyboard.add_hotkey(self.cfg["raccourcis"]["arreter_lecture"],
                                                   self.bridge.stopReading.emit)
        elif not active and self._esc_handle is not None:
            try:
                keyboard.remove_hotkey(self._esc_handle)
            except (KeyError, ValueError):
                pass
            self._esc_handle = None

    # ------------------------------------------------------------------ panneau de l'icône
    def _tray_activated(self, reason, classic: bool) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            if self.panel is not None:
                self.panel.close()
            self.open_settings()
        elif not classic and reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.Context):
            self.show_panel()

    def show_panel(self) -> None:
        from .hardware import ENGINES
        from .ui_tray import TrayPanel
        if self.panel is not None:
            self.panel.close()
            return
        if time.monotonic() - getattr(self, "_panel_closed_at", 0.0) < 0.35:
            return          # le clic sur l'icône vient de fermer le panneau
        r = self.cfg["raccourcis"]
        ready = self.asr is not None
        engine = getattr(self, "engine", "")
        short = {"whisper": "Whisper", "parakeet": "Parakeet", "windows": "Windows"}
        status = f"Prêt · {ENGINES.get(engine, '')}" if ready else "Chargement du moteur…"
        from .hardware import test_label
        if test_label():
            status += "\n" + test_label()
        state = {
            "title": f"Vocalis {__version__}",
            "status": status,
            "ready": ready,
            "hotkeys": [("Dictée", key_label(r["dictee_bascule"])),
                        ("Lecture", key_label(r["lecture"]))]
            + ([("Demander", key_label(r["demander"]))] if r.get("demander") else []),
            "engine": engine,
            "engines": [(k, short[k]) for k in ENGINES],
            "click_stop": bool(self.cfg["lecture"].get("clic_arrete", True)),
        }
        if "test" in self.cfg:
            from .hardware import offline, simulate_no_gpu
            state["test"] = {"gpu": not simulate_no_gpu(), "internet": not offline()}
        actions = {
            "engine": self.switch_engine,
            "click_stop": self._toggle_click_mode,
            "test_gpu": lambda on: self._set_test("sans_carte_graphique", not on),
            "test_internet": lambda on: self._set_test("hors_ligne", not on),
            "settings": lambda: self.open_settings(),
            "dictionary": lambda: self.open_settings("dictionnaire"),
            "log": lambda: os.startfile(str(LOG_PATH)),
            "restart": self._restart,
            "quit": self._quit,
        }
        self.panel = TrayPanel(state, actions)
        self.panel.closed.connect(self._panel_closed)
        self.panel.show_near(self.tray.geometry())

    def _panel_closed(self) -> None:
        self.panel = None
        self._panel_closed_at = time.monotonic()

    # ------------------------------------------------------------------ moteur de dictée
    def _toggle_click_mode(self, on: bool) -> None:
        log.info("Essai clic / double-clic pendant la lecture : %s", "oui" if on else "non")
        self.alt_hook.plain_clicks = on
        self._save_settings("lecture", {"clic_arrete": on})
        for act in self.menu.actions():
            if act.isCheckable() and act.text().startswith("Lecture : clic"):
                act.blockSignals(True)
                act.setChecked(on)
                act.blockSignals(False)

    def _make_engine_menu(self, menu: QMenu) -> None:
        from PySide6.QtGui import QActionGroup

        from .hardware import ENGINES
        sub = menu.addMenu("Moteur de dictée")
        group = QActionGroup(sub)
        group.setExclusive(True)
        self._engine_actions = {}
        for key, label in ENGINES.items():
            act = sub.addAction(label)
            act.setCheckable(True)
            group.addAction(act)
            act.triggered.connect(lambda _=False, k=key: self.switch_engine(k))
            self._engine_actions[key] = act

    def _sync_engine_menu(self) -> None:
        for key, act in getattr(self, "_engine_actions", {}).items():
            act.setChecked(key == getattr(self, "engine", ""))

    def switch_engine(self, engine: str) -> None:
        """Change de moteur sans redémarrer (menu de l'icône ou paramètres)."""
        if engine == getattr(self, "engine", "") and self.asr is not None:
            return
        if self._busy():
            self.pill.show_state("info", "Termine d'abord la dictée en cours", autohide_ms=2000)
            self._sync_engine_menu()
            return
        from .hardware import ENGINES
        log.info("Changement de moteur : %s", ENGINES[engine])
        self._save_settings("dictee", {"moteur": engine})
        self.asr = None
        self.engine = engine
        self.tray.setIcon(make_icon(GREY))
        self.tray.setToolTip(f"Vocalis — chargement de {ENGINES[engine]}…")
        self._sync_engine_menu()
        threading.Thread(target=self._load_models, daemon=True, name="chargement").start()

    # ------------------------------------------------------------------ chargement
    def _load_models(self) -> None:
        from .hardware import ENGINES, resolve_engine
        notes = []
        engine = resolve_engine(self.cfg)
        self.engine = engine
        try:
            if engine == "windows":
                from .windows_stt import WindowsEngine, supported_tags
                self.asr = WindowsEngine(self.cfg)
                try:
                    log.info("Langues de dictée Windows : %s", supported_tags())
                except Exception:
                    log.exception("Reconnaissance vocale Windows indisponible")
                    notes.append("Reconnaissance vocale Windows indisponible (voir le journal).")
            elif engine == "parakeet":
                from . import parakeet
                if not parakeet.installed():
                    self.bridge.loaded.emit(f"@Téléchargement de Parakeet ({parakeet.SIZE_MB} Mo, "
                                            "une seule fois)…")
                self.asr = parakeet.ParakeetAsr(self.cfg)
            else:
                from .asr import build_asr
                self.asr = build_asr(self.cfg)
                if self.asr.device == "cpu" and self.cfg["dictee"]["appareil"] == "cuda":
                    notes.append("Pas de carte graphique utilisable : Whisper est lent sur "
                                 "le processeur. Essaie le moteur Parakeet (clic droit sur "
                                 "l'icône > Moteur de dictée).")
            log.info("Moteur de dictée : %s", ENGINES[engine])
        except Exception as exc:
            log.exception("Moteur de dictée %s indisponible", engine)
            notes.append(f"Moteur de dictée {ENGINES[engine]} indisponible : {exc}")
        if self.cleaner.enabled:
            if self.cleaner.available():
                self.cleaner.warmup()
            else:
                self.cleaner.enabled = False
                notes.append("Nettoyage désactivé : Ollama ou le modèle "
                             f"{self.cleaner.model} est absent.")
        for lang in ("fr", "pt"):
            if not self.reading.tts.available(lang):
                notes.append(f"Voix de lecture {lang.upper()} absente.")
        self.bridge.loaded.emit("\n".join(notes))

    def _on_loaded(self, notes: str) -> None:
        if notes.startswith("@"):                  # étape intermédiaire du chargement
            self.tray.setToolTip("Vocalis — " + notes[1:])
            self.tray.showMessage("Vocalis", notes[1:], QSystemTrayIcon.Information, 4000)
            return
        if not getattr(self, "_update_checked", False):
            self._update_checked = True
            QTimer.singleShot(15000, self.check_updates)
        r = self.cfg["raccourcis"]
        ready = self.asr is not None
        self.tray.setIcon(make_icon(ACCENT if ready else GREY))
        from .hardware import ENGINES
        name = ENGINES.get(getattr(self, "engine", ""), "")
        self.tray.setToolTip(f"Vocalis — prêt · {name}" if ready else "Vocalis — problème")
        self._sync_engine_menu()
        body = (f"{key_label(r['dictee_bascule'])} dictée marche/arrêt · "
                f"{key_label(r['lecture'])} lecture")
        if notes:
            body = notes + "\nClique ici pour ouvrir les paramètres."
        self.tray.showMessage(f"Vocalis est prêt · {name}" if ready else "Vocalis", body,
                              QSystemTrayIcon.Information if not notes
                              else QSystemTrayIcon.Warning, 6000)

    def _save_settings(self, section: str, values: dict) -> None:
        """Enregistre quelques réglages sans toucher aux autres."""
        from .config import save_config
        cfg = load_config()                 # relit le fichier : ne perd aucun réglage
        cfg.setdefault(section, {}).update(values)
        self.cfg.setdefault(section, {}).update(values)
        try:
            save_config(cfg)
        except Exception:
            log.exception("Réglage non enregistré : %s", values)

    def _writing_rect(self):
        """Où l'on écrit (sélection ou curseur de texte), en coordonnées de l'écran ;
        à défaut, la souris."""
        from PySide6.QtGui import QGuiApplication

        from . import screenmap
        r = None
        t = time.monotonic()
        try:
            r = self.reading.uia.caret_rect().result(timeout=0.8)
        except Exception:
            r = None
        if r:
            rect = screenmap.to_logical_rect(*r)
            if QGuiApplication.screenAt(rect.center().toPoint()) is not None:
                log.info("Position trouvée en %.2f s : %s", time.monotonic() - t,
                         tuple(round(v) for v in rect.getRect()))
                return rect
        log.info("Position introuvable (%.2f s) : près de la souris", time.monotonic() - t)
        return screenmap.mouse_rect()

    def _languages(self) -> list[str]:
        langs = [l for l in self.cfg["dictee"].get("langues", []) if l] or ["fr"]
        return langs

    def _cycle_language(self) -> None:
        """Clic sur le badge : FR -> PT -> EN -> FR…"""
        langs = self._languages()
        current = (self.pill.lang or self.cfg["dictee"].get("langue_preferee", "")).lower()
        nxt = langs[(langs.index(current) + 1) % len(langs)] if current in langs else langs[0]
        log.info("Langue choisie sur la pastille : %s", nxt)
        self.pill.set_language(nxt, auto=False)
        self._save_settings("dictee", {"langue_preferee": nxt})
        if self._busy():
            self.session.set_language(nxt)

    # ------------------------------------------------------------------ dictée
    def _busy(self) -> bool:
        return self.session is not None and self.session.is_alive()

    def _start_dictation(self, mode: str, ask: bool = False) -> None:
        if self.asr is None:
            self.pill.show_state("info", "Chargement des modèles…", autohide_ms=2000)
            return
        if self._busy():
            if self.session.stopping:
                self.pill.show_state("info", "Patiente, je termine le texte précédent…",
                                     autohide_ms=1500)
            return
        self.mode = mode
        self.learner.stop()
        self.ask.stop_speaking()
        self.reading.stop()          # une lecture en cours s'arrête (les deux s'excluent)
        report = lambda s, t: self.bridge.dictState.emit(s, t)  # noqa: E731
        if getattr(self, "engine", "") == "windows":
            from .windows_stt import WindowsDictationSession
            self.session = WindowsDictationSession(self.cfg, self.cleaner, report,
                                                   self.translator)
        else:
            self.session = DictationSession(self.cfg, self.asr, self.cleaner, report,
                                            self.translator)
        followup = ask and self.ask.open
        rect = self._writing_rect()
        self.pill.place_near(rect)
        self.pill.begin_session()
        pref = self.cfg["dictee"].get("langue_preferee", "") or self._languages()[0]
        if ask:
            if not followup:
                self._ask_rect = rect
            self.session.ask_mode = True
            self._ctx_pending = None
            self._pending_q = None
            if not followup:
                from . import actions
                shown, more = actions.visible(6)
                self.pill.fit_actions([(a["id"], a["nom"]) for a in shown + more])
                self._ctx_pending = self.session
                self._read_context(self.session, pref)
            self.pill.set_language(self.cfg["dictee"].get("langue_forcee", "") or pref)
            self.pill.show_state("listening", "Question de suite" if followup
                                 else "Pose ta question", closable=True)
            self.session.start()
            return
        self._read_selection(self.session)
        self.pill.set_language(self.cfg["dictee"].get("langue_forcee", "") or pref)
        self.pill.show_state("listening", "J'écoute", closable=(mode == "toggle"))
        self.session.start()

    # ------------------------------------------------------------------ Demander
    def _on_ask(self) -> None:
        """Touche « Demander » : question sur le texte sélectionné (ou le paragraphe
        sous la souris). Deuxième appui : la question est validée."""
        if self._busy():
            if self.session.ask_mode and not self.session.stopping:
                self._stop_dictation()
            elif not self.session.ask_mode:
                self.pill.show_state("info", "Termine d'abord la dictée en cours",
                                     autohide_ms=1500)
            return
        self._start_dictation("toggle", ask=True)

    def _read_context(self, session, lang: str) -> None:
        """Texte de la question : la sélection, sinon le paragraphe sous la souris."""
        from PySide6.QtGui import QCursor

        from . import screenmap
        uia = self.reading.uia
        nx, ny = screenmap.to_native(QCursor.pos())

        def work():
            text = ""
            try:
                text = (uia.selected_text().result(timeout=1.5) or "").strip()
            except Exception:
                pass
            selected = bool(text)
            if not text:
                try:
                    doc = uia.paragraphs_at(nx, ny).result(timeout=2.5)
                    if doc is not None and doc.paragraphs:
                        text = "\n\n".join(doc.paragraphs[doc.start_para:]).strip()
                except Exception:
                    pass
            log.info("Demander : texte de %d caractère(s)", len(text))
            self.bridge.askContext.emit(session, text, selected)

        threading.Thread(target=work, daemon=True, name="demander-texte").start()

    def _on_ask_context(self, session, text: str, selected: bool = False) -> None:
        if session is not getattr(self, "_ctx_pending", None):
            return                                  # question annulée entre-temps
        self._ctx_pending = None
        self._ask_selected = selected
        lang = self.cfg["dictee"].get("langue_preferee", "") or self._languages()[0]
        self.ask.begin(text, lang)
        if text and session.is_alive():
            session.set_selection(text)
        if self._pending_q is not None:             # question posée avant d'avoir le texte
            q, self._pending_q = self._pending_q, None
            self.ask.ask(*q)

    def _on_question(self, payload: str) -> None:
        import json

        from . import actions
        raw, lang, preset = json.loads(payload)
        self.pill.set_language("")
        self.pill.end_session()
        pref = self.cfg["dictee"].get("langue_preferee", "") or self._languages()[0]
        raw = raw.strip()
        action = actions.get(preset) if preset else None
        if action is None and raw:
            action = actions.match_spoken(raw)       # « plus poli » dit à voix haute
            if action is not None:
                log.info("Action reconnue à la voix : %s", action["nom"])
                raw = ""
        if action is not None:
            question = raw                  # quelques mots dits avant le clic : précision
            shown = action["nom"] + (f" · {raw}" if raw else "")
            lang = pref if not raw else lang
        elif raw:
            question = shown = raw
        else:
            self.pill.show_state("done", "Rien entendu", autohide_ms=1500)
            return
        self.pill._hide()
        if not self.ask.open:
            self.ask.card.place_near(getattr(self, "_ask_rect", None))
        args = (question, shown, lang or pref, action, getattr(self, "_ask_selected", False))
        if getattr(self, "_ctx_pending", None) is not None:
            self._pending_q = args
            return
        self.ask.ask(*args)

    def _install_assistant(self) -> None:
        from .assistant import assistant_model
        from .ui_tasks import Task, TaskWindow, install_step
        model = assistant_model(self.cfg)
        size = "4,7 Go" if "7b" in model else "2 Go"
        tasks = [Task(f"assistant de Vocalis ({model}, environ {size})",
                      install_step("assistant"))]
        self._assistant_tasks = TaskWindow("Installer l'assistant", tasks,
                                           restart_after=False)
        self._assistant_tasks.show()
        self.ask.card.show_message("Installation en cours… Quand elle est finie, appuie de "
                                   "nouveau sur la touche « Demander ».")

    def _actions_menu(self) -> None:
        """« … » : les autres actions, et un lien vers la page Actions."""
        from PySide6.QtGui import QAction, QCursor
        menu = QMenu()
        for key, label in self.pill.more:
            act = QAction(label, menu)
            act.triggered.connect(lambda _=False, k=key: self._busy() and
                                  self.session.set_target(k))
            menu.addAction(act)
        menu.addSeparator()
        act = QAction("Gérer les actions…", menu)
        act.triggered.connect(lambda: (self._cancel_dictation(), self.open_settings("actions")))
        menu.addAction(act)
        self._menu_ref = menu
        menu.popup(QCursor.pos())

    def _pick_target(self, lang: str) -> None:
        """Clic sur une langue dans l'encadré : le texte validé sera traduit dans
        cette langue (nouveau clic sur la même langue : plus de traduction).
        Avec « Demander » : clic sur une action (« … » ouvre les autres)."""
        if self._busy():
            if lang == "__more__" and self.session.ask_mode:
                self._actions_menu()
                return
            self.session.set_target(lang)

    def _read_selection(self, session) -> None:
        """Texte sélectionné au moment d'appuyer sur la touche : il s'affiche dans
        l'encadré ; la dictée s'y ajoute, un clic sur une langue le traduit, et la
        validation remplace la sélection."""
        fut = self.reading.uia.selected_text()

        def wait():
            try:
                text = (fut.result(timeout=1.5) or "").strip()
            except Exception:
                return
            if text:
                log.info("Dictée avec une sélection de %d caractère(s)", len(text))
                session.set_selection(text)

        threading.Thread(target=wait, daemon=True, name="selection").start()

    def _cancel_dictation(self) -> None:
        """Croix de l'encadré : tout est annulé, rien n'est collé ni remplacé."""
        self._ctx_pending = self._pending_q = None
        if self._busy():
            log.info("Dictée annulée par la croix")
            self.session.cancel()
        self.pill.set_language("")
        self.pill.end_session()
        self.pill._hide()

    def _stop_dictation(self) -> None:
        if self._busy() and not self.session.stopping:
            self.session.request_stop()
            self.pill.show_state("finishing", "Finalisation…")

    def _on_read(self) -> None:
        """Touche de lecture : une dictée en cours est annulée (les deux s'excluent)."""
        self.ask.stop_speaking()
        from . import screenmap
        if not self._busy():
            self.pill.place_near(screenmap.mouse_rect())   # messages de lecture : près de la souris
        if self._busy() and not self.session.stopping:
            self.session.cancel()
            self.pill.end_session()
            self.pill.set_language("")
            self.pill._hide()
        self.reading.start_reading()

    def _on_toggle(self) -> None:
        if self._busy():
            self._stop_dictation()
        else:
            self._start_dictation("toggle")

    def _on_dict_state(self, state: str, text: str) -> None:
        if state == "listening":
            if self.session and not self.session.stopping:
                self.pill.show_state("listening", "J'écoute", closable=(self.mode == "toggle"))
        elif state == "live":
            import json
            sure, maybe, done = json.loads(text)
            self.pill.set_live(sure, maybe, done)
        elif state == "lang":
            lang, _, auto = text.partition("|")
            self.pill.set_language(lang, auto=(auto == "auto"))
        elif state == "target":
            self.pill.set_target(text)
        elif state == "selection":
            self.pill.set_selection(text)
        elif state == "translation":
            self.pill.set_translation(text)
        elif state == "cleaning" and text:
            self.pill.show_state("cleaning", text)
        elif state == "preview":
            if self.session and not self.session.stopping:
                shown = text if len(text) <= 48 else "…" + text[-47:]
                self.pill.show_state("listening", shown or "J'écoute",
                                     closable=(self.mode == "toggle"))
        elif state == "finishing":
            self.pill.show_state("finishing", "Finalisation…")
        elif state == "cleaning":
            self.pill.show_state("cleaning", "Nettoyage…")
        elif state == "question":
            self._on_question(text)
        elif state == "inserted":
            self.learner.watch(text)
        elif state == "cancelled":
            self.pill.set_language("")
            self.pill.end_session()
        elif state == "done":
            self.pill.set_language("")
            self.pill.end_session()
            self.pill.show_state("done", text or "Terminé", autohide_ms=2500 if text else 700)
        elif state == "error":
            self.pill.set_language("")
            self.pill.end_session()
            self.pill.show_state("error", text, autohide_ms=5000)


SERVER_NAME = "Vocalis_Commandes"


def _send_to_running(message: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(1000):
        return False
    sock.write(message.encode())
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main() -> None:
    os.chdir(ROOT)
    setup_logging()
    from .config import CONFIG_PATH, DEFAULTS, save_config
    if not CONFIG_PATH.exists():          # première utilisation : réglages par défaut
        try:
            import copy
            save_config(copy.deepcopy(DEFAULTS))
        except Exception:
            log.exception("Réglages par défaut non écrits")
    log.info("Démarrage de Vocalis %s %s", __version__, sys.argv[1:])
    from .hardware import test_label
    if test_label():
        log.info("%s", test_label())
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("Vocalis")
    from .winutil import qt_in_french
    qt_in_french(qapp)
    qapp.setFont(QFont("Segoe UI", 10))
    qapp.setWindowIcon(make_icon())

    if "--parametres" in sys.argv:
        # Vocalis tourne déjà : c'est lui qui ouvre ses paramètres.
        if _send_to_running("parametres"):
            return
        from .settings_ui import run_standalone
        qapp.setQuitOnLastWindowClosed(True)
        run_standalone()
        return

    restarting = "--restart" in sys.argv
    acquired = winutil.acquire_single_instance()
    if not acquired and restarting:
        for _ in range(50):
            time.sleep(0.1)
            if winutil.acquire_single_instance():
                acquired = True
                break
    if not acquired:
        # Deuxième lancement : on ouvre les paramètres de l'instance en cours.
        if not _send_to_running("parametres"):
            QMessageBox.information(None, "Vocalis",
                                    "Vocalis est déjà lancé (icône près de l'horloge).")
        return
    app = App(qapp)  # noqa: F841  (garde une référence)
    sys.exit(qapp.exec())
