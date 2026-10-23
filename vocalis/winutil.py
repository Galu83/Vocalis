"""Fonctions Windows bas niveau : saisie clavier simulée, presse-papiers, fenêtre active.

La saisie passe par SendInput en mode Unicode : elle ne dépend ni de la disposition
du clavier (AZERTY, QWERTY...) ni du presse-papiers, et gère les accents.
"""
from __future__ import annotations

import ctypes
import sys
import time

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183
_MARK = 0x564F4958  # "VOIX" : marque nos frappes simulées


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> "INPUT":
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, _MARK)
    return inp


def _send(inputs: list) -> None:
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def type_text(text: str, chunk: int = 40) -> None:
    """Tape le texte à l'endroit du curseur, dans la fenêtre active."""
    if not IS_WINDOWS or not text:
        return
    batch: list = []
    for ch in text:
        if ch == "\r":
            continue
        if ch == "\n":
            batch += [_key(vk=VK_RETURN), _key(vk=VK_RETURN, flags=KEYEVENTF_KEYUP)]
        else:
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):  # gère les caractères hors BMP (paires)
                unit = int.from_bytes(data[i:i + 2], "little")
                batch += [
                    _key(scan=unit, flags=KEYEVENTF_UNICODE),
                    _key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                ]
        if len(batch) >= chunk * 2:
            _send(batch)
            batch = []
            time.sleep(0.002)
    _send(batch)


def press_key(vk: int, times: int = 1) -> None:
    if not IS_WINDOWS:
        return
    for start in range(0, times, 50):
        n = min(50, times - start)
        batch = []
        for _ in range(n):
            batch += [_key(vk=vk), _key(vk=vk, flags=KEYEVENTF_KEYUP)]
        _send(batch)
        time.sleep(0.003)


def backspace(times: int) -> None:
    press_key(VK_BACK, times)


VK_LEFT = 0x25
VK_V = 0x56


def select_left(count: int) -> None:
    """Maj + Flèche gauche, count fois : sélectionne le texte avant le curseur."""
    if not IS_WINDOWS or count <= 0:
        return
    _send([_key(vk=VK_SHIFT)])
    try:
        for start in range(0, count, 50):
            n = min(50, count - start)
            batch = []
            for _ in range(n):
                batch += [_key(vk=VK_LEFT), _key(vk=VK_LEFT, flags=KEYEVENTF_KEYUP)]
            _send(batch)
            time.sleep(0.003)
    finally:
        _send([_key(vk=VK_SHIFT, flags=KEYEVENTF_KEYUP)])


def send_ctrl_v() -> None:
    if not IS_WINDOWS:
        return
    _send([
        _key(vk=VK_CONTROL),
        _key(vk=VK_V),
        _key(vk=VK_V, flags=KEYEVENTF_KEYUP),
        _key(vk=VK_CONTROL, flags=KEYEVENTF_KEYUP),
    ])


def paste_text(text: str) -> None:
    """Colle le texte (une seule étape d'annulation), puis restaure le presse-papiers."""
    if not IS_WINDOWS or not text:
        return
    previous = get_clipboard_text() if clipboard_has_text() else None
    if not set_clipboard_text(text):
        type_text(text)
        return
    time.sleep(0.03)
    send_ctrl_v()
    time.sleep(0.4)           # laisse l'application lire le presse-papiers
    if previous is not None:
        set_clipboard_text(previous)


def send_ctrl_c() -> None:
    if not IS_WINDOWS:
        return
    _send([
        _key(vk=VK_CONTROL),
        _key(vk=VK_C),
        _key(vk=VK_C, flags=KEYEVENTF_KEYUP),
        _key(vk=VK_CONTROL, flags=KEYEVENTF_KEYUP),
    ])


