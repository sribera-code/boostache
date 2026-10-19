"""
diagnostics.py – Journal sur disque et capture des erreurs.

Lancé par pythonw.exe (raccourci, démarrage avec Windows), Boostache n'a pas de
console : sans ce module, les exceptions non rattrapées (threads, callbacks), les
messages du module logging (pywebview, asyncio, bibliothèques) et tout ce qui
s'écrit sur stdout/stderr disparaîtraient. Ils rejoignent ici le journal
(engine.logger), écrit aussi sur disque :

- un fichier par lancement dans DATA_DIR/logs, les SESSIONS_KEPT derniers gardés
  (« Recharger » lance la nouvelle instance avant que l'ancienne ne ferme : chacune
  écrit le sien) ;
- un plantage natif (WebView2, pythonnet, ctypes…) laisse à côté la pile de
  chaque thread (faulthandler) ;
- au démarrage, une session précédente qui ne s'est pas terminée normalement
  (plantage, arrêt forcé) est signalée dans le journal, avec la fin du sien.

install() est appelé au tout début de main.py, avant l'import de pywebview.
"""

import atexit
import ctypes
import datetime
import faulthandler
import importlib.metadata
import io
import logging
import os
import platform
import re
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

from engine import format_entry, logger
from storage import DATA_DIR

APP_DIR = Path(__file__).resolve().parent
LOG_DIR = DATA_DIR / "logs"
SESSIONS_KEPT = 20
MAX_FILE_SIZE = 20 * 1024 * 1024
END_MARK = "— Fin de session —"
PREVIOUS_TAIL = 40           # lignes du journal précédent jointes au signalement d'un arrêt anormal
REPORT_PROBLEMS = 60         # erreurs et avertissements repris dans le rapport
REPORT_RECENT = 150          # dernières entrées reprises dans le rapport
# Bibliothèques bavardes : seuls leurs avertissements et erreurs sont gardés
QUIET_LOGGERS = ("httpx", "httpcore", "urllib3", "comtypes", "PIL", "watchdog", "hpack")
STREAM_IDLE = 0.25           # s sans écriture : les lignes reçues sur stdout/stderr forment une entrée
FAULT_CHECK = 2.0            # s entre deux vérifications du rapport de faulthandler
_PROGRESS = re.compile(r"\d+%\|")              # barre de progression (tqdm)
_API_CALL = re.compile(r'File "[^"]*[\\/]api\.py", line \d+, in (\w+)')   # méthode appelée depuis l'interface
_SESSION_NAME = re.compile(r"session_(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)_(\d+)\.log")
_APP_PREFIX = str(APP_DIR).lower() + os.sep
STARTED = time.time()

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_k32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD))
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.GetTickCount64.restype = ctypes.c_ulonglong
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


_k32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD)

_session: "_SessionFile | None" = None
_fault: "_FaultReport | None" = None
_streams: list = []
_console = None              # vraie console (python.exe), sinon None (pythonw.exe)


# ─────────────────────────────────────────────
#  Journal de la session sur disque
# ─────────────────────────────────────────────
class _SessionFile:
    """Une ligne par entrée (détails en retrait), écrite aussitôt : rien n'est perdu
    en cas de plantage. Les répétitions d'une entrée sont résumées en une ligne
    quand la série s'arrête."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._f = open(path, "a", encoding="utf-8", errors="replace")
        self._size = 0
        self._full = False
        self._repeat: dict | None = None

    def __call__(self, entry: dict):
        with self._lock:
            if entry.get("repeat", 1) > 1:
                self._repeat = entry
                return
            self._flush_repeat()
            self._write(format_entry(entry, date=True))

    def write(self, text: str):
        with self._lock:
            self._write(text)

    def _flush_repeat(self):
        entry, self._repeat = self._repeat, None
        if entry:
            last = datetime.datetime.fromtimestamp(entry["last"]).strftime("%H:%M:%S")
            self._write(f"    … même message ×{entry['repeat']} (dernière fois à {last})")

    def _write(self, text: str, force: bool = False):
        if self._f is None or (self._full and not force):
            return
        if self._size > MAX_FILE_SIZE and not force:
            self._full = True
            text = f"… journal coupé ici : plus de {MAX_FILE_SIZE // 2 ** 20} Mo pour cette session."
        try:
            self._f.write(text + "\n")
            self._f.flush()
            self._size += len(text) + 1
        except (OSError, ValueError):
            pass

    def close(self):
        with self._lock:
            self._flush_repeat()
            self._write(END_MARK, force=True)
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None


class _FaultReport:
    """Pile de chaque thread écrite par faulthandler lors d'une erreur fatale
    (accès mémoire invalide dans une DLL, erreur fatale de Python…).

    Windows signale aussi à faulthandler des exceptions que le code natif rattrape
    lui-même (COM…) : le fichier est relu régulièrement et, si Boostache tourne
    toujours, son contenu passe dans le journal (niveau debug) et il est vidé. Un
    rapport encore présent au démarrage suivant est donc celui d'un vrai plantage."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._f = None
        self._open()

    def _open(self):
        self._f = open(self.path, "w", encoding="utf-8", errors="replace")
        faulthandler.enable(file=self._f, all_threads=True)

    def check(self):
        with self._lock:
            if self._f is None:
                return
            try:
                if self.path.stat().st_size == 0:
                    return
                text = self.path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return
            faulthandler.disable()
            self._f.close()
            self._open()
        if text:
            first = text.splitlines()[0]
            logger.debug(f"Exception Windows rattrapée par le code natif ({first.rstrip(':')}).",
                         details=_head(text, 60), source="diagnostics")

    def close(self):
        with self._lock:
            faulthandler.disable()
            try:
                self._f.close()
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self._f = None


