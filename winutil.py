"""
winutil.py – Petits utilitaires Win32 (premier plan, barre de titre sombre,
explorateur, liens externes, interception de la touche Impr. écran).
"""

import ctypes
import os
import subprocess
import threading
import webbrowser
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD]

SW_RESTORE = 9
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_CAPTION_COLOR = 35          # Windows 11 uniquement (ignoré ailleurs)


def is_minimized(hwnd: int) -> bool:
    return bool(hwnd and user32.IsIconic(hwnd))


def restore_window(hwnd: int):
    """Restauration native : WinForms ne sait pas restaurer une fenêtre masquée
    pendant qu'elle était réduite (elle réapparaîtrait hors écran)."""
    if hwnd:
        user32.ShowWindow(hwnd, SW_RESTORE)


def bring_to_front(hwnd: int):
    """Place la fenêtre au premier plan, même si une autre application a le
    focus (Windows bloque SetForegroundWindow sans cette astuce)."""
    if not hwnd:
        return
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        attached = bool(fg_tid and fg_tid != tid and user32.AttachThreadInput(fg_tid, tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, tid, False)
    except Exception:
        pass


def dark_title_bar(hwnd: int, caption_rgb: tuple[int, int, int] | None = None):
    """Barre de titre sombre (Windows 10 20H1+), couleur exacte sous Windows 11."""
    if not hwnd:
        return
    try:
        on = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                                     ctypes.byref(on), ctypes.sizeof(on))
        if caption_rgb:
            r, g, b = caption_rgb
            color = ctypes.c_int(r | (g << 8) | (b << 16))
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR,
                                         ctypes.byref(color), ctypes.sizeof(color))
    except Exception:
        pass


def open_path(path: str):
    os.startfile(path)


def reveal_in_explorer(path: str):
    """Ouvre l'explorateur sur le dossier du fichier, fichier sélectionné."""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        os.startfile(path)
    else:
        subprocess.Popen(f'explorer /select,"{path}"')


def open_url(url: str) -> bool:
    """Ouvre un lien dans le navigateur par défaut (http, https et mailto seulement)."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://", "mailto:")):
        return False
    webbrowser.open(url)
    return True


# ─────────────────────────────────────────────
#  Fenêtres des autres applications : premier plan
# ─────────────────────────────────────────────
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
GW_OWNER = 4
HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _is_topmost(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.splitext(os.path.basename(buf.value))[0]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def list_windows(exclude=(), limit: int = 30) -> list[dict]:
    """Fenêtres d'application visibles (celles de la barre des tâches), de la
    plus récemment utilisée à la plus ancienne."""
    found: list[dict] = []

    def visit(hwnd, _):
        if len(found) >= limit:
            return False
        if (hwnd in exclude or not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER)
                or user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        cloaked = ctypes.c_int(0)   # applications UWP suspendues, autres bureaux virtuels
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in SHELL_CLASSES:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        found.append({"hwnd": int(hwnd), "title": title.value, "app": _process_name(hwnd),
                      "topmost": _is_topmost(hwnd)})
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


def set_topmost(hwnd: int, on: bool) -> bool:
    """Garde une fenêtre au-dessus des autres (ou la libère). Retourne False si
    Windows refuse, par exemple pour une application lancée en administrateur."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    user32.SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    return _is_topmost(hwnd) == on


# ─────────────────────────────────────────────
#  Touche Impr. écran
# ─────────────────────────────────────────────
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_QUIT = 0x0012
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
VK_SNAPSHOT = 0x2C
MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)   # Maj, Ctrl, Alt, Windows gauche/droite

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class PrintScreenHook:
    """Remplace la touche Impr. écran (seule, sans modificateur) par `callback`,
    dans tout Windows. Alt+Impr. écran et Win+Impr. écran gardent leur effet.

    Hook clavier bas niveau dédié plutôt que la librairie keyboard : celle-ci
    attend le scan code 84 alors que la touche physique envoie 55 (étendu), et
    ne bloque pas le relâchement de la touche — Windows copierait alors l'écran
    entier dans le presse-papiers, ou ouvrirait sa propre capture."""

    def __init__(self, callback):
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._proc = None          # référence forte au callback ctypes (sinon GC)
        self._swallowing = False   # appui intercepté : on bloque jusqu'au relâchement
        self._ok = False

    def start(self) -> bool:
        """Installe le hook. Retourne False s'il n'a pas pu l'être."""
        if self._thread:
            return self._ok
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True,
                                        name="PrintScreenHook")
        self._thread.start()
        ready.wait(3)
        return self._ok

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread = None
        self._thread_id = 0

    def _fire(self):
        try:
            self._callback()
        except Exception:
            pass

    def _run(self, ready: threading.Event):
        self._thread_id = kernel32.GetCurrentThreadId()

        def proc(n_code, w_param, l_param):
            # Doit rendre la main vite : Windows retire les hooks trop lents
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == VK_SNAPSHOT:
                    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    if down and not self._swallowing and not any(
                            user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_VKS):
                        self._swallowing = True
                        threading.Thread(target=self._fire, daemon=True,
                                         name="PrintScreen").start()
                    if self._swallowing:
                        if not down:
                            self._swallowing = False
                        return 1
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = HOOKPROC(proc)
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                        kernel32.GetModuleHandleW(None), 0)
        self._ok = bool(hook)
        ready.set()
        if not hook:
            return
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(hook)
