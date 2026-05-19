"""
clipboard_listener.py – Écouteur du presse-papiers Windows (Win32).

Utilise AddClipboardFormatListener pour recevoir des notifications push
(WM_CLIPBOARDUPDATE) à chaque copie — sans polling.
"""

import ctypes
import threading
from ctypes import wintypes


user32   = ctypes.WinDLL("user32",   use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY         = 0x0002
HWND_MESSAGE       = wintypes.HWND(-3)
CF_UNICODETEXT     = 13

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style",         wintypes.UINT),
        ("lpfnWndProc",   WNDPROC),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     wintypes.HINSTANCE),
        ("hIcon",         wintypes.HICON),
        ("hCursor",       wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName",  wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Win32 prototypes
user32.RegisterClassW.restype                  = wintypes.ATOM
user32.CreateWindowExW.restype                 = wintypes.HWND
user32.CreateWindowExW.argtypes                = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DefWindowProcW.restype                  = ctypes.c_long
user32.DefWindowProcW.argtypes                 = [wintypes.HWND, wintypes.UINT,
                                                  wintypes.WPARAM, wintypes.LPARAM]
user32.AddClipboardFormatListener.argtypes     = [wintypes.HWND]
user32.RemoveClipboardFormatListener.argtypes  = [wintypes.HWND]
user32.DestroyWindow.argtypes                  = [wintypes.HWND]
user32.GetMessageW.argtypes                    = [ctypes.c_void_p, wintypes.HWND,
                                                  wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes               = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes               = [ctypes.c_void_p]
user32.PostThreadMessageW.argtypes             = [wintypes.DWORD, wintypes.UINT,
                                                  wintypes.WPARAM, wintypes.LPARAM]
user32.OpenClipboard.argtypes                  = [wintypes.HWND]
user32.GetClipboardData.argtypes               = [wintypes.UINT]
user32.GetClipboardData.restype                = wintypes.HANDLE
user32.CloseClipboard.argtypes                 = []
user32.IsClipboardFormatAvailable.argtypes     = [wintypes.UINT]

kernel32.GlobalLock.argtypes                   = [wintypes.HANDLE]
kernel32.GlobalLock.restype                    = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes                 = [wintypes.HANDLE]
kernel32.GetCurrentThreadId.restype            = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes             = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype              = wintypes.HMODULE


def _read_clipboard_text() -> str | None:
    """Lit le texte du presse-papiers en CF_UNICODETEXT, ou None."""
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    if not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


class ClipboardListener:
    """Lance un écouteur Win32 dans un thread dédié.
    `callback(text:str)` est invoqué depuis ce thread à chaque copie texte."""

    def __init__(self, callback):
        self._callback   = callback
        self._thread     = None
        self._hwnd       = None
        self._thread_id  = None
        self._stop       = False
        self._wnd_proc   = None   # garder une réf forte (sinon GC)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ClipboardListener")
        self._thread.start()

    def stop(self):
        self._stop = True
        try:
            if self._hwnd:
                user32.RemoveClipboardFormatListener(self._hwnd)
                user32.DestroyWindow(self._hwnd)
        except Exception:
            pass
        try:
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_DESTROY, 0, 0)
        except Exception:
            pass

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        class_name = f"BoostacheClipListener_{self._thread_id}"

        def wnd_proc(hwnd, msg, wp, lp):
            if msg == WM_CLIPBOARDUPDATE:
                try:
                    text = _read_clipboard_text()
                    if text:
                        try:
                            self._callback(text)
                        except Exception:
                            pass
                except Exception:
                    pass
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._wnd_proc = WNDPROC(wnd_proc)
        hinstance = kernel32.GetModuleHandleW(None)

        wc = WNDCLASS()
        wc.style         = 0
        wc.lpfnWndProc   = self._wnd_proc
        wc.cbClsExtra    = 0
        wc.cbWndExtra    = 0
        wc.hInstance     = hinstance
        wc.hIcon         = None
        wc.hCursor       = None
        wc.hbrBackground = None
        wc.lpszMenuName  = None
        wc.lpszClassName = class_name

        if not user32.RegisterClassW(ctypes.byref(wc)):
            return

        self._hwnd = user32.CreateWindowExW(
            0, class_name, "Boostache Clipboard Listener",
            0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinstance, None)
        if not self._hwnd:
            return

        if not user32.AddClipboardFormatListener(self._hwnd):
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
            return

        # Pompe à messages
        msg = wintypes.MSG()
        while not self._stop:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
