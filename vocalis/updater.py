"""Mise à jour automatique des bibliothèques au lancement.

Quand une nouvelle version de Vocalis change requirements.txt, le prochain
lancement installe ce qui manque (pip), avec une petite fenêtre de progression,
puis démarre normalement.

N'utilise que la bibliothèque standard (et PySide6 pour la fenêtre) : ce module
tourne avant le chargement du reste de l'application.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
STAMP = VENV / "vocalis_bibliotheques.txt"
LOG = ROOT / "journal_installation" / "mise_a_jour_auto.log"
SERVER_NAME = "Vocalis_Commandes"


def _req_files() -> list[Path]:
    files = [ROOT / "requirements.txt"]
    gpu = ROOT / "requirements-gpu.txt"
    if gpu.exists():
        try:
            from .hardware import has_nvidia_gpu
            if has_nvidia_gpu():
                files.append(gpu)
        except Exception:
            pass
    return [f for f in files if f.exists()]


def _digest(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    h.update(sys.version.encode())
    return h.hexdigest()


def in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def needed() -> bool:
    if not in_venv():
        return False                      # lancé hors de l'installation : on ne touche à rien
    try:
        return STAMP.read_text(encoding="utf-8").strip() != _digest(_req_files())
    except OSError:
        return True


def mark_done() -> None:
    STAMP.write_text(_digest(_req_files()), encoding="utf-8")


def pip_command() -> list[str]:
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":          # pip a besoin de la version console
        python = python.with_name("python.exe")
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
           "--no-input", "--progress-bar", "off"]
    for f in _req_files():
        cmd += ["-r", str(f)]
    return cmd


def _stop_running_instance() -> None:
    """Une ancienne version tourne encore : on lui demande de se fermer (ses
    fichiers bloqueraient la mise à jour)."""
    try:
        from PySide6.QtNetwork import QLocalSocket
    except Exception:
        return
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(500):
        return
    sock.write(b"quitter")
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    for _ in range(50):                    # jusqu'à 10 s
        time.sleep(0.2)
        probe = QLocalSocket()
        probe.connectToServer(SERVER_NAME)
        if not probe.waitForConnected(200):
            time.sleep(0.5)
            return
        probe.disconnectFromServer()


def run_with_window() -> bool:
    """Installe les bibliothèques avec une petite fenêtre. Renvoie True si tout va bien."""
    from PySide6.QtCore import QEventLoop, QProcess, Qt, QTimer
    from PySide6.QtWidgets import (QApplication, QLabel, QMessageBox, QProgressBar,
                                   QVBoxLayout, QWidget)

    app = QApplication.instance() or QApplication(sys.argv)
    _stop_running_instance()

    win = QWidget(None, Qt.Window | Qt.WindowStaysOnTopHint)
    win.setWindowTitle("Vocalis")
    win.setStyleSheet("QWidget { font-family: 'Segoe UI'; font-size: 10pt; }"
                      "QLabel#t { font-size: 12pt; font-weight: 600; }")
    lay = QVBoxLayout(win)
    lay.setContentsMargins(22, 18, 22, 18)
    title = QLabel("Mise à jour de Vocalis…")
    title.setObjectName("t")
    detail = QLabel("Installation des nouveaux composants. Cela peut prendre une ou deux "
                    "minutes, une seule fois.")
    detail.setWordWrap(True)
    line = QLabel("")
    line.setStyleSheet("color: #6E6E73; font-size: 9pt;")
    bar = QProgressBar()
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    for wdg in (title, detail, bar, line):
        lay.addWidget(wdg)
    win.setFixedWidth(460)
    win.show()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} : {' '.join(pip_command())}\n")
    proc = QProcess()
    proc.setProcessChannelMode(QProcess.MergedChannels)
    proc.setWorkingDirectory(str(ROOT))

    def read():
        text = bytes(proc.readAll()).decode("utf-8", errors="replace")
        log.write(text)
        last = [l.strip() for l in text.splitlines() if l.strip()]
        if last:
            msg = last[-1]
            line.setText(msg if len(msg) < 70 else msg[:67] + "…")

    loop = QEventLoop()
    proc.readyRead.connect(read)
    proc.finished.connect(lambda *_: loop.quit())
    proc.errorOccurred.connect(lambda *_: loop.quit())
    cmd = pip_command()
    proc.start(cmd[0], cmd[1:])
    QTimer.singleShot(30 * 60 * 1000, loop.quit)          # garde-fou : 30 min
    loop.exec()
    read()
    ok = proc.state() == QProcess.NotRunning and proc.exitStatus() == QProcess.NormalExit \
        and proc.exitCode() == 0
    if proc.state() != QProcess.NotRunning:
        proc.kill()
        proc.waitForFinished(3000)
    log.write(f"==== fin : {'OK' if ok else 'ÉCHEC'} (code {proc.exitCode()})\n")
    log.close()
    win.close()
    if ok:
        mark_done()
    else:
        QMessageBox.warning(None, "Vocalis",
                            "La mise à jour automatique n'a pas abouti (Internet coupé ?).\n"
                            "Vocalis démarre quand même ; elle sera retentée au prochain "
                            "lancement.\n\nDétails : journal_installation\\"
                            "mise_a_jour_auto.log")
    app.processEvents()
    return ok


def ensure_up_to_date() -> None:
    """Appelé au tout début du lancement."""
    if "--sans-mise-a-jour" in sys.argv or not needed():
        return
    try:
        run_with_window()
    except Exception as exc:          # ne jamais empêcher le démarrage
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"Erreur de la mise à jour automatique : {exc!r}\n")
        except OSError:
            pass


if __name__ == "__main__":
    # « python -m vocalis.updater --marquer » : après l'installation des bibliothèques.
    if "--marquer" in sys.argv:
        mark_done()
    else:
        sys.exit(subprocess.call(pip_command()))