def _head(text: str, lines: int) -> str:
    rows = text.splitlines()
    return "\n".join(rows[:lines] + ([f"… ({len(rows) - lines} lignes de plus)"] if len(rows) > lines else []))


# ─────────────────────────────────────────────
#  Exceptions non rattrapées
# ─────────────────────────────────────────────
def _module_of(filename: str, name: str) -> str:
    return "main" if name == "__main__" else (name or Path(filename).stem).rsplit(".", 1)[-1]


def _origin(tb) -> str | None:
    """Module de Boostache le plus profond dans une trace (là où chercher la cause)."""
    found = None
    for frame, _ in traceback.walk_tb(tb):
        filename = frame.f_code.co_filename
        if filename.lower().startswith(_APP_PREFIX):
            found = _module_of(filename, frame.f_globals.get("__name__", ""))
    return found


def _origin_text(text: str) -> str | None:
    """Même chose pour une trace déjà mise en texte."""
    found = None
    for filename in re.findall(r'File "([^"]+)"', text or ""):
        if filename.lower().startswith(_APP_PREFIX):
            found = Path(filename).stem
    return found


def _exc_line(exc) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _report(message: str, exc_type, exc, tb, source: str = "python"):
    details = "".join(traceback.format_exception(exc_type, exc, tb)).rstrip() if exc_type else None
    logger.error(message, details=details, source=_origin(tb) or source)
    if _console is not None:
        try:
            _console.write(f"{message}\n{details or ''}\n")
        except Exception:
            pass


def _excepthook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    _report(f"Exception non rattrapée : {_exc_line(exc)}", exc_type, exc, tb)


def _thread_excepthook(args):
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    _report(f"Thread « {name} » arrêté par une exception : {_exc_line(args.exc_value)}",
            args.exc_type, args.exc_value, args.exc_traceback, source="threading")


def _unraisable_hook(u):
    """Exception qui ne peut remonter nulle part : callback ctypes (hooks Windows,
    clavier), destructeur, ramasse-miettes…"""
    what = u.err_msg or "Exception ignorée"
    if u.object is not None:
        try:
            what += f" : {repr(u.object)[:200]}"
        except Exception:
            pass
    if u.exc_value is not None:
        what += f" — {_exc_line(u.exc_value)}"
    _report(what, u.exc_type, u.exc_value, u.exc_traceback)


# ─────────────────────────────────────────────
#  Module logging (pywebview, asyncio, bibliothèques)
# ─────────────────────────────────────────────
class _LogHandler(logging.Handler):
    """Messages du module logging → journal. INFO d'une bibliothèque : niveau
    « debug » (détail technique). Une trace passée comme message (pywebview, quand
    un appel depuis l'interface échoue) est résumée par sa dernière ligne."""

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage().strip()
        except Exception:
            msg = str(record.msg)
        level = ("error" if record.levelno >= logging.ERROR else
                 "warning" if record.levelno >= logging.WARNING else "debug")
        source = "warnings" if record.name == "py.warnings" else record.name.split(".")[0]
        details = None
        if record.exc_info and record.exc_info[0] is not None:
            details = "".join(traceback.format_exception(*record.exc_info)).rstrip()
            source = _origin(record.exc_info[2]) or source
        if msg.startswith(_TRACE_START):
            details = msg + (f"\n\n{details}" if details else "")
            source = _origin_text(msg) or source
            call = _API_CALL.search(msg)
            msg = (f"Appel de l'interface api.{call.group(1)} en échec : " if call else "") + msg.splitlines()[-1]
        elif "\n" in msg:
            msg, _, rest = msg.partition("\n")
            details = rest.strip() + (f"\n\n{details}" if details else "")
        logger.log(msg or "(sans message)", level=level, details=details or None, source=source)


