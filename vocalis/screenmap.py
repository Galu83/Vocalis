"""Coordonnées d'écran : Windows (pixels physiques) <-> Qt (pixels logiques).

Avec Qt 6 sous Windows, chaque écran a un coin haut-gauche en pixels physiques et
une taille en pixels logiques (divisée par le facteur d'échelle de l'écran).
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF
from PySide6.QtGui import QGuiApplication

IS_WINDOWS = sys.platform == "win32"


# Origine physique de chaque écran, mesurée (voir calibrate) : plus fiable que
# toute hypothèse quand les écrans ont des échelles différentes.
_ORIGINS: dict[str, tuple[float, float]] = {}


def _origin(screen) -> tuple[float, float]:
    g = screen.geometry()
    return _ORIGINS.get(screen.name(), (g.x(), g.y()))


def _native_rect(screen) -> QRect:
    g = screen.geometry()
    dpr = screen.devicePixelRatio()
    ox, oy = _origin(screen)
    return QRect(round(ox), round(oy), round(g.width() * dpr), round(g.height() * dpr))


def calibrate(native: tuple[int, int] | None = None) -> None:
    """Mesure l'origine physique de l'écran sous la souris en comparant la position
    du curseur donnée par Windows (pixels physiques) et par Qt (pixels logiques)."""
    if not IS_WINDOWS:
        return
    from PySide6.QtGui import QCursor
    nx, ny = native or cursor_native()
    lp = QCursor.pos()
    s = QGuiApplication.screenAt(lp)
    if s is None:
        return
    g, dpr = s.geometry(), s.devicePixelRatio()
    _ORIGINS[s.name()] = (nx - (lp.x() - g.x()) * dpr, ny - (lp.y() - g.y()) * dpr)


def screen_at_native(x: float, y: float):
    for s in QGuiApplication.screens():
        if _native_rect(s).contains(int(x), int(y)):
            return s
    return QGuiApplication.primaryScreen()


def to_native(p: QPoint) -> tuple[int, int]:
    s = QGuiApplication.screenAt(p) or QGuiApplication.primaryScreen()
    g, dpr = s.geometry(), s.devicePixelRatio()
    ox, oy = _origin(s)
    return (round(ox + (p.x() - g.x()) * dpr), round(oy + (p.y() - g.y()) * dpr))


def to_logical_point(x: float, y: float) -> QPointF:
    s = screen_at_native(x, y)
    g, dpr = s.geometry(), s.devicePixelRatio()
    ox, oy = _origin(s)
    return QPointF(g.x() + (x - ox) / dpr, g.y() + (y - oy) / dpr)


def to_logical_rect(left: float, top: float, width: float, height: float) -> QRectF:
    s = screen_at_native(left + width / 2, top + height / 2)
    g, dpr = s.geometry(), s.devicePixelRatio()
    ox, oy = _origin(s)
    return QRectF(g.x() + (left - ox) / dpr, g.y() + (top - oy) / dpr,
                  width / dpr, height / dpr)


# ------------------------------------------------------------------ fenêtres Windows
if IS_WINDOWS:
    from ctypes import wintypes
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    GA_ROOT = 2


def cursor_native() -> tuple[int, int]:
    if not IS_WINDOWS:
        return (0, 0)
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def window_rect_at(x: int, y: int) -> QRectF | None:
    """Rectangle (logique) de la fenêtre principale sous le point physique (x, y),
    limité à l'écran qui la contient."""
    if not IS_WINDOWS:
        return None
    hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return None
    root = _user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    r = wintypes.RECT()
    if not _user32.GetWindowRect(root, ctypes.byref(r)):
        return None
    rect = to_logical_rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    s = screen_at_native(x, y)
    return rect.intersected(QRectF(s.geometry()))


def root_window_at(x: int, y: int) -> int:
    """Identifiant de la fenêtre principale sous le point physique (x, y)."""
    if not IS_WINDOWS:
        return 0
    hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return 0
    return int(_user32.GetAncestor(hwnd, GA_ROOT) or hwnd)


# ------------------------------------------------------------------ point d'écriture
if IS_WINDOWS:
    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT)]
    _user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]


def win32_caret_native() -> tuple[float, float, float, float] | None:
    """Curseur de texte des applications classiques (Bloc-notes, Office…), en pixels
    physiques : (gauche, haut, largeur, hauteur). None si l'application n'en publie pas."""
    if not IS_WINDOWS:
        return None
    info = _GUITHREADINFO()
    info.cbSize = ctypes.sizeof(_GUITHREADINFO)
    if not _user32.GetGUIThreadInfo(0, ctypes.byref(info)) or not info.hwndCaret:
        return None
    r = info.rcCaret
    if r.bottom - r.top <= 0:
        return None
    pt = wintypes.POINT(r.left, r.top)
    _user32.ClientToScreen(info.hwndCaret, ctypes.byref(pt))
    return (pt.x, pt.y, max(1, r.right - r.left), r.bottom - r.top)


def mouse_rect() -> QRectF:
    """Petit rectangle autour de la souris (quand on ne trouve pas où l'on écrit)."""
    from PySide6.QtGui import QCursor
    p = QCursor.pos()
    return QRectF(p.x() - 1, p.y() - 10, 2, 20)
