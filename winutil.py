"""
winutil.py – Petits utilitaires Win32 (premier plan et transparence, barre de
titre sombre, explorateur, liens externes, icônes et miniatures des fenêtres,
interception de la touche Impr. écran).
"""

import base64
import ctypes
import io
import os
import subprocess
import threading
import webbrowser
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
shell32 = ctypes.WinDLL("shell32")
dwmapi = ctypes.WinDLL("dwmapi")

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD]

SW_RESTORE = 9
SPI_GETWORKAREA = 0x0030
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_CAPTION_COLOR = 35          # Windows 11 uniquement (ignoré ailleurs)


def is_minimized(hwnd: int) -> bool:
    return bool(hwnd and user32.IsIconic(hwnd))


def is_maximized(hwnd: int) -> bool:
    return bool(hwnd and user32.IsZoomed(hwnd))


def work_area_size() -> tuple[int, int] | None:
    """Zone utile de l'écran principal (sans la barre des tâches), en pixels
    logiques comme les tailles de fenêtre de pywebview."""
    rect = wintypes.RECT()
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return None
    # 96 tant que le processus ne gère pas l'échelle : le rectangle est alors déjà logique
    scale = (user32.GetDpiForSystem() or 96) / 96
    return int((rect.right - rect.left) / scale), int((rect.bottom - rect.top) / scale)


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
#  Fenêtres des autres applications : premier plan et transparence
# ─────────────────────────────────────────────
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x00000002
RDW_INVALIDATE, RDW_ERASE, RDW_ALLCHILDREN, RDW_FRAME = 0x0001, 0x0004, 0x0080, 0x0400
MIN_OPACITY = 20                    # en dessous, la fenêtre devient introuvable
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
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.COLORREF),
                                              ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD)]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _is_topmost(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


_faded: set[int] = set()    # fenêtres que Boostache a rendues transparentes (style ajouté par lui)


def _layered_attributes(hwnd: int) -> tuple[int, int, int] | None:
    """(couleur clé, alpha, drapeaux) d'une fenêtre transparente. None si elle
    dessine sa transparence pixel par pixel (UpdateLayeredWindow)."""
    key, alpha, flags = wintypes.COLORREF(), wintypes.BYTE(), wintypes.DWORD()
    if not user32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)):
        return None
    return key.value, alpha.value, flags.value


def _opacity(hwnd: int) -> int | None:
    """Opacité en pour cent (100 = opaque), None si l'application gère
    elle-même sa transparence."""
    if not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_LAYERED:
        return 100
    attrs = _layered_attributes(hwnd)
    if attrs is None:
        return None
    _, alpha, flags = attrs
    return round(alpha * 100 / 255) if flags & LWA_ALPHA else 100


def _process_path(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _app_path(hwnd: int, cls: str) -> str:
    """Exécutable de l'application. Une application du Store vit dans une
    fenêtre enfant : son cadre appartient à ApplicationFrameHost."""
    if cls == UWP_FRAME_CLASS:
        core = user32.FindWindowExW(hwnd, None, UWP_CORE_CLASS, None)
        if core:
            return _process_path(core) or _process_path(hwnd)
    return _process_path(hwnd)


def list_windows(exclude=(), limit: int = 30) -> list[dict]:
    """Fenêtres d'application visibles (celles de la barre des tâches), dans
    l'ordre d'empilement : celles au premier plan d'abord, puis de la plus
    récemment utilisée à la plus ancienne."""
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
        path = _app_path(hwnd, cls.value)
        found.append({"hwnd": int(hwnd), "title": title.value,
                      "app": os.path.splitext(os.path.basename(path))[0],
                      "icon": window_icon(hwnd, path), "topmost": _is_topmost(hwnd),
                      "opacity": _opacity(hwnd), "minimized": bool(user32.IsIconic(hwnd))})
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


def set_topmost(hwnd: int, on: bool) -> bool | None:
    """Garde une fenêtre au-dessus des autres (ou la libère). Retourne False si
    Windows refuse, par exemple pour une application lancée en administrateur,
    None si la fenêtre n'existe plus."""
    if not hwnd or not user32.IsWindow(hwnd):
        return None
    user32.SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    return _is_topmost(hwnd) == on


def set_opacity(hwnd: int, percent: int) -> bool | None:
    """Rend une fenêtre plus ou moins transparente (100 = opaque). Retourne
    False si Windows refuse (application lancée en administrateur ou qui ne
    répond plus, transparence gérée par l'application), None si la fenêtre
    n'existe plus."""
    if not hwnd or not user32.IsWindow(hwnd):
        return None
    if user32.IsHungAppWindow(hwnd):    # changer son style attendrait sa réponse
        return False
    percent = max(MIN_OPACITY, min(100, int(percent)))
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if style & WS_EX_LAYERED:
        attrs = _layered_attributes(hwnd)
        if attrs is None:
            return False
        key, _, flags = attrs
    elif percent == 100:
        return True
    else:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        if not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_LAYERED:
            return False
        _faded.add(hwnd)
        key, flags = 0, 0
    if percent == 100 and hwnd in _faded:
        # Style retiré plutôt qu'un alpha à 255 : la fenêtre retrouve son rendu d'origine
        _faded.discard(hwnd)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & ~WS_EX_LAYERED)
        user32.RedrawWindow(hwnd, None, None, RDW_ERASE | RDW_INVALIDATE | RDW_FRAME | RDW_ALLCHILDREN)
        return not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_LAYERED
    return bool(user32.SetLayeredWindowAttributes(hwnd, key, round(percent * 255 / 100), flags | LWA_ALPHA))


