"""
wasapi.py – Accès minimal à WASAPI (API audio de Windows) par comtypes :
liste des périphériques, capture « loopback » du son qui sort d'une sortie
audio, capture d'un micro, lecture de silence et lecture d'un son PCM
(lecture à voix haute, speech.py).

Les captures sont demandées au même format (float 32 bits, 48 kHz, stéréo) :
Windows convertit lui-même depuis le format du périphérique
(AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM), on peut donc additionner les
échantillons de la sortie et du micro sans rééchantillonner. La lecture
profite de la même conversion, dans l'autre sens.

Les objets COM ne doivent servir que dans le thread qui les a créés, après
com_thread().
"""

import ctypes
from contextlib import contextmanager
from ctypes import POINTER, Structure, Union, byref, c_float, c_uint32, c_uint64, c_void_p, wstring_at
from ctypes.wintypes import DWORD, LPCWSTR, WORD

import comtypes
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, IUnknown

RATE = 48000
CHANNELS = 2
SAMPLE = ctypes.sizeof(c_float)
FRAME = SAMPLE * CHANNELS

# EDataFlow / ERole / états des périphériques
E_RENDER, E_CAPTURE = 0, 1
E_CONSOLE = 0
DEVICE_STATE_ACTIVE = 1

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDCLNT_E_DEVICE_INVALIDATED = -2004287484     # 0x88890004
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
BUFFER_HNS = 2_000_000                          # 200 ms, en unités de 100 ns
STGM_READ = 0
VT_LPWSTR = 31

_ole32 = ctypes.OleDLL("ole32")
_ole32.CoTaskMemFree.argtypes = [c_void_p]
_ole32.CoTaskMemFree.restype = None


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", WORD), ("nChannels", WORD), ("nSamplesPerSec", DWORD),
                ("nAvgBytesPerSec", DWORD), ("nBlockAlign", WORD), ("wBitsPerSample", WORD),
                ("cbSize", WORD)]


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _PropValue(Union):
    _fields_ = [("pwszVal", c_void_p), ("pad", c_uint64 * 2)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", WORD), ("r1", WORD), ("r2", WORD), ("r3", WORD), ("value", _PropValue)]


PKEY_Device_FriendlyName = PROPERTYKEY(GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14)


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(DWORD), "count")),
        COMMETHOD([], HRESULT, "GetAt", (["in"], DWORD, "index"), (["out"], POINTER(PROPERTYKEY), "key")),
        COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "value")),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "ctx"),
                  (["in"], c_void_p, "params"), (["out"], POINTER(POINTER(IUnknown)), "obj")),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD, "access"),
                  (["out"], POINTER(POINTER(IPropertyStore)), "store")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_void_p), "id")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "state")),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint32), "count")),
        COMMETHOD([], HRESULT, "Item", (["in"], c_uint32, "index"),
                  (["out"], POINTER(POINTER(IMMDevice)), "device")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], DWORD, "flow"), (["in"], DWORD, "mask"),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)), "devices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], DWORD, "flow"), (["in"], DWORD, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "device")),
        COMMETHOD([], HRESULT, "GetDevice", (["in"], LPCWSTR, "id"),
                  (["out"], POINTER(POINTER(IMMDevice)), "device")),
    ]


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize", (["in"], DWORD, "mode"), (["in"], DWORD, "flags"),
                  (["in"], ctypes.c_int64, "duration"), (["in"], ctypes.c_int64, "period"),
                  (["in"], c_void_p, "format"), (["in"], POINTER(GUID), "session")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "frames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_int64), "latency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "frames")),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], DWORD, "mode"), (["in"], c_void_p, "format"),
                  (["out"], POINTER(c_void_p), "closest")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(c_void_p), "format")),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(ctypes.c_int64), "default"),
                  (["out"], POINTER(ctypes.c_int64), "minimum")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], c_void_p, "event")),
        COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "iid"),
                  (["out"], POINTER(POINTER(IUnknown)), "obj")),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(c_void_p), "data"),
                  (["out"], POINTER(c_uint32), "frames"), (["out"], POINTER(DWORD), "flags"),
                  (["out"], POINTER(c_uint64), "position"), (["out"], POINTER(c_uint64), "qpc")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "frames")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "frames")),
    ]


class IAudioRenderClient(IUnknown):
    _iid_ = GUID("{F294ACFC-3146-4483-A7BF-ADDCA7C260E2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer", (["in"], c_uint32, "frames"), (["out"], POINTER(c_void_p), "data")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "frames"), (["in"], DWORD, "flags")),
    ]


@contextmanager
def com_thread():
    """COM initialisé pour le thread courant (déjà fait par un autre module : rien à défaire)."""
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        owned = True
    except OSError:
        owned = False
    try:
        yield
    finally:
        if owned:
            comtypes.CoUninitialize()


def is_invalidated(error: Exception) -> bool:
    """Périphérique débranché, désactivé ou changé de format pendant la capture."""
    return getattr(error, "hresult", None) == AUDCLNT_E_DEVICE_INVALIDATED


def _enumerator():
    return comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)


def _device_id(device) -> str:
    raw = device.GetId()
    try:
        return wstring_at(raw)
    finally:
        _ole32.CoTaskMemFree(raw)


def _friendly_name(device) -> str:
    store = device.OpenPropertyStore(STGM_READ)
    value = PROPVARIANT()
    store.GetValue(byref(PKEY_Device_FriendlyName), byref(value))
    try:
        return wstring_at(value.value.pwszVal) if value.vt == VT_LPWSTR and value.value.pwszVal else ""
    finally:
        _ole32.PropVariantClear(byref(value))


