"""
locks.py – Section Fichiers bloqués : les applications qui empêchent de
supprimer un fichier ou un dossier, et de quoi les fermer ou les terminer.

Comme Process Explorer ou File Locksmith : la table des handles du système
(NtQuerySystemInformation) donne les fichiers et dossiers ouverts de chaque
processus, chaque handle est dupliqué pour lire son chemin ; s'y ajoutent les
programmes lancés depuis l'élément et les DLL chargées. Dans un dossier, tout
ce qui est dessous compte (un sous-dossier courant d'une console, un fichier
ouvert dans un éditeur…).

Sans droits d'administrateur, les processus élevés et les services ne peuvent
pas être examinés : ce module se relance alors en administrateur (fenêtre
UAC, `--scan` / `--kill`) et rend son résultat dans un fichier JSON.
Bibliothèque standard seulement (pas d'import de l'application) pour ce
lancement à part.
"""

import ctypes
import json
import msvcrt
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
PROCESS_TERMINATE = 0x0001
PROCESS_VM_READ = 0x0010
PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
DUPLICATE_SAME_ACCESS = 0x2
FILE_TYPE_DISK = 1
FILE_READ_ATTRIBUTES = 0x80
FILE_SHARE_ALL = 0x7
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE = wintypes.HANDLE(-1).value
TH32CS_SNAPPROCESS = 0x2
LIST_MODULES_ALL = 0x3
WM_CLOSE = 0x0010
GW_OWNER = 4
ERROR_ACCESS_DENIED = 5
ERROR_CANCELLED = 1223           # fenêtre UAC refusée
SEE_MASK_NOCLOSEPROCESS = 0x40
SEE_MASK_NOASYNC = 0x100
SW_HIDE = 0
TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x20, 0x8
SE_PRIVILEGE_ENABLED = 0x2

STUCK_AFTER = 0.3        # lecture d'un handle sans réponse (dossier surveillé, tube en attente…) : abandonnée (s)
HELPER_TIMEOUT = 120     # analyse ou fin de processus en administrateur (s)
KILL_WAIT_MS = 3000      # attente de la fin d'un processus terminé (ms)
MAX_USES = 50            # éléments utilisés montrés par processus
MAX_WINDOWS = 4          # titres de fenêtres montrés par processus
ELEVATE_VERB = "runas"

# Processus dont la fin ferait planter ou redémarrer Windows : jamais terminés
CRITICAL = {"system", "registry", "secure system", "memory compression", "smss.exe", "csrss.exe",
            "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe",
            "dwm.exe", "fontdrvhost.exe", "msmpeng.exe"}
WEBVIEW = "msedgewebview2.exe"       # navigateur intégré de Boostache (le tuer casse l'interface)
SHELL = "explorer.exe"


class HANDLE_ENTRY(ctypes.Structure):
    _fields_ = [("Object", ctypes.c_void_p), ("UniqueProcessId", ctypes.c_size_t),
                ("HandleValue", ctypes.c_size_t), ("GrantedAccess", wintypes.ULONG),
                ("CreatorBackTraceIndex", wintypes.USHORT), ("ObjectTypeIndex", wintypes.USHORT),
                ("HandleAttributes", wintypes.ULONG), ("Reserved", wintypes.ULONG)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", ctypes.c_int64), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


ntdll.NtQuerySystemInformation.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                                           ctypes.POINTER(wintypes.ULONG)]
ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                     ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
kernel32.GetFileType.argtypes = [wintypes.HANDLE]
kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_uint64)] * 4
kernel32.K32EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
kernel32.K32GetMappedFileNameW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
kernel32.K32GetMappedFileNameW.restype = wintypes.DWORD
kernel32.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
kernel32.QueryDosDeviceW.restype = wintypes.DWORD
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int64)]
advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
                                           wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.IsUserAnAdmin.restype = wintypes.BOOL

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_file_type: int | None = None      # index du type d'objet « File » (fixe jusqu'au redémarrage de Windows)
_stuck: set[tuple[int, int, int]] = set()    # handles illisibles (pid, valeur, objet), voir _handle_paths


def is_elevated() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


# ─────────────────────────────────────────────
#  Chemins
# ─────────────────────────────────────────────
def _clean(path: str) -> str:
    r"""\\?\C:\x → C:\x, \\?\UNC\srv\x → \\srv\x."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\" + path[7:]
    return path[4:] if path.startswith("\\\\?\\") else path


def _final_path(handle) -> str:
    """Chemin complet d'un handle de fichier ou de dossier ("" s'il n'en a pas)."""
    buf = ctypes.create_unicode_buffer(512)
    n = kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
    if n > len(buf):
        buf = ctypes.create_unicode_buffer(n + 1)
        n = kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
    return _clean(buf.value) if 0 < n < len(buf) else ""


def resolve(path: str) -> str:
    """Chemin réel (lecteurs substitués, jonctions, noms courts résolus), comme
    ceux des handles des autres processus ; à défaut, chemin absolu."""
    handle = kernel32.CreateFileW(path, FILE_READ_ATTRIBUTES, FILE_SHARE_ALL, None, OPEN_EXISTING,
                                  FILE_FLAG_BACKUP_SEMANTICS, None)
    if handle in (None, INVALID_HANDLE):
        return os.path.abspath(path)
    try:
        return _final_path(handle) or os.path.abspath(path)
    finally:
        kernel32.CloseHandle(handle)


def _key(path: str) -> str:
    return os.path.normcase(_clean(path)).rstrip("\\")


def _under(path_key: str, targets: list[str]) -> str | None:
    """Cible contenant ce chemin (clés normalisées), ou None."""
    for t in targets:
        if path_key == t or path_key.startswith(t + "\\"):
            return t
    return None


# ─────────────────────────────────────────────
#  Processus
# ─────────────────────────────────────────────
def _processes() -> dict[int, tuple[int, str]]:
    """pid → (pid du parent, nom de l'exécutable)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, INVALID_HANDLE):
        return {}
    found = {}
    try:
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            found[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return found


def _image_path(handle) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(len(buf))
    return buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""


def _start_time(handle) -> int:
    times = [ctypes.c_uint64() for _ in range(4)]
    return times[0].value if kernel32.GetProcessTimes(handle, *map(ctypes.byref, times)) else 0


def _dos_devices() -> list[tuple[str, str]]:
    r"""(\Device\HarddiskVolume3\, C:\) pour chaque lecteur, et les partages réseau."""
    found = [("\\device\\mup\\", "\\\\")]
    buf = ctypes.create_unicode_buffer(1024)
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if kernel32.QueryDosDeviceW(f"{letter}:", buf, len(buf)):
            found.append((buf.value.lower() + "\\", f"{letter}:\\"))
    return found


def _modules(handle, devices: list[tuple[str, str]]) -> list[str]:
    """DLL chargées par le processus (il faut PROCESS_QUERY_INFORMATION | PROCESS_VM_READ).
    Nom du fichier projeté en mémoire plutôt que GetModuleFileNameEx, qui relit
    toute la liste des modules à chaque appel (15 fois plus lent)."""
    needed = wintypes.DWORD()
    count = 512
    while True:
        mods = (ctypes.c_void_p * count)()
        if not kernel32.K32EnumProcessModulesEx(handle, mods, ctypes.sizeof(mods), ctypes.byref(needed),
                                                LIST_MODULES_ALL):
            return []
        n = needed.value // ctypes.sizeof(ctypes.c_void_p)
        if n <= count:
            break
        count = n + 64
    buf = ctypes.create_unicode_buffer(1024)
    paths = []
    for mod in mods[:n]:
        if kernel32.K32GetMappedFileNameW(handle, mod, buf, len(buf)):
            name = buf.value
            low = name.lower()
            for device, dos in devices:
                if low.startswith(device):
                    paths.append(dos + name[len(device):])
                    break
    return paths


def _descendants(procs: dict[int, tuple[int, str]], root: int) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in procs.items():
        if pid != ppid:
            children.setdefault(ppid, []).append(pid)
    found, stack = set(), [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def windows_by_pid() -> dict[int, list[tuple[int, str]]]:
    """pid → fenêtres d'application visibles [(hwnd, titre)]."""
    found: dict[int, list[tuple[int, str]]] = {}

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.setdefault(pid.value, []).append((int(hwnd), title.value))
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


# ─────────────────────────────────────────────
#  Handles de fichiers ouverts
# ─────────────────────────────────────────────
def _handle_table():
    """Tous les handles du système : (tampon, tableau d'entrées)."""
    size = 1 << 22
    while True:
        buf = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG()
        status = ntdll.NtQuerySystemInformation(SYSTEM_EXTENDED_HANDLE_INFORMATION, buf, size,
                                                ctypes.byref(needed))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, needed.value + (1 << 20))
            continue
        if status:
            raise OSError(f"NtQuerySystemInformation : 0x{status:08X}")
        count = ctypes.c_size_t.from_buffer(buf).value
        # En-tête : NumberOfHandles et Reserved (ULONG_PTR chacun)
        return buf, (HANDLE_ENTRY * count).from_buffer(buf, 2 * ctypes.sizeof(ctypes.c_size_t))


def _file_handles() -> dict[int, list[tuple[int, int]]]:
    """pid → [(valeur, adresse de l'objet)] des handles d'objets « File » :
    fichiers et dossiers, mais aussi tubes, consoles, périphériques (triés
    ensuite par GetFileType)."""
    global _file_type
    me = os.getpid()
    with open(sys.executable, "rb") as probe:      # un handle de fichier connu : donne l'index du type
        own = msvcrt.get_osfhandle(probe.fileno())
        _buf, entries = _handle_table()
        if _file_type is None:
            _file_type = next((e.ObjectTypeIndex for e in entries
                               if e.UniqueProcessId == me and e.HandleValue == own), None)
    if _file_type is None:
        raise OSError("type des handles de fichiers introuvable")
    by_pid: dict[int, list[tuple[int, int]]] = {}
    for e in entries:
        # (sans le handle de sondage, fermé depuis : sa valeur peut désigner autre chose)
        if e.ObjectTypeIndex == _file_type and not (e.UniqueProcessId == me and e.HandleValue == own):
            by_pid.setdefault(e.UniqueProcessId, []).append((e.HandleValue, e.Object or 0))
    return by_pid


def _handle_paths(jobs: list[tuple[int, int, int, int]]) -> tuple[dict[int, list[str]], int]:
    """jobs : (pid, handle du processus source, valeur du handle, adresse de
    l'objet). Retourne pid → chemins des fichiers et dossiers ouverts, et le
    nombre de handles illisibles.

    Le nom d'un handle synchrone occupé (dossier surveillé par
    ReadDirectoryChangesW, tube en attente de lecture…) ne se lit qu'à la fin
    de l'opération en cours, parfois jamais : la lecture se fait dans un fil
    surveillé, remplacé (et le handle sauté) s'il ne progresse plus. Le fil
    bloqué se termine quand l'opération finit ; d'ici là, le handle est sauté
    d'emblée aux analyses suivantes (_stuck)."""
    paths: dict[int, list[str]] = {}
    current = kernel32.GetCurrentProcess()
    state = {"gen": 0, "i": 0, "t": time.monotonic()}
    stuck = 0

    def work(gen: int, start: int, done: threading.Event):
        dup = wintypes.HANDLE()
        for i in range(start, len(jobs)):
            if state["gen"] != gen:
                return
            state["i"], state["t"] = i, time.monotonic()
            pid, source, value, obj = jobs[i]
            if (pid, value, obj) in _stuck:
                continue
            if not kernel32.DuplicateHandle(source, value, current, ctypes.byref(dup), 0, False,
                                            DUPLICATE_SAME_ACCESS):
                continue
            try:
                if kernel32.GetFileType(dup) == FILE_TYPE_DISK:
                    path = _final_path(dup)
                    if state["gen"] != gen:          # débloqué après coup : le fil s'arrête
                        _stuck.discard((pid, value, obj))
                        return
                    if path:
                        paths.setdefault(pid, []).append(path)
            finally:
                kernel32.CloseHandle(dup)
        done.set()

    stuck = sum(1 for pid, _s, value, obj in jobs if (pid, value, obj) in _stuck)
    start = 0
    while start < len(jobs):
        done = threading.Event()
        threading.Thread(target=work, args=(state["gen"], start, done), daemon=True,
                         name="locks-handles").start()
        while not done.wait(0.05):
            if time.monotonic() - state["t"] > STUCK_AFTER:
                break
        if done.is_set():
            break
        pid, _source, value, obj = jobs[state["i"]]
        _stuck.add((pid, value, obj))
        state["gen"] += 1
        stuck += 1
        start = state["i"] + 1
    return paths, stuck


def _enable_debug_privilege():
    """En administrateur : ouvrir aussi les services et processus système."""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                     ctypes.byref(token)):
        return
    try:
        luid = ctypes.c_int64()
        if advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES * 1)(LUID_AND_ATTRIBUTES(luid.value, SE_PRIVILEGE_ENABLED)))
            advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
    finally:
        kernel32.CloseHandle(token)


# ─────────────────────────────────────────────
#  Analyse
# ─────────────────────────────────────────────
def scan_local(paths: list[str], owner: int | None = None) -> dict:
    """Processus qui utilisent les éléments `paths` (fichiers ou dossiers, et
    tout ce qu'ils contiennent). owner : pid de Boostache (lui et ses
    processus enfants sont signalés)."""
    started = time.monotonic()
    owner = owner or os.getpid()
    if is_elevated():
        _enable_debug_privilege()

    targets, keys = [], []
    for p in paths:
        real = resolve(p)
        targets.append({"path": p, "real": real, "dir": os.path.isdir(p), "exists": os.path.exists(p)})
        keys.append(_key(real))
    keys = [k for k in keys if k]
    roots = {k for k in keys if os.path.splitdrive(k)[1] in ("", "\\")}
    if roots:
        raise ValueError("Un lecteur entier ne peut pas être analysé : déposez un dossier ou un fichier.")

    procs = _processes()
    by_pid = _file_handles()
    found: dict[int, dict[str, set]] = {}       # pid → type d'utilisation → chemins

    def use(pid: int, kind: str, path: str):
        found.setdefault(pid, {}).setdefault(kind, set()).add(path)

    # Handles ouverts
    sources, denied, jobs = {}, set(), []
    for pid, values in by_pid.items():
        source = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
        if not source:
            denied.add(pid)
            continue
        sources[pid] = source
        jobs.extend((pid, source, value, obj) for value, obj in values)
    try:
        opened, stuck = _handle_paths(jobs)
    finally:
        for source in sources.values():
            kernel32.CloseHandle(source)
    for pid, names in opened.items():
        for name in names:
            if _under(_key(name), keys):
                use(pid, "dir" if os.path.isdir(name) else "file", name)

    # Programmes lancés depuis l'élément, DLL chargées
    times, images = {}, {}
    devices = _dos_devices()
    for pid in procs:
        if pid in (0, 4):
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        full = bool(handle)
        if not handle:
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            denied.add(pid)
            continue
        try:
            times[pid] = _start_time(handle)
            image = images[pid] = _image_path(handle)
            if image and _under(_key(image), keys):
                use(pid, "exe", image)
            if full:
                for mod in _modules(handle, devices):
                    if _under(_key(mod), keys) and _key(mod) != _key(image):
                        use(pid, "dll", mod)
            else:
                denied.add(pid)       # DLL non vérifiées
        finally:
            kernel32.CloseHandle(handle)

    family = _descendants(procs, owner)
    processes = []
    for pid, uses in found.items():
        name = procs.get(pid, (0, ""))[1] or f"PID {pid}"
        items = [{"kind": kind, "path": path} for kind in ("exe", "dll", "dir", "file")
                 for path in sorted(uses.get(kind, ()), key=str.lower)]
        own = ("self" if pid == owner else "" if pid not in family
               else "webview" if name.lower() == WEBVIEW else "child")
        processes.append({
            # start : heure de création (FILETIME), en texte (trop grand pour un nombre JavaScript)
            "pid": pid, "name": name, "exe": images.get(pid, ""), "start": str(times.get(pid, 0)),
            "uses": items[:MAX_USES], "count": len(items), "own": own,
            "critical": pid in (0, 4) or name.lower() in CRITICAL,
        })
        denied.discard(pid)
    processes.sort(key=lambda p: (p["own"] == "self", p["name"].lower(), p["pid"]))
    unseen = sorted({procs[pid][1] for pid in denied if pid in procs and pid not in (0, 4)}, key=str.lower)
    return {"targets": targets, "processes": processes, "denied": unseen, "stuck": stuck,
            "elevated": is_elevated(), "seconds": round(time.monotonic() - started, 2)}


# ─────────────────────────────────────────────
#  Fin des processus
# ─────────────────────────────────────────────
def terminate_local(targets: list[dict]) -> dict:
    """targets : [{pid, start}] ; start (heure de création) évite de tuer un
    autre processus qui aurait repris le même pid entre-temps."""
    if is_elevated():
        _enable_debug_privilege()
    result = {"killed": [], "gone": [], "denied": [], "failed": []}
    for t in targets:
        pid, start = int(t["pid"]), int(t.get("start") or 0)
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                                      False, pid)
        if not handle:
            err = ctypes.get_last_error()
            result["denied" if err == ERROR_ACCESS_DENIED else "gone"].append(pid)
            continue
        try:
            if start and _start_time(handle) != start:
                result["gone"].append(pid)
            elif kernel32.TerminateProcess(handle, 1):
                kernel32.WaitForSingleObject(handle, KILL_WAIT_MS)
                result["killed"].append(pid)
            elif ctypes.get_last_error() == ERROR_ACCESS_DENIED:
                result["denied"].append(pid)
            else:
                result["failed"].append(pid)
        finally:
            kernel32.CloseHandle(handle)
    return result


def close_windows(pid: int) -> int:
    """Demande aux fenêtres du processus de se fermer (comme la croix : l'application
    peut proposer d'enregistrer). Retourne le nombre de fenêtres prévenues."""
    return sum(1 for hwnd, _ in windows_by_pid().get(int(pid), []) if user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def restart_shell():
    """Explorateur terminé : la barre des tâches revient s'il n'a pas redémarré seul."""
    for _ in range(30):
        if user32.FindWindowW("Shell_TrayWnd", None):
            return
        time.sleep(0.1)
    windir = os.environ.get("WINDIR", r"C:\Windows")
    subprocess.Popen([os.path.join(windir, SHELL)], close_fds=True)


# ─────────────────────────────────────────────
#  Pour l'interface
# ─────────────────────────────────────────────
def scan(paths: list[str], elevated: bool = False) -> dict:
    """Analyse ; elevated : en administrateur (fenêtre UAC) si Boostache ne
    l'est pas déjà. Ajoute les fenêtres de chaque processus."""
    app_elevated = is_elevated()
    if elevated and not app_elevated:
        result = run_elevated("scan", {"paths": paths, "owner": os.getpid()})
    else:
        result = scan_local(paths)
    result["app_elevated"] = app_elevated       # (elevated : celui qui a fait l'analyse)
    windows = windows_by_pid()
    for p in result["processes"]:
        p["windows"] = [title for _, title in windows.get(p["pid"], [])][:MAX_WINDOWS]
        p["shell"] = p["name"].lower() == SHELL
    return result


def terminate(targets: list[dict], elevated: bool = False) -> dict:
    """targets : [{pid, start, name}]. L'Explorateur terminé est relancé."""
    result = (run_elevated("kill", {"targets": targets}) if elevated and not is_elevated()
              else terminate_local(targets))
    killed = set(result["killed"])
    if any(int(t["pid"]) in killed and str(t.get("name", "")).lower() == SHELL for t in targets):
        threading.Thread(target=restart_shell, daemon=True, name="locks-shell").start()
    return result


# ─────────────────────────────────────────────
#  En administrateur (fenêtre UAC)
# ─────────────────────────────────────────────
def run_elevated(command: str, payload: dict) -> dict:
    """Relance ce module en administrateur : `command` (scan, kill) sur
    `payload`, résultat lu dans un fichier JSON. PermissionError si l'utilisateur
    refuse la fenêtre UAC."""
    fd, out = tempfile.mkstemp(prefix="boostache-locks-", suffix=".json")
    os.close(fd)
    src = out[:-5] + "-in.json"
    try:
        with open(src, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        exe = sys.executable
        if exe.lower().endswith("python.exe") and os.path.isfile(exe[:-10] + "pythonw.exe"):
            exe = exe[:-10] + "pythonw.exe"         # pas de console
        info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW),
                                 fMask=SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC, lpVerb=ELEVATE_VERB, lpFile=exe,
                                 lpParameters=subprocess.list2cmdline([os.path.abspath(__file__), command, src, out]),
                                 nShow=SW_HIDE)
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            err = ctypes.get_last_error()
            if err == ERROR_CANCELLED:
                raise PermissionError("Droits d'administrateur refusés.")
            raise OSError(f"Lancement en administrateur impossible (erreur {err}).")
        if not info.hProcess:
            raise OSError("Lancement en administrateur impossible.")
        try:
            if kernel32.WaitForSingleObject(info.hProcess, HELPER_TIMEOUT * 1000) != 0:
                raise TimeoutError("L'analyse en administrateur ne répond pas.")
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        finally:
            kernel32.CloseHandle(info.hProcess)
        with open(out, encoding="utf-8") as f:
            text = f.read()
        if not text:
            raise OSError(f"L'analyse en administrateur a échoué (code {code.value}).")
        result = json.loads(text)
        if "error" in result:
            raise OSError(result["error"])
        return result
    finally:
        for p in (src, out):
            try:
                os.remove(p)
            except OSError:
                pass


def _helper(command: str, src: str, out: str):
    try:
        with open(src, encoding="utf-8") as f:
            payload = json.load(f)
        if command == "scan":
            result = scan_local(payload["paths"], payload.get("owner"))
        elif command == "kill":
            result = terminate_local(payload["targets"])
        else:
            result = {"error": f"commande inconnue : {command}"}
    except Exception as e:
        result = {"error": str(e)}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    if len(sys.argv) == 4:
        _helper(*sys.argv[1:4])
    else:
        print(json.dumps(scan_local(sys.argv[1:]), indent=2, ensure_ascii=False))
