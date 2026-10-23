"""Fenêtre de progression pour les opérations longues (ajouter une langue, réparer
l'installation…) : chaque étape est expliquée en mots simples, avec une barre
d'avancement, puis Vocalis redémarre si nécessaire."""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from .config import ROOT
from .ui_update import STYLE


@dataclass
class Task:
    label: str                                   # « Lecture d'écran en espagnol »
    command: list[str] | None = None             # programme à lancer (ou None)
    func: Callable[[Callable[[str], None]], None] | None = None   # ou fonction Python
    optional: bool = False                       # un échec n'arrête pas la suite


def install_step(step: str, languages: str = "") -> list[str]:
    """Commande d'une étape de vocalis/scripts/install.ps1."""
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle",
           "Hidden", "-File", str(ROOT / "vocalis" / "scripts" / "install.ps1"), "-Step", step]
    if languages:
        cmd += ["-Languages", languages]
    return cmd


def python_module(module: str, *args: str) -> list[str]:
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return [exe, "-m", module, *args]


class _Relay(QObject):
    line = Signal(str)
    step = Signal(int, str)
    done = Signal(bool, str)


class TaskWindow(QWidget):
    def __init__(self, title: str, tasks: list[Task], restart_after: bool,
                 on_restart: Callable[[], None] | None = None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(470)
        self.tasks = tasks
        self.restart_after = restart_after
        self.on_restart = on_restart
        self._relay = _Relay()
        self._relay.line.connect(self._on_line)
        self._relay.step.connect(self._on_step)
        self._relay.done.connect(self._on_done)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("title")
        lay.addWidget(t)
        self.lbl_step = QLabel("Préparation…")
        self.lbl_step.setWordWrap(True)
        lay.addWidget(self.lbl_step)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setObjectName("sub")
        self.lbl_detail.setWordWrap(True)
        lay.addWidget(self.lbl_detail)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn = QPushButton("Patiente…")
        self.btn.setObjectName("later")
        self.btn.setEnabled(False)
        self.btn.clicked.connect(self._close_or_restart)
        row.addWidget(self.btn)
        lay.addSpacing(6)
        lay.addLayout(row)
        self._index = 0

    def start(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2,
                  screen.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        threading.Thread(target=self._work, daemon=True, name="taches").start()

    # ------------------------------------------------------------------ travail
    def _work(self) -> None:
        problems = []
        for i, task in enumerate(self.tasks):
            self._relay.step.emit(i, task.label)
            ok = True
            try:
                if task.func is not None:
                    task.func(self._relay.line.emit)
                elif task.command:
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    proc = subprocess.Popen(task.command, cwd=str(ROOT),
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            creationflags=flags, text=True,
                                            encoding="utf-8", errors="replace")
                    for line in proc.stdout:
                        line = line.strip()
                        if line:
                            self._relay.line.emit(line)
                    ok = proc.wait() in (0, 2)
            except Exception as exc:
                self._relay.line.emit(str(exc))
                ok = False
            if not ok:
                problems.append(task.label)
                if not task.optional:
                    break
        self._relay.done.emit(not problems, ", ".join(problems))

    # ------------------------------------------------------------------ affichage
    def _on_step(self, i: int, label: str) -> None:
        self._index = i
        n = len(self.tasks)
        self.lbl_step.setText(f"Étape {i + 1} sur {n} : {label}")
        self.lbl_detail.setText("")
        self.bar.setValue(int(1000 * i / max(1, n)))

    def _on_line(self, line: str) -> None:
        shown = line if len(line) < 110 else line[:107] + "…"
        self.lbl_detail.setText(shown)
        m = re.search(r"(\d{1,3})\s?%", line)
        if m:
            n = len(self.tasks)
            part = min(100, int(m.group(1))) / 100
            self.bar.setValue(int(1000 * (self._index + part) / max(1, n)))

    def _on_done(self, ok: bool, problems: str) -> None:
        self.bar.setValue(1000)
        if ok:
            self.lbl_step.setText("Terminé." + (" Vocalis va redémarrer." if self.restart_after
                                                else ""))
            self.btn.setText("Redémarrer Vocalis" if self.restart_after else "Fermer")
        else:
            self.lbl_step.setText(f"Terminé avec un problème : {problems}.")
            self.btn.setText("Redémarrer Vocalis" if self.restart_after else "Fermer")
        self.btn.setObjectName("primary")
        self.btn.setStyleSheet("")
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)
        self.btn.setEnabled(True)

    def _close_or_restart(self) -> None:
        self.close()
        if self.restart_after and self.on_restart is not None:
            self.on_restart()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(24, 24, 29, 246))
        p.drawRoundedRect(r, 14, 14)
