"""Alt + clic pendant une lecture : capté dans toutes les fenêtres et « avalé »,
pour que l'application en dessous ne reçoive pas le clic (pas de lien ouvert)."""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Callable

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
WH_MOUSE_LL = 14
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_QUIT = 0x0012
VK_MENU = 0x12
VK_NONAME_DUMMY = 0xE8            # touche « vide » : empêche Alt d'ouvrir les menus


class AltClickHook:
    def __init__(self, on_click: Callable[[int, int], None],
                 on_plain_click: Callable[[int, int, bool], None] | None = None):
        self.on_click = on_click
        # Essai : clic simple (False) ou double-clic (True) pendant une lecture.
        self.on_plain_click = on_plain_click
        self.plain_clicks = False
        self._last_down = (0, 0, 0)          # (instant ms, x, y)
        self.active = False
        self._swallow_up = False
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._proc = None

    def start(self) -> None:
        if not IS_WINDOWS or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="souris")
        self._thread.start()

    def stop(self) -> None:
        if IS_WINDOWS and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                          wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                             wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short

        def proc(n_code, w_param, l_param):
            if n_code == 0 and self.active:
                if w_param == WM_LBUTTONDOWN and user32.GetAsyncKeyState(VK_MENU) & 0x8000:
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    self._swallow_up = True
                    x, y = info.pt.x, info.pt.y
                    threading.Thread(target=self._fire, args=(x, y), daemon=True).start()
                    return 1
                if w_param == WM_LBUTTONUP and self._swallow_up:
                    self._swallow_up = False
                    return 1
                if w_param == WM_LBUTTONDOWN and self.plain_clicks and self.on_plain_click:
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    x, y, t = info.pt.x, info.pt.y, int(info.time)
                    t0, x0, y0 = self._last_down
                    double = (0 <= t - t0 <= dbl_time and abs(x - x0) <= dbl_dx
                              and abs(y - y0) <= dbl_dy)
                    self._last_down = (0, 0, 0) if double else (t, x, y)
                    threading.Thread(target=self.on_plain_click, args=(x, y, double),
                                     daemon=True).start()
                    # le clic n'est pas avalé : l'application le reçoit normalement
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        dbl_time = int(user32.GetDoubleClickTime() or 500)
        dbl_dx = max(4, int(user32.GetSystemMetrics(36)))      # SM_CXDOUBLECLK
        dbl_dy = max(4, int(user32.GetSystemMetrics(37)))      # SM_CYDOUBLECLK
        self._proc = HOOKPROC(proc)            # garde une référence (sinon plantage)
        self._thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        if not hook:
            log.warning("Crochet souris impossible (Alt+clic indisponible)")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    def _fire(self, x: int, y: int) -> None:
        # Alt relâché seul ouvrirait les menus de l'application : une touche
        # « vide » envoyée pendant qu'Alt est enfoncé l'en empêche.
        try:
            from . import winutil
            winutil.press_key(VK_NONAME_DUMMY)
        except Exception:
            pass
        log.info("Alt+clic en (%d, %d)", x, y)
        self.on_click(x, y)
