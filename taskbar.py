"""
taskbar.py – Pastille sur le bouton de Boostache dans la barre des tâches
(ITaskbarList3.SetOverlayIcon), la même que sur l'icône du tray.

Les appels COM passent par un thread à part : ils peuvent venir de n'importe
quel thread (enregistreur, lecture à voix haute, interface).
"""

import ctypes
import io
import queue
import threading
from ctypes import wintypes

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

from engine import logger

CLSID_TaskbarList = GUID("{56FDF344-FD6D-11D0-958A-006097C9A090}")
LR_DEFAULTCOLOR = 0
ICON_VERSION = 0x00030000

user32 = ctypes.WinDLL("user32")
user32.CreateIconFromResourceEx.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.CreateIconFromResourceEx.restype = wintypes.HICON
user32.DestroyIcon.argtypes = [wintypes.HICON]


class ITaskbarList(IUnknown):
    _iid_ = GUID("{56FDF342-FD6D-11D0-958A-006097C9A090}")
    _methods_ = [
        COMMETHOD([], HRESULT, "HrInit"),
        COMMETHOD([], HRESULT, "AddTab", (["in"], wintypes.HWND, "hwnd")),
        COMMETHOD([], HRESULT, "DeleteTab", (["in"], wintypes.HWND, "hwnd")),
        COMMETHOD([], HRESULT, "ActivateTab", (["in"], wintypes.HWND, "hwnd")),
        COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], wintypes.HWND, "hwnd")),
    ]


class ITaskbarList2(ITaskbarList):
    _iid_ = GUID("{602D4995-B13A-429B-A66E-1935E44F4317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "MarkFullscreenWindow", (["in"], wintypes.HWND, "hwnd"),
                  (["in"], wintypes.BOOL, "fFullscreen")),
    ]


class ITaskbarList3(ITaskbarList2):
    # Méthodes inutilisées déclarées sans leurs arguments : seul leur rang compte
    _iid_ = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
    _methods_ = [
        *(COMMETHOD([], HRESULT, name) for name in (
            "SetProgressValue", "SetProgressState", "RegisterTab", "UnregisterTab", "SetTabOrder",
            "SetTabActive", "ThumbBarAddButtons", "ThumbBarUpdateButtons", "ThumbBarSetImageList")),
        COMMETHOD([], HRESULT, "SetOverlayIcon", (["in"], wintypes.HWND, "hwnd"),
                  (["in"], wintypes.HICON, "hIcon"), (["in"], wintypes.LPCWSTR, "pszDescription")),
    ]


def _hicon(image) -> int:
    """Image PIL → HICON (PNG lu tel quel par Windows, transparence comprise)."""
    buf = io.BytesIO()
    image.save(buf, "PNG")
    data = buf.getvalue()
    return user32.CreateIconFromResourceEx(data, len(data), True, ICON_VERSION,
                                           image.width, image.height, LR_DEFAULTCOLOR)


class Overlay:
    """Pastille du bouton d'une fenêtre dans la barre des tâches."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def set(self, hwnd: int, image=None, description: str = ""):
        """image : PIL (16 px affichés, plus grande pour les écrans à forte densité) ; None : aucune."""
        if not hwnd:
            return
        self._queue.put((hwnd, image, description))
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="taskbar-overlay")
                self._thread.start()

    def _run(self):
        comtypes.CoInitialize()
        try:
            taskbar = comtypes.client.CreateObject(CLSID_TaskbarList, interface=ITaskbarList3)
            taskbar.HrInit()
        except (comtypes.COMError, OSError) as e:
            logger.log(f"Barre des tâches : pastille indisponible ({e})")
            return
        while True:
            hwnd, image, description = self._queue.get()
            hicon = _hicon(image) if image is not None else None
            try:
                taskbar.SetOverlayIcon(hwnd, hicon, description)
            except comtypes.COMError:
                pass        # bouton pas encore créé, ou fenêtre fermée
            finally:
                if hicon:
                    user32.DestroyIcon(hicon)     # la barre des tâches en garde sa copie
