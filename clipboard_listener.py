"""
clipboard_listener.py – Écouteur du presse-papiers Windows (Win32).

Utilise AddClipboardFormatListener pour recevoir des notifications push
(WM_CLIPBOARDUPDATE) à chaque copie — sans polling.
"""

import ctypes
import threading
import time
from ctypes import wintypes


user32   = ctypes.WinDLL("user32",   use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY         = 0x0002
HWND_MESSAGE       = wintypes.HWND(-3)
CF_DIB             = 8
CF_UNICODETEXT     = 13
GMEM_MOVEABLE      = 0x0002

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
user32.RegisterClipboardFormatW.argtypes       = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype        = wintypes.UINT
user32.EmptyClipboard.argtypes                 = []
user32.SetClipboardData.argtypes               = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype                = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype      = wintypes.DWORD

kernel32.GlobalAlloc.argtypes                  = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype                   = wintypes.HGLOBAL
kernel32.GlobalFree.argtypes                   = [wintypes.HGLOBAL]
kernel32.GlobalLock.argtypes                   = [wintypes.HANDLE]
kernel32.GlobalLock.restype                    = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes                 = [wintypes.HANDLE]
kernel32.GetCurrentThreadId.restype            = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes             = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype              = wintypes.HMODULE


# Marqueurs de confidentialité Windows utilisés par les gestionnaires de mots
# de passe (Bitwarden, KeePass, 1Password…) et Office pour exclure un contenu
# de l'historique du presse-papiers.
CF_CAN_INCLUDE_IN_HISTORY = user32.RegisterClipboardFormatW(
    "CanIncludeInClipboardHistory")
CF_EXCLUDE_FROM_MONITORING = user32.RegisterClipboardFormatW(
    "ExcludeClipboardContentFromMonitorProcessing")
CF_CAN_UPLOAD_TO_CLOUD = user32.RegisterClipboardFormatW(
    "CanUploadToCloudClipboard")
CF_PNG = user32.RegisterClipboardFormatW("PNG")
CF_HTML = user32.RegisterClipboardFormatW("HTML Format")


def _is_clipboard_private() -> bool:
    """True si l'app source a marqué le contenu comme confidentiel."""
    try:
        if CF_EXCLUDE_FROM_MONITORING and \
                user32.IsClipboardFormatAvailable(CF_EXCLUDE_FROM_MONITORING):
            return True
        for fmt in (CF_CAN_INCLUDE_IN_HISTORY, CF_CAN_UPLOAD_TO_CLOUD):
            if not fmt or not user32.IsClipboardFormatAvailable(fmt):
                continue
            h = user32.GetClipboardData(fmt)
            if not h:
                continue
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                continue
            try:
                if ctypes.c_uint32.from_address(ptr).value == 0:
                    return True
            finally:
                kernel32.GlobalUnlock(h)
    except Exception:
        pass
    return False


def _open_clipboard(hwnd=None, retries: int = 25) -> bool:
    """OpenClipboard échoue si une autre app le tient ouvert : on réessaie."""
    for _ in range(retries):
        if user32.OpenClipboard(hwnd):
            return True
        time.sleep(0.02)
    return False


def _read_clipboard_text(skip_private: bool = True) -> str | None:
    """Lit le texte du presse-papiers en CF_UNICODETEXT, ou None.
    Retourne None si l'app source a marqué le contenu comme confidentiel
    (sauf skip_private=False, pour un collage demandé par l'utilisateur)."""
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    if not _open_clipboard():
        return None
    try:
        if skip_private and _is_clipboard_private():
            return None
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


def get_clipboard_text() -> str:
    """Texte actuel du presse-papiers (collage explicite), ou chaîne vide."""
    return _read_clipboard_text(skip_private=False) or ""


def _set_clipboard(items: list[tuple[int, bytes]]) -> bool:
    """Remplace le contenu du presse-papiers par les formats donnés
    [(format, octets)…]. Vrai si le premier format a pu être déposé.

    Le presse-papiers est ouvert avec une fenêtre invisible comme propriétaire :
    ouvert sans fenêtre, EmptyClipboard le laisse sans propriétaire et
    SetClipboardData peut alors échouer (documentation Win32)."""
    owner = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0,
                                   None, None, None, None)   # jamais affichée
    try:
        if not _open_clipboard(owner):
            return False
        try:
            user32.EmptyClipboard()
            ok = []
            for fmt, data in items:
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    ok.append(False)
                    continue
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    kernel32.GlobalFree(h)
                    ok.append(False)
                    continue
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(h)
                if not user32.SetClipboardData(fmt, h):
                    kernel32.GlobalFree(h)
                    ok.append(False)
                    continue
                ok.append(True)   # le système est désormais propriétaire de h
            return bool(ok and ok[0])
        finally:
            user32.CloseClipboard()
    finally:
        if owner:
            user32.DestroyWindow(owner)


def set_clipboard_text(text: str) -> bool:
    """Place du texte dans le presse-papiers Windows (CF_UNICODETEXT)."""
    return _set_clipboard([(CF_UNICODETEXT, text.encode("utf-16-le", "surrogatepass") + b"\0\0")])


def _image_formats(image) -> list[tuple[int, bytes]]:
    """CF_DIB (lu par toutes les applications) et PNG (Office, navigateurs…
    conservent la transparence)."""
    import io
    bmp = io.BytesIO()
    image.convert("RGB").save(bmp, "BMP")
    items = [(CF_DIB, bmp.getvalue()[14:])]   # DIB = BMP sans son en-tête de fichier
    if CF_PNG:
        png = io.BytesIO()
        image.save(png, "PNG")
        items.append((CF_PNG, png.getvalue()))
    return items


def _html_format(fragment: str) -> bytes:
    """Fragment HTML au format « HTML Format » de Windows (en-tête d'offsets en octets)."""
    header = ("Version:0.9\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\n"
              "StartFragment:{:010d}\r\nEndFragment:{:010d}\r\n")
    prefix = "<html><body><!--StartFragment-->"
    suffix = "<!--EndFragment--></body></html>"
    start_html = len(header.format(0, 0, 0, 0).encode("utf-8"))
    start_frag = start_html + len(prefix.encode("utf-8"))
    end_frag = start_frag + len(fragment.encode("utf-8"))
    end_html = end_frag + len(suffix.encode("utf-8"))
    doc = header.format(start_html, end_html, start_frag, end_frag) + prefix + fragment + suffix
    return doc.encode("utf-8") + b"\0"


def set_clipboard_image(image) -> bool:
    """Place une image PIL dans le presse-papiers."""
    return _set_clipboard(_image_formats(image))


def set_clipboard_content(text: str = "", html: str = "", image=None) -> bool:
    """Dépose plusieurs représentations d'un même contenu : chaque application
    colle celle qu'elle sait lire (texte brut, HTML mis en forme avec ses
    images pour Word/Outlook/navigateurs, image seule pour Paint…)."""
    items = []
    if text:
        items.append((CF_UNICODETEXT, text.encode("utf-16-le", "surrogatepass") + b"\0\0"))
    if html and CF_HTML:
        items.append((CF_HTML, _html_format(html)))
    if image is not None:
        items += _image_formats(image)
    return _set_clipboard(items) if items else False


def clipboard_sequence() -> int:
    """Compteur système incrémenté à chaque modification du presse-papiers."""
    return int(user32.GetClipboardSequenceNumber())


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