# ─────────────────────────────────────────────
#  Fenêtres des autres applications : icône et miniature
# ─────────────────────────────────────────────
WM_GETICON = 0x007F
ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2
GCLP_HICON, GCLP_HICONSM = -14, -34
SMTO_ABORTIFHUNG = 0x0002
DI_NORMAL = 0x0003
PW_RENDERFULLCONTENT = 0x0002       # fenêtres accélérées : navigateurs, WebView2, Store
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
UWP_FRAME_CLASS = "ApplicationFrameWindow"
UWP_CORE_CLASS = "Windows.UI.Core.CoreWindow"
ICON_SIZE = 32
THUMB_SIZE = (240, 150)             # 2× la vignette affichée : nette sur écran haute densité
MAX_CAPTURE_PIXELS = 40_000_000


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int,
                              ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.IsHungAppWindow.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
                                   ctypes.POINTER(wintypes.HICON), wintypes.UINT]
shell32.ExtractIconExW.restype = wintypes.UINT
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
_set_thread_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)   # Windows 10 1607+
if _set_thread_dpi:
    _set_thread_dpi.argtypes = [ctypes.c_void_p]
    _set_thread_dpi.restype = ctypes.c_void_p
_window_dpi = getattr(user32, "GetDpiForWindow", None)
if _window_dpi:
    _window_dpi.argtypes = [wintypes.HWND]
    _window_dpi.restype = wintypes.UINT
try:
    _monitor_dpi = ctypes.WinDLL("shcore").GetDpiForMonitor
    _monitor_dpi.argtypes = [wintypes.HMONITOR, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                             ctypes.POINTER(wintypes.UINT)]
except (OSError, AttributeError):
    _monitor_dpi = None
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0

_exe_icons: dict[str, str | None] = {}