def modifiers_down() -> bool:
    if not IS_WINDOWS:
        return False
    return any(user32.GetAsyncKeyState(vk) & 0x8000
               for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def wait_modifiers_released(timeout: float = 1.5) -> None:
    end = time.monotonic() + timeout
    while modifiers_down() and time.monotonic() < end:
        time.sleep(0.02)


def foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    return int(user32.GetForegroundWindow() or 0)


# ---------------------------------------------------------------- presse-papiers

def _open_clipboard() -> bool:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber()) if IS_WINDOWS else 0


def clipboard_has_text() -> bool:
    return bool(IS_WINDOWS and user32.IsClipboardFormatAvailable(CF_UNICODETEXT))


def get_clipboard_text() -> str | None:
    if not IS_WINDOWS or not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    if not IS_WINDOWS:
        return False
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not _open_clipboard():
        kernel32.GlobalFree(handle)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True  # le système possède désormais la mémoire
    finally:
        user32.CloseClipboard()


def copy_selection(timeout: float = 0.5) -> str:
    """Copie le texte sélectionné dans la fenêtre active (Ctrl+C) puis restaure
    le texte qui se trouvait dans le presse-papiers."""
    if not IS_WINDOWS:
        return ""
    wait_modifiers_released()
    previous = get_clipboard_text() if clipboard_has_text() else None
    seq = clipboard_sequence()
    send_ctrl_c()
    end = time.monotonic() + timeout
    while clipboard_sequence() == seq and time.monotonic() < end:
        time.sleep(0.02)
    if clipboard_sequence() == seq:
        return ""  # rien n'a été copié : pas de sélection
    time.sleep(0.03)
    text = get_clipboard_text() or ""
    if previous is not None:
        set_clipboard_text(previous)
    return text


# ---------------------------------------------------------------- instance unique

_mutex = None


def acquire_single_instance(name: str = "Vocalis_Instance_Unique") -> bool:
    global _mutex
    if not IS_WINDOWS:
        return True
    handle = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)   # sinon le verrou resterait vivant
        return False
    _mutex = handle
    return True


# ---------------------------------------------------------------- raccourcis Windows

def startup_shortcut():
    import os
    from pathlib import Path
    return (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / "Vocalis.lnk")


def is_autostart() -> bool:
    return IS_WINDOWS and startup_shortcut().exists()


def create_shortcut(lnk, arguments: str = "-m vocalis") -> bool:
    """Crée un raccourci .lnk qui lance Vocalis sans console."""
    if not IS_WINDOWS:
        return False
    import os
    import subprocess
    from pathlib import Path

    from .config import ICON_PATH, ROOT
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    env = dict(os.environ, VL_LNK=str(lnk), VL_TARGET=str(pythonw), VL_ARGS=arguments,
               VL_DIR=str(ROOT), VL_ICO=str(ICON_PATH) if ICON_PATH.exists() else "")
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:VL_LNK);"
              "$s.TargetPath=$env:VL_TARGET;$s.Arguments=$env:VL_ARGS;"
              "$s.WorkingDirectory=$env:VL_DIR;$s.Description='Vocalis';"
              "if($env:VL_ICO){$s.IconLocation=$env:VL_ICO};$s.Save()")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, timeout=30, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return Path(lnk).exists()
    except Exception:
        return False


def set_autostart(enabled: bool) -> bool:
    lnk = startup_shortcut()
    if not enabled:
        try:
            lnk.unlink(missing_ok=True)
            return True
        except OSError:
            return False
    lnk.parent.mkdir(parents=True, exist_ok=True)
    return create_shortcut(lnk)


# ---------------------------------------------------------------- captures d'écran

WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_from_capture(widget) -> None:
    """Rend une fenêtre de l'outil invisible aux captures d'écran (Windows 10 2004+) :
    le cadre jaune ne perturbe ni l'OCR ni le suivi du défilement."""
    if not IS_WINDOWS:
        return
    try:
        user32.SetWindowDisplayAffinity(ctypes.c_void_p(int(widget.winId())),
                                        WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass
