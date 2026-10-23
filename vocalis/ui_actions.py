"""Fenêtres des Actions : « Garder comme action », l'éditeur (avec son mode avancé)
et la page Actions des paramètres."""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox,
                               QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QScrollArea,
                               QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import actions

log = logging.getLogger(__name__)

ICON = {"repondre": "💬", "transformer": "✏️"}
TYPE_LABEL = {"repondre": "💬 Répond", "transformer": "✏️ Transforme"}
SAMPLE = ("Bonjour Paul, je t'écris parce que le rapport n'est toujours pas arrivé. Il faut "
          "absolument qu'on l'ait avant vendredi sinon on va avoir des problèmes avec le "
          "client. Merci de faire le nécessaire.")


def _style() -> str:
    from .settings_ui import STYLE
    return STYLE + "QDialog { background: #F4F4F6; }"


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(True)
    return lbl


# =========================================================================== garder
class KeepDialog(QDialog):
    """« Garder comme action » : nom proposé, type deviné, un clic."""
    saved = Signal(str)

    def __init__(self, question: str):
        super().__init__(None, Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Vocalis — Nouvelle action")
        self.setStyleSheet(_style())
        self.setFixedWidth(440)
        self.question = question
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        t = QLabel("Garder comme action")
        t.setObjectName("cardTitle")
        lay.addWidget(t)
        lay.addWidget(_hint(f"« {question} » deviendra un bouton de « Demander ». Tu pourras "
                            "aussi le dire à voix haute."))
        lay.addWidget(QLabel("Nom du bouton :"))
        self.name = QLineEdit(actions.suggest_name(question))
        lay.addWidget(self.name)
        lay.addWidget(QLabel("Le résultat :"))
        self.rb_answer = QRadioButton("💬 S'affiche et il est lu (répondre, résumer…)")
        self.rb_transform = QRadioButton("✏️ Remplace le texte sélectionné (réécrire, "
                                         "corriger…)")
        (self.rb_transform if actions.guess_type(question) == "transformer"
         else self.rb_answer).setChecked(True)
        lay.addWidget(self.rb_answer)
        lay.addWidget(self.rb_transform)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Garder")
        ok.setObjectName("primary")
        ok.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def show_centered(self, near: QWidget | None = None) -> None:
        self.adjustSize()
        if near is not None and near.isVisible():
            g = near.geometry()
            self.move(g.center().x() - self.width() // 2, g.top() + 20)
        self.show()
        self.raise_()
        self.activateWindow()
        self.name.setFocus()
        self.name.selectAll()

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            return
        kind = "transformer" if self.rb_transform.isChecked() else "repondre"
        actions.add_from_question(name, self.question, kind)
        self.saved.emit(name)
        self.accept()


# =========================================================================== éditeur
class _Relay(QObject):
    piece = Signal(str)
    done = Signal(str)


class ActionEditor(QDialog):
    """Créer ou modifier une action. Le mode avancé reste caché tant qu'on ne le
    coche pas."""
    saved = Signal()

    def __init__(self, action: dict | None, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vocalis — Action")
        self.setStyleSheet(_style())
        self.resize(640, 720)
        self.cfg = cfg
        self.original = action
        a = action or {"nom": "", "consigne": "", "type": "repondre", "lire": True,
                       "expert": False, "prompt": "", "systeme": "", "creativite": 0.3,
                       "modele": "", "actif": True}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setStyleSheet("background: #F4F4F6;")
        area.setWidget(body)
        outer.addWidget(area, 1)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 10)
        lay.setSpacing(8)

        lay.addWidget(QLabel("<b>Nom du bouton</b>"))
        self.name = QLineEdit(a["nom"])
        self.name.setPlaceholderText("ex. Plus poli")
        lay.addWidget(self.name)

        lay.addWidget(QLabel("<b>Ce que fait l'action</b> (une phrase, comme tu le dirais)"))
        self.instruction = QPlainTextEdit(a["consigne"])
        self.instruction.setPlaceholderText("ex. Rends ce texte plus poli sans changer le sens.")
        self.instruction.setFixedHeight(64)
        lay.addWidget(self.instruction)

        lay.addWidget(QLabel("<b>Le résultat</b>"))
        self.rb_answer = QRadioButton("💬 S'affiche dans la carte (répondre, résumer, expliquer…)")
        self.rb_transform = QRadioButton("✏️ Remplace le texte sélectionné, après aperçu "
                                         "(réécrire, corriger, traduire…)")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_answer)
        grp.addButton(self.rb_transform)
        (self.rb_transform if a["type"] == "transformer" else self.rb_answer).setChecked(True)
        lay.addWidget(self.rb_answer)
        lay.addWidget(self.rb_transform)
        self.chk_read = QCheckBox("Lire le résultat à voix haute")
        self.chk_read.setChecked(bool(a.get("lire", True)))
        lay.addWidget(self.chk_read)

        # ---------------------------------------------------------------- mode avancé
        self.chk_expert = QCheckBox("Mode avancé : écrire le prompt complet")
        self.chk_expert.setChecked(bool(a.get("expert")))
        lay.addWidget(self.chk_expert)
        self.expert = QFrame()
        self.expert.setObjectName("card")
        ex = QVBoxLayout(self.expert)
        ex.setContentsMargins(14, 10, 14, 12)
        ex.setSpacing(6)
        ex.addWidget(QLabel("<b>Prompt complet</b> (envoyé tel quel au modèle)"))
        self.prompt = QPlainTextEdit(a.get("prompt", ""))
        self.prompt.setPlaceholderText(
            "ex.\nTu es un relecteur exigeant. Voici un texte :\n{texte}\n\nRelève les "
            "trois faiblesses principales et propose une version améliorée en {langue}.")
        self.prompt.setFixedHeight(120)
        ex.addWidget(self.prompt)
        ex.addWidget(_hint("Variables : {texte} = le texte sélectionné · {question} = ce que "
                           "tu dis en plus en cliquant · {langue} = ta langue. Sans {texte}, "
                           "le texte est ajouté à la fin. Ce prompt remplace « Ce que fait "
                           "l'action »."))
        ex.addWidget(QLabel("<b>Instructions système</b> (facultatif)"))
        self.system = QPlainTextEdit(a.get("systeme", ""))
        self.system.setPlaceholderText("ex. Tu réponds toujours en trois phrases au plus.")
        self.system.setFixedHeight(52)
        ex.addWidget(self.system)
        row = QHBoxLayout()
        row.addWidget(QLabel("Créativité :"))
        row.addWidget(QLabel("précis"))
        self.temp = QSlider(Qt.Horizontal)
        self.temp.setRange(0, 100)
        self.temp.setValue(int(round(float(a.get("creativite", 0.3)) * 100)))
        self.temp.setFixedWidth(160)
        row.addWidget(self.temp)
        row.addWidget(QLabel("créatif"))
        row.addSpacing(16)
        row.addWidget(QLabel("Modèle :"))
        self.model = QComboBox()
        self.model.addItem("Automatique", "")
        for m in self._models():
            self.model.addItem(m, m)
        i = self.model.findData(a.get("modele", ""))
        if i < 0 and a.get("modele"):
            self.model.addItem(a["modele"], a["modele"])
            i = self.model.count() - 1
        self.model.setCurrentIndex(max(0, i))
        row.addWidget(self.model)
        row.addStretch(1)
        ex.addLayout(row)
        lay.addWidget(self.expert)
        self.chk_expert.toggled.connect(self._toggle_expert)
        self._toggle_expert(self.chk_expert.isChecked())

        # ---------------------------------------------------------------- essai
        lay.addWidget(QLabel("<b>Essayer</b> sur un texte d'exemple"))
        self.sample = QPlainTextEdit(SAMPLE)
        self.sample.setFixedHeight(56)
        lay.addWidget(self.sample)
        trow = QHBoxLayout()
        self.btn_try = QPushButton("▶ Essayer")
        self.btn_try.clicked.connect(self._try)
        trow.addWidget(self.btn_try)
        self.lbl_try = QLabel("")
        self.lbl_try.setObjectName("hint")
        trow.addWidget(self.lbl_try, 1)
        lay.addLayout(trow)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setFixedHeight(90)
        self.result.hide()
        lay.addWidget(self.result)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(22, 10, 22, 14)
        if action is not None:
            delete = QPushButton("Supprimer")
            delete.clicked.connect(self._delete)
            bottom.addWidget(delete)
        bottom.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Enregistrer")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        save.setDefault(True)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        outer.addLayout(bottom)

        self._relay = _Relay()
        self._relay.piece.connect(self._on_piece)
        self._relay.done.connect(self._on_done)
        self.rb_transform.toggled.connect(
            lambda on: self.chk_read.setChecked(False) if on else self.chk_read.setChecked(True))

    def _models(self) -> list[str]:
        try:
            import requests
            url = self.cfg["nettoyage"]["url"].rstrip("/")
            r = requests.get(f"{url}/api/tags", timeout=1.5)
            return sorted(m["name"] for m in r.json().get("models", []))
        except Exception:
            return []

    def _toggle_expert(self, on: bool) -> None:
        self.expert.setVisible(on)
        self.instruction.setEnabled(not on)

    def current(self) -> dict:
        a = dict(self.original or {})
        a.update({"nom": self.name.text().strip(),
                  "consigne": self.instruction.toPlainText().strip(),
                  "type": "transformer" if self.rb_transform.isChecked() else "repondre",
                  "lire": self.chk_read.isChecked(),
                  "expert": self.chk_expert.isChecked(),
                  "prompt": self.prompt.toPlainText(),
                  "systeme": self.system.toPlainText(),
                  "creativite": self.temp.value() / 100,
                  "modele": self.model.currentData() or ""})
        a.setdefault("actif", True)
        return a

    def _check(self, a: dict) -> str:
        if not a["nom"]:
            return "Donne un nom au bouton."
        if a["expert"] and not a["prompt"].strip():
            return "Mode avancé : écris le prompt complet (ou décoche le mode avancé)."
        if not a["expert"] and not a["consigne"]:
            return "Écris en une phrase ce que fait l'action."
        return ""

    def _try(self) -> None:
        a = self.current()
        error = self._check(a)
        if error:
            self.lbl_try.setText(error)
            return
        from .assistant import Conversation, NotInstalled
        conv = Conversation(self.cfg, self.sample.toPlainText(),
                            self.cfg["dictee"].get("langue_preferee", "fr") or "fr")
        self.result.clear()
        self.result.show()
        self.btn_try.setEnabled(False)
        self.lbl_try.setText("…")

        def work():
            try:
                conv.check()
                out = ""
                for p in conv.run_action(a):
                    out += p
                    self._relay.piece.emit(p)
                self._relay.done.emit("")
            except NotInstalled:
                self._relay.done.emit("L'assistant n'est pas installé (Paramètres > État > "
                                      "Réparer).")
            except Exception as exc:
                self._relay.done.emit(f"Erreur : {exc}")

        threading.Thread(target=work, daemon=True, name="essai-action").start()

    def _on_piece(self, p: str) -> None:
        self.result.insertPlainText(p)

    def _on_done(self, msg: str) -> None:
        self.btn_try.setEnabled(True)
        self.lbl_try.setText(msg)

    def _save(self) -> None:
        a = self.current()
        error = self._check(a)
        if error:
            QMessageBox.warning(self, "Vocalis", error)
            return
        acts = actions.load()
        if self.original is not None:
            acts = [a if x["id"] == self.original["id"] else x for x in acts]
        else:
            acts.append(a)
        actions.save(acts)
        self.saved.emit()
        self.accept()

    def _delete(self) -> None:
        if QMessageBox.question(self, "Vocalis", f"Supprimer l'action « {self.original['nom']} » ?"
                                ) != QMessageBox.Yes:
            return
        actions.save([x for x in actions.load() if x["id"] != self.original["id"]])
        self.saved.emit()
        self.accept()


# =========================================================================== page
class ActionsPanel(QWidget):
    """Contenu de la page Actions des paramètres (enregistré tout de suite)."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        t = self.table = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Bouton", "Nom", "Résultat", "Utilisée"])
        t.verticalHeader().setVisible(False)
        h = t.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setMinimumHeight(260)
        t.setStyleSheet("QTableWidget { background: white; border: 1px solid #E3E3E8; "
                        "border-radius: 6px; gridline-color: #EEEEF1; }")
        t.cellDoubleClicked.connect(lambda r, c: self._edit())
        t.itemChanged.connect(self._toggled)
        lay.addWidget(t)
        row = QHBoxLayout()
        for label, fn, primary in (("+ Nouvelle action", self._new, True),
                                   ("Modifier", self._edit, False),
                                   ("Remettre les exemples", self._reset, False)):
            b = QPushButton(label)
            if primary:
                b.setStyleSheet("QPushButton { background: #E5484D; color: white; border: none;"
                                " font-weight: 600; padding: 6px 14px; border-radius: 7px; }"
                                "QPushButton:hover { background: #D33C41; }")
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        t = self.table
        t.setRowCount(0)
        for a in actions.load():
            r = t.rowCount()
            t.insertRow(r)
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if a["actif"] else Qt.Unchecked)
            chk.setData(Qt.UserRole, a["id"])
            t.setItem(r, 0, chk)
            name = a["nom"] + ("   · avancé" if a["expert"] else "")
            t.setItem(r, 1, QTableWidgetItem(name))
            t.setItem(r, 2, QTableWidgetItem(TYPE_LABEL[a["type"]]))
            t.setItem(r, 3, QTableWidgetItem(f"{a['usages']} fois" if a["usages"] else "—"))
        self._loading = False

    def _toggled(self, item) -> None:
        if self._loading or item.column() != 0:
            return
        acts = actions.load()
        for a in acts:
            if a["id"] == item.data(Qt.UserRole):
                a["actif"] = item.checkState() == Qt.Checked
        actions.save(acts)

    def _selected(self) -> dict | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        return actions.get(self.table.item(r, 0).data(Qt.UserRole))

    def _open(self, action) -> None:
        ed = ActionEditor(action, self.cfg, self.window())
        ed.saved.connect(self.refresh)
        ed.exec()

    def _new(self) -> None:
        self._open(None)

    def _edit(self) -> None:
        a = self._selected()
        if a is not None:
            self._open(a)

    def _reset(self) -> None:
        """Remet les exemples manquants (sans toucher aux actions créées)."""
        acts = actions.load()
        have = {a["id"] for a in acts}
        for d in actions.DEFAULTS:
            if d[0] not in have:
                acts.append(actions._default(*d))
        actions.save(acts)
        self.refresh()


def screen_center(w: QWidget) -> None:
    geo = QGuiApplication.primaryScreen().availableGeometry()
    w.move(geo.center() - w.rect().center())