class _Canvas:
    """Bitmap 32 bits (lignes de haut en bas) sélectionné dans un DC mémoire."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.dc = gdi32.CreateCompatibleDC(None)
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0)
        self.bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(self.dc, ctypes.byref(header), 0,
                                             ctypes.byref(self.bits), None, 0)
        self.old = gdi32.SelectObject(self.dc, self.bitmap) if self.bitmap else None

    def __enter__(self):
        if not self.bitmap:
            self.__exit__()
            raise OSError("bitmap impossible à créer")
        return self

    def __exit__(self, *_):
        if self.old:
            gdi32.SelectObject(self.dc, self.old)
        if self.bitmap:
            gdi32.DeleteObject(self.bitmap)
        gdi32.DeleteDC(self.dc)

    def fill(self, byte: int):
        ctypes.memset(self.bits, byte, self.width * self.height * 4)

    def image(self):
        from PIL import Image
        gdi32.GdiFlush()
        data = ctypes.string_at(self.bits, self.width * self.height * 4)
        return Image.frombuffer("RGB", (self.width, self.height), data, "raw", "BGRX", 0, 1)


def _data_url(image, fmt: str) -> str:
    buf = io.BytesIO()
    image.save(buf, fmt, **({"quality": 80} if fmt == "JPEG" else {}))
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _render_icon(hicon: int) -> str | None:
    """HICON → PNG transparent. Dessinée sur fond noir puis sur fond blanc,
    l'icône révèle son opacité par l'écart entre les deux, qu'elle ait un
    canal alpha ou un simple masque (anciennes icônes)."""
    from PIL import Image, ImageChops
    with _Canvas(ICON_SIZE, ICON_SIZE) as canvas:
        layers = []
        for byte in (0x00, 0xFF):
            canvas.fill(byte)
            if not user32.DrawIconEx(canvas.dc, 0, 0, hicon, ICON_SIZE, ICON_SIZE, 0, None, DI_NORMAL):
                return None
            layers.append(canvas.image())
    black, white = layers
    alpha = ImageChops.invert(ImageChops.subtract(white, black).convert("L"))
    if not alpha.getbbox():
        return None
    return _data_url(Image.merge("RGBa", (*black.split(), alpha)).convert("RGBA"), "PNG")


def _window_hicon(hwnd: int) -> int:
    """Icône fournie par la fenêtre, sinon par sa classe (à ne pas détruire)."""
    result = ctypes.c_size_t()
    for kind in (ICON_BIG, ICON_SMALL2, ICON_SMALL):
        if (user32.SendMessageTimeoutW(hwnd, WM_GETICON, kind, 0, SMTO_ABORTIFHUNG, 100,
                                       ctypes.byref(result)) and result.value):
            return result.value
    return user32.GetClassLongPtrW(hwnd, GCLP_HICON) or user32.GetClassLongPtrW(hwnd, GCLP_HICONSM)


def _exe_icon(path: str) -> str | None:
    key = path.lower()
    if key not in _exe_icons:
        url = None
        large = wintypes.HICON()
        if shell32.ExtractIconExW(path, 0, ctypes.byref(large), None, 1) and large.value:
            try:
                url = _render_icon(large.value)
            finally:
                user32.DestroyIcon(large)
        _exe_icons[key] = url
    return _exe_icons[key]


def window_icon(hwnd: int, exe_path: str = "") -> str | None:
    """Icône de la fenêtre (data URL PNG), à défaut celle de son exécutable."""
    try:
        hicon = _window_hicon(hwnd)
        return (_render_icon(hicon) if hicon else None) or (_exe_icon(exe_path) if exe_path else None)
    except Exception:
        return None


def _render_scale(hwnd: int) -> float:
    """Part de la capture réellement dessinée : une application qui ne gère pas
    l'échelle de l'écran est rendue à sa taille logique (Windows l'agrandit
    ensuite à l'affichage), dans le coin d'un bitmap aux dimensions physiques."""
    if not (_window_dpi and _monitor_dpi):
        return 1.0
    dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    window_dpi = _window_dpi(hwnd)
    if (not window_dpi or _monitor_dpi(monitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
            or not dpi_x.value):
        return 1.0
    return min(1.0, window_dpi / dpi_x.value)


def window_thumbnail(hwnd: int) -> str | None:
    """Miniature JPEG (data URL) du contenu de la fenêtre, même cachée par
    d'autres. None si elle est réduite, ne répond plus ou se capture en noir."""
    if not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd) or user32.IsHungAppWindow(hwnd):
        return None
    # Pixels physiques, même sur un écran d'une autre échelle que le principal
    previous = _set_thread_dpi(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) if _set_thread_dpi else None
    try:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0 or width * height > MAX_CAPTURE_PIXELS:
            return None
        with _Canvas(width, height) as canvas:
            if not user32.PrintWindow(hwnd, canvas.dc, PW_RENDERFULLCONTENT):
                return None
            image = canvas.image()
        # Sans les bordures invisibles de redimensionnement (Windows 10+)
        frame = wintypes.RECT()
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                        ctypes.byref(frame), ctypes.sizeof(frame)):
            frame = rect
        scale = _render_scale(hwnd)
        box = (max(0, round((frame.left - rect.left) * scale)), max(0, round((frame.top - rect.top) * scale)),
               min(width, round((frame.right - rect.left) * scale)),
               min(height, round((frame.bottom - rect.top) * scale)))
        if box[2] - box[0] > 8 and box[3] - box[1] > 8:
            image = image.crop(box)
        image.thumbnail(THUMB_SIZE)
        return _data_url(image, "JPEG") if image.getbbox() else None
    except Exception:
        return None
    finally:
        if previous:
            _set_thread_dpi(previous)


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