def _capture_logging():
    root = logging.getLogger()
    root.addHandler(_LogHandler())
    root.setLevel(logging.INFO)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # Sans gestionnaire, pywebview ajoute le sien, qui écrirait sur stderr (doublon)
    logging.getLogger("pywebview").addHandler(logging.NullHandler())
    logging.captureWarnings(True)


# ─────────────────────────────────────────────
#  stdout / stderr
# ─────────────────────────────────────────────
class _Stream(io.TextIOBase):
    """Remplace sys.stdout / sys.stderr : les lignes écrites rejoignent le journal,
    regroupées en une entrée par rafale (une trace imprimée par une bibliothèque
    reste d'un seul tenant) ; un \\r efface la ligne en cours (barres de
    progression). Avec une console (python.exe), elle reçoit tout aussi."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, source: str, level: str, console=None):
        super().__init__()
        self._source = source
        self._level = level
        self._out = console
        self._lock = threading.Lock()
        self._partial = ""
        self._lines: list[str] = []
        self._last = 0.0

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, s) -> int:
        s = s if isinstance(s, str) else str(s)
        if self._out is not None:
            try:
                self._out.write(s)
            except Exception:
                pass
        with self._lock:
            *done, self._partial = (self._partial + s).split("\n")
            for line in done:
                line = line.rsplit("\r", 1)[-1].rstrip()
                if line:
                    self._lines.append(line)
            self._last = time.monotonic()
        return len(s)

    def flush(self):
        if self._out is not None:
            try:
                self._out.flush()
            except Exception:
                pass

    def drain(self, force: bool = False):
        with self._lock:
            if force and self._partial.strip():
                self._lines.append(self._partial.rsplit("\r", 1)[-1].rstrip())
                self._partial = ""
            if not self._lines or (not force and time.monotonic() - self._last < STREAM_IDLE):
                return
            lines, self._lines = self._lines, []
        for group in _split_traces(lines):
            level, msg, source = self._level, group[0], self._source
            details = "\n".join(group) if len(group) > 1 else None
            if msg.startswith(_TRACE_START):
                level, msg = "error", group[-1]
                source = _origin_text(details) or source
            elif _PROGRESS.search(msg):
                level = "debug"
            logger.log(msg.strip(), level=level, details=details, source=source)


_TRACE_START = "Traceback (most recent call last)"
_TRACE_CHAIN = ("During handling of the above exception", "The above exception was the direct cause")


def _split_traces(lines: list[str]) -> list[list[str]]:
    """Découpe une rafale de lignes : chaque trace (jusqu'à la ligne de son
    exception, causes enchaînées comprises) forme un groupe, le reste aussi."""
    groups: list[list[str]] = []
    mode = None          # "plain", "trace" (appels), "ended" (ligne de l'exception lue), "chained"
    for line in lines:
        if line.startswith(_TRACE_START):
            if mode != "chained":                  # sinon : la cause enchaînée reste dans le groupe
                groups.append([])
            mode = "trace"
        elif mode == "trace":
            if not line.startswith((" ", "\t")):
                mode = "ended"
        elif mode == "ended" and line.startswith(_TRACE_CHAIN):
            mode = "chained"
        elif mode != "chained":
            if mode != "plain":
                groups.append([])
            mode = "plain"
        groups[-1].append(line)
    return groups


def _capture_streams():
    global _console
    _console = sys.stderr       # None avec pythonw.exe
    sys.stdout = _Stream("stdout", "info", sys.stdout)
    sys.stderr = _Stream("stderr", "warning", sys.stderr)
    _streams[:] = [sys.stdout, sys.stderr]
    threading.Thread(target=_watch, daemon=True, name="journal").start()


def _watch():
    next_fault = time.monotonic() + FAULT_CHECK
    while True:
        time.sleep(STREAM_IDLE)
        try:
            for stream in _streams:
                stream.drain()
            if _fault is not None and time.monotonic() >= next_fault:
                next_fault = time.monotonic() + FAULT_CHECK
                _fault.check()
        except Exception:
            pass


# ─────────────────────────────────────────────
#  Installation, fin de session
# ─────────────────────────────────────────────
def install():
    """Journal sur disque, capture des exceptions, de logging et des sorties
    standard, signalement d'un arrêt anormal de la session précédente.
    À appeler une fois, au tout début (avant l'import de pywebview)."""
    global _session, _fault
    if _session is not None or sys.excepthook is _excepthook:
        return
    previous = []
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        previous = _session_files()
        stamp = datetime.datetime.fromtimestamp(STARTED).strftime("%Y-%m-%d_%H-%M-%S")
        path = LOG_DIR / f"session_{stamp}_{os.getpid()}.log"
        _session = _SessionFile(path)
        _session.write(f"# Boostache · session du {datetime.datetime.fromtimestamp(STARTED):%Y-%m-%d %H:%M:%S}"
                       f" · pid {os.getpid()} · {_versions_line()}")
        logger.subscribe(_session, replay=True)
        _fault = _FaultReport(path.with_suffix(".crash.txt"))
        _prune(previous)
    except OSError as e:
        logger.warning(f"Journal sur disque indisponible ({e}).")
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    sys.unraisablehook = _unraisable_hook
    _capture_logging()
    _capture_streams()
    atexit.register(close)
    if previous:
        _check_previous(previous[-1])


def close():
    """Fin normale (atexit, une fois les threads non démons terminés) : la marque de
    fin dit au démarrage suivant que la session s'est bien terminée."""
    global _session, _fault
    for stream in _streams:
        stream.drain(force=True)
    if _fault is not None:
        _fault.close()
        _fault = None
    if _session is not None:
        _session.close()
        _session = None


def session_path() -> Path | None:
    return _session.path if _session else None


def _session_files() -> list[Path]:
    """Journaux des sessions sur disque, du plus ancien au plus récent."""
    try:
        return sorted(p for p in LOG_DIR.glob("session_*.log") if _SESSION_NAME.fullmatch(p.name))
    except OSError:
        return []


def _prune(previous: list[Path]):
    for path in previous[:max(0, len(previous) - (SESSIONS_KEPT - 1))]:
        for p in (path, path.with_suffix(".crash.txt")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass          # session encore ouverte (autre instance)


def _tail(path: Path, size: int = 64 * 1024) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - size))
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _session_state(path: Path) -> str:
    """ok (terminée normalement), running (une autre instance tourne), crash
    (plantage relevé par faulthandler), shutdown (arrêtée avec Windows) ou abnormal."""
    lines = _tail(path, 4096)
    if lines and lines[-1].strip() == END_MARK:
        return "ok"
    m = _SESSION_NAME.fullmatch(path.name)
    if m and int(m.group(2)) != os.getpid() and _is_python(int(m.group(2))):
        return "running"
    crash = path.with_suffix(".crash.txt")
    try:
        if crash.stat().st_size > 0:
            return "crash"
    except OSError:
        pass
    try:
        if path.stat().st_mtime < _boot_time():
            return "shutdown"
    except OSError:
        pass
    return "abnormal"