def list_devices(flow: int) -> list[dict]:
    """Périphériques actifs (sorties : E_RENDER, entrées : E_CAPTURE), défaut en tête."""
    enum = _enumerator()
    try:
        default = _device_id(enum.GetDefaultAudioEndpoint(flow, E_CONSOLE))
    except comtypes.COMError:
        default = ""
    devices = []
    collection = enum.EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE)
    for i in range(collection.GetCount()):
        device = collection.Item(i)
        dev_id = _device_id(device)
        devices.append({"id": dev_id, "name": _friendly_name(device) or dev_id, "default": dev_id == default})
    devices.sort(key=lambda d: (not d["default"], d["name"].lower()))
    return devices


def get_device(flow: int, device_id: str = ""):
    """Périphérique choisi, ou celui par défaut (device_id vide ou périphérique disparu).
    Renvoie (device, id, nom)."""
    enum = _enumerator()
    device = None
    if device_id:
        try:
            device = enum.GetDevice(device_id)
            if device.GetState() != DEVICE_STATE_ACTIVE:
                device = None
        except comtypes.COMError:
            device = None
    if device is None:
        device = enum.GetDefaultAudioEndpoint(flow, E_CONSOLE)
    return device, _device_id(device), _friendly_name(device)


def _client(device):
    return device.Activate(byref(IAudioClient._iid_), CLSCTX_ALL, None).QueryInterface(IAudioClient)


class CaptureStream:
    """Capture d'une sortie (loopback=True) ou d'un micro, en float stéréo 48 kHz."""

    def __init__(self, device, loopback: bool):
        fmt = WAVEFORMATEX(WAVE_FORMAT_IEEE_FLOAT, CHANNELS, RATE, RATE * FRAME, FRAME, SAMPLE * 8, 0)
        flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
        if loopback:
            flags |= AUDCLNT_STREAMFLAGS_LOOPBACK
        self._client = _client(device)
        self._client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, BUFFER_HNS, 0, ctypes.addressof(fmt), None)
        self._capture = self._client.GetService(byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
        self._client.Start()

    def read(self) -> bytes:
        """Tout ce qui est disponible (octets float32 entrelacés), b"" si rien."""
        chunks = []
        while self._capture.GetNextPacketSize():
            data, frames, flags, _pos, _qpc = self._capture.GetBuffer()
            if flags & AUDCLNT_BUFFERFLAGS_SILENT or not data:
                chunks.append(bytes(frames * FRAME))
            else:
                chunks.append(ctypes.string_at(data, frames * FRAME))
            self._capture.ReleaseBuffer(frames)
        return b"".join(chunks)

    def close(self):
        try:
            self._client.Stop()
        except comtypes.COMError:
            pass
        self._capture = self._client = None


class SilenceStream:
    """Joue du silence sur une sortie. Sans rien à jouer, Windows ne fournit
    aucune donnée à la capture loopback : le silence la fait tourner en continu
    (l'enregistrement garde la bonne durée, pauses sonores comprises)."""

    def __init__(self, device):
        self._client = _client(device)
        mix = self._client.GetMixFormat()
        try:
            self._client.Initialize(AUDCLNT_SHAREMODE_SHARED, 0, BUFFER_HNS, 0, mix, None)
        finally:
            _ole32.CoTaskMemFree(mix)
        self._size = self._client.GetBufferSize()
        self._render = self._client.GetService(byref(IAudioRenderClient._iid_)).QueryInterface(IAudioRenderClient)
        self.feed()
        self._client.Start()

    def feed(self):
        free = self._size - self._client.GetCurrentPadding()
        if free:
            self._render.GetBuffer(free)
            self._render.ReleaseBuffer(free, AUDCLNT_BUFFERFLAGS_SILENT)

    def close(self):
        try:
            self._client.Stop()
        except comtypes.COMError:
            pass
        self._render = self._client = None


class RenderStream:
    """Joue du PCM 16 bits (mono par défaut, à la fréquence donnée) sur une
    sortie ; Windows le convertit au format du périphérique. Le flux démarre
    au premier write() ; pause() / resume() gardent ce qui est en tampon,
    flush() le jette."""

    def __init__(self, device, rate: int, channels: int = 1):
        self.rate = rate
        self.frame = 2 * channels
        fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, channels, rate, rate * self.frame, self.frame, 16, 0)
        flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
        self._client = _client(device)
        self._client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, BUFFER_HNS, 0, ctypes.addressof(fmt), None)
        self._size = self._client.GetBufferSize()
        self._render = self._client.GetService(byref(IAudioRenderClient._iid_)).QueryInterface(IAudioRenderClient)
        self._running = False

    def write(self, data: bytes) -> int:
        """Copie dans le tampon ce qui y tient ; renvoie le nombre d'octets pris."""
        frames = min(self._size - self._client.GetCurrentPadding(), len(data) // self.frame)
        if frames <= 0:
            return 0
        ctypes.memmove(self._render.GetBuffer(frames), data, frames * self.frame)
        self._render.ReleaseBuffer(frames, 0)
        if not self._running:
            self._client.Start()
            self._running = True
        return frames * self.frame

    def pending(self) -> float:
        """Secondes écrites mais pas encore jouées."""
        return self._client.GetCurrentPadding() / self.rate

    def pause(self):
        if self._running:
            self._client.Stop()
            self._running = False

    def resume(self):
        if not self._running and self._client.GetCurrentPadding():
            self._client.Start()
            self._running = True

    def flush(self):
        self.pause()
        self._client.Reset()

    def close(self):
        try:
            self._client.Stop()
        except comtypes.COMError:
            pass
        self._render = self._client = None