def _check_previous(path: Path):
    """Signale (dans le journal de cette session) une session précédente qui ne
    s'est pas terminée normalement."""
    state = _session_state(path)
    if state in ("ok", "running"):
        return
    try:
        when = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y à %H:%M:%S")
    except OSError:
        when = "?"
    if state == "shutdown":
        logger.info(f"La session précédente s'est terminée avec l'arrêt de Windows (dernière entrée le {when}).",
                    source="diagnostics")
        return
    details = []
    if state == "crash":
        try:
            fault = path.with_suffix(".crash.txt").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            fault = ""
        details += ["Rapport de faulthandler (pile de chaque thread au moment du plantage) :", fault, ""]
    details += [f"Fin du journal précédent ({path.name}) :", *_tail(path)[-PREVIOUS_TAIL:]]
    if state == "crash":
        logger.error(f"La session précédente a planté le {when} (erreur fatale dans Python ou une DLL).",
                     details="\n".join(details), source="diagnostics")
    else:
        logger.warning(f"La session précédente s'est arrêtée sans passer par « Quitter » (dernière entrée le {when}) :"
                       " processus tué, blocage ou fermeture de la session Windows.",
                       details="\n".join(details), source="diagnostics")


def _is_python(pid: int) -> bool:
    """Le processus tourne toujours et c'est bien Python (et pas un numéro réattribué)."""
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        return bool(_k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))) \
            and "python" in Path(buf.value).name.lower()
    finally:
        _k32.CloseHandle(handle)


def _boot_time() -> float:
    return time.time() - _k32.GetTickCount64() / 1000


# ─────────────────────────────────────────────
#  Pour l'interface : sessions, fichiers, rapport
# ─────────────────────────────────────────────
def sessions() -> list[dict]:
    """Journaux sur disque, du plus récent au plus ancien."""
    out = []
    for path in reversed(_session_files()):
        m = _SESSION_NAME.fullmatch(path.name)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        start = datetime.datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S")
        current = _session is not None and path == _session.path
        out.append({"name": path.name, "start": start.isoformat(), "size": size,
                    "state": "current" if current else _session_state(path)})
    return out


def open_log(name: str | None = None) -> bool:
    """Ouvre le journal d'une session (celle-ci par défaut) dans l'éditeur de Windows."""
    if name:
        path = LOG_DIR / name
        if not _SESSION_NAME.fullmatch(name) or not path.is_file():
            return False
    elif _session:
        path = _session.path
    else:
        return False
    try:
        os.startfile(str(path))
        return True
    except OSError:
        return False


def open_folder() -> bool:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(LOG_DIR))
        return True
    except OSError:
        return False


def _version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except Exception:
        return "?"


def _git_version() -> str:
    """Branche et commit du dépôt d'où tourne Boostache (lus dans .git, sans git)."""
    git = APP_DIR / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        branch = ref.rsplit("/", 1)[-1]
        ref_file = git / ref
        if ref_file.is_file():
            return f"{branch} @ {ref_file.read_text(encoding='utf-8').strip()[:7]}"
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return f"{branch} @ {line[:7]}"
        return branch
    except OSError:
        return "inconnue"


def _versions_line() -> str:
    exe = Path(sys.executable).name
    return (f"Boostache {_git_version()} · Python {platform.python_version()} ({exe}) · "
            f"pywebview {_version('pywebview')} · Windows {platform.version()}")


def _memory_mb() -> int | None:
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if _k32.K32GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return round(counters.WorkingSetSize / 2 ** 20)
    return None


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d}" if hours < 48 else f"{hours // 24} jours"


def report(extra: list[str] | None = None, client: dict | None = None) -> str:
    """Rapport de diagnostic, à coller dans une conversation ou un message :
    versions, état, erreurs et avertissements de la session avec leurs traces,
    dernières entrées. extra : lignes d'état fournies par l'application."""
    client = client or {}
    entries = logger.recent()
    problems = [e for e in entries if e["level"] in ("warning", "error")]
    errors = sum(e["level"] == "error" for e in problems)
    now = datetime.datetime.now()
    memory = _memory_mb()
    threads = sorted(t.name for t in threading.enumerate())
    lines = [
        "Rapport de diagnostic Boostache",
        f"Généré le {now:%Y-%m-%d %H:%M:%S} · session démarrée le "
        f"{datetime.datetime.fromtimestamp(STARTED):%Y-%m-%d %H:%M:%S} (depuis {_duration(time.time() - STARTED)})",
        _versions_line(),
    ]
    if client.get("userAgent"):
        lines.append(f"Interface : {client['userAgent']}"
                     + (f" · fenêtre {client.get('viewport')}" if client.get("viewport") else ""))
    lines += [
        f"Processus {os.getpid()}" + (f" · mémoire {memory} Mo" if memory else "")
        + f" · {len(threads)} threads : {', '.join(threads)}",
        f"Données : {DATA_DIR}",
        f"Journal : {_session.path if _session else 'non écrit sur disque'}",
        *(extra or []),
        "",
        f"── Erreurs et avertissements : {errors} erreur(s), {len(problems) - errors} avertissement(s)"
        + (f", les {REPORT_PROBLEMS} derniers" if len(problems) > REPORT_PROBLEMS else "") + " ──",
        *([format_entry(e) for e in problems[-REPORT_PROBLEMS:]] or ["(aucun)"]),
        "",
        f"── Dernières entrées ({min(len(entries), REPORT_RECENT)} sur {len(entries)}) ──",
        *[format_entry(e, details=False) for e in entries[-REPORT_RECENT:]],
    ]
    return "\n".join(lines)
