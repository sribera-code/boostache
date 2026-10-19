"""
engine.py – Machinerie interne de Boostache
Logger, TaskManager, HotkeyManager, TTSEngine (speech.py), création de l'icône tray.
Ne pas modifier sauf pour étendre le moteur lui-même.
"""

import os
import re
import sys
import threading
import time
import datetime
import traceback
from collections import deque

from speech import TTSEngine

try:
    import schedule
    import keyboard
    import pystray
    from PIL import Image, ImageDraw
except ImportError as e:
    raise ImportError(
        f"Dépendance manquante : {e}\n"
        "Installe via : pip install pystray pillow schedule keyboard"
    ) from e


# ─────────────────────────────────────────────
#  Chemin de l'icône (même dossier que engine.py)
# ─────────────────────────────────────────────
ICON_PATH = os.path.join(os.path.dirname(__file__), "boostache.ico")


# ─────────────────────────────────────────────
#  Icône tray — charge boostache.ico
# ─────────────────────────────────────────────
def create_tray_icon(size: int = 64) -> Image.Image:
    """Charge boostache.ico depuis le dossier du projet."""
    if os.path.exists(ICON_PATH):
        img = Image.open(ICON_PATH).convert("RGBA")
        return img.resize((size, size), Image.LANCZOS)
    # Fallback si le .ico est absent
    img = Image.new("RGBA", (size, size), (15, 15, 15, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill=(40, 40, 40, 255))
    return img


# ─────────────────────────────────────────────
#  Logger thread-safe (journal de l'application)
# ─────────────────────────────────────────────
LEVELS = ("debug", "info", "warning", "error")
LEVEL_NAMES = {"debug": "DEBUG", "info": "INFO", "warning": "AVERT", "error": "ERREUR"}
# Message d'échec écrit hors d'un bloc except : avertissement
_FAILURE = re.compile(r"⚠|erreur|échec|impossible|indisponible|illisible|injoignable|refusée?|perdu|interrompue",
                      re.IGNORECASE)
MAX_MESSAGE = 4000
MAX_DETAILS = 12000          # trace gardée (sa fin : l'exception et les appels les plus profonds)
REPEAT_WINDOW = 300          # même message qu'à l'instant (s) : compté au lieu d'être répété


def format_exception(exc: BaseException) -> str:
    """Trace complète d'une exception (causes enchaînées comprises), coupée si trop longue."""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    return text if len(text) <= MAX_DETAILS else "…\n" + text[-MAX_DETAILS:]


def format_entry(entry: dict, date: bool = False, details: bool = True) -> str:
    """Entrée du journal en texte : « 14:32:05 ERREUR [gmail] message », détails en retrait."""
    t = datetime.datetime.fromtimestamp(entry["t"])
    stamp = t.strftime("%Y-%m-%d %H:%M:%S") if date else t.strftime("%H:%M:%S")
    text = f"{stamp} {LEVEL_NAMES.get(entry['level'], entry['level']):<6} [{entry['src']}] {entry['msg']}"
    if entry.get("repeat", 1) > 1:
        last = datetime.datetime.fromtimestamp(entry["last"]).strftime("%H:%M:%S")
        text += f"  (×{entry['repeat']}, dernière fois à {last})"
    if details and entry.get("details"):
        text += "\n" + "\n".join(("    " + line).rstrip() for line in entry["details"].splitlines())
    return text


def _caller(depth: int) -> str:
    """Module à l'origine d'une entrée (« gmail », « rag »…) ; « custom_tasks »
    pour le code d'une tâche créée dans l'interface (exécuté sans module)."""
    try:
        name = sys._getframe(depth + 1).f_globals.get("__name__", "custom_tasks")
    except ValueError:
        return "?"
    return "main" if name == "__main__" else name.rsplit(".", 1)[-1]


class Logger:
    """
    Journal de l'application : entrées horodatées avec un niveau (debug, info,
    warning, error), leur source (le module appelant) et des détails facultatifs
    (la trace d'une exception). Thread-safe : appelable depuis n'importe quel thread.

    Usage :
        logger.log("Mon message")           # niveau deviné, voir log()
        logger.error("Échec de …")          # aussi debug(), info(), warning()
        logger.subscribe(ma_fonction)       # appelée avec chaque entrée (dict)

    Entrée : {seq, t, level, src, msg, details?, repeat?, last?}. Un message
    identique au précédent (même niveau, même source) n'ajoute pas d'entrée : son
    compteur repeat augmente et l'entrée est republiée (même seq).

    Les dernières entrées sont conservées (recent()) pour qu'une interface
    ouverte après coup puisse afficher ce qui s'est passé au démarrage ; celles
    de niveau debug à part, pour qu'une rafale ne chasse pas les erreurs.
    """

    def __init__(self, history: int = 3000, debug_history: int = 1000):
        self._handlers: list = []
        self._lock = threading.RLock()
        self._history: deque[dict] = deque(maxlen=history)
        self._debug: deque[dict] = deque(maxlen=debug_history)
        self._seq = 0
        self._last: dict | None = None

    def subscribe(self, handler, replay: bool = False):
        """replay : le handler reçoit d'abord les entrées déjà conservées."""
        with self._lock:
            if replay:
                for entry in self.recent():
                    handler(entry)
            self._handlers.append(handler)

    def log(self, message: str, level: str | None = None, details: str | None = None, source: str | None = None):
        """Sans niveau : « error » dans un bloc except (la trace de l'exception en
        cours est jointe), « warning » pour un message d'échec (impossible,
        indisponible…), sinon « info »."""
        exc = sys.exc_info()[1]
        message = str(message)
        if level is None:
            level = "error" if exc is not None else "warning" if _FAILURE.search(message) else "info"
        self._add(level, message, details, source or _caller(1), exc)

    def debug(self, message: str, details: str | None = None, source: str | None = None):
        self._add("debug", str(message), details, source or _caller(1))

    def info(self, message: str, details: str | None = None, source: str | None = None):
        self._add("info", str(message), details, source or _caller(1))

    def warning(self, message: str, details: str | None = None, source: str | None = None):
        self._add("warning", str(message), details, source or _caller(1), sys.exc_info()[1])

    def error(self, message: str, details: str | None = None, source: str | None = None):
        self._add("error", str(message), details, source or _caller(1), sys.exc_info()[1])

    def _add(self, level: str, message: str, details: str | None, source: str, exc: BaseException | None = None):
        if level not in LEVELS:
            level = "info"
        if details is None and exc is not None and level in ("warning", "error"):
            details = format_exception(exc)
        if len(message) > MAX_MESSAGE:
            message = message[:MAX_MESSAGE] + "…"
        if details and len(details) > MAX_DETAILS:
            details = "…\n" + details[-MAX_DETAILS:]
        now = time.time()
        with self._lock:
            last = self._last
            if (last and last["msg"] == message and last["level"] == level and last["src"] == source
                    and now - last["last"] < REPEAT_WINDOW):
                last["repeat"] = last.get("repeat", 1) + 1
                last["last"] = now
                entry = last
            else:
                self._seq += 1
                entry = {"seq": self._seq, "t": now, "last": now, "level": level, "src": source, "msg": message}
                if details:
                    entry["details"] = details
                (self._debug if level == "debug" else self._history).append(entry)
                self._last = entry
            for h in self._handlers:
                try:
                    h(dict(entry))
                except Exception:
                    pass

    def recent(self) -> list[dict]:
        """Dernières entrées, dans l'ordre."""
        with self._lock:
            return sorted((dict(e) for e in (*self._history, *self._debug)), key=lambda e: e["seq"])

    def clear_history(self):
        with self._lock:
            self._history.clear()
            self._debug.clear()
            self._last = None


# ─────────────────────────────────────────────
#  TaskManager
# ─────────────────────────────────────────────
class TaskManager:
    """
    Wraps `schedule`. Tourne dans un thread daemon.

    Usage dans tasks.py :
        task_manager.add(
            schedule.every(5).minutes.do(ma_fonction),
            label="Description affichée dans l'UI"
        )
    """

    def __init__(self, logger: Logger):
        self._logger = logger
        self._running = False
        self._thread = None  # type: ignore
        self.registered: list[dict] = []

    def add(self, job, label: str = ""):
        desc = label or str(job)
        self.registered.append({"label": desc, "job": job})
        self._logger.log(f"Tâche ajoutée : {desc}")
        return job

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._logger.log("Planificateur démarré.")

    def stop(self):
        self._running = False
        self._logger.log("Planificateur arrêté.")

    def _loop(self):
        while self._running:
            schedule.run_pending()
            time.sleep(0.5)


# ─────────────────────────────────────────────
#  HotkeyManager
# ─────────────────────────────────────────────
class HotkeyManager:
    """
    Wraps `keyboard`. Enregistre des hotkeys globaux système.

    Usage dans bindings.py :
        hotkey_manager.add("ctrl+alt+h", ma_fonction, label="Description")

    Prérequis Windows : lancer le script en administrateur.
    """

    def __init__(self, logger: Logger):
        self._logger = logger
        self.registered: list[dict] = []

    def add(self, combo: str, callback, label: str = ""):
        desc = label or combo
        keyboard.add_hotkey(combo, callback)
        self.registered.append({"combo": combo, "label": desc, "callback": callback})
        self._logger.log(f"Hotkey enregistré : {combo} → {desc}")

    def list_external(self, combo: str, callback, label: str = ""):
        """Affiche dans l'onglet Raccourcis un raccourci intercepté autrement
        que par `keyboard` (ex. Impr. écran, via un hook Win32 dédié)."""
        desc = label or combo
        self.unlist(combo)
        self.registered.append({"combo": combo, "label": desc, "callback": callback})
        self._logger.log(f"Hotkey enregistré : {combo} → {desc}")

    def unlist(self, combo: str):
        self.registered[:] = [e for e in self.registered if e["combo"] != combo]

    def remove_all(self):
        keyboard.unhook_all_hotkeys()


# ─────────────────────────────────────────────
#  Lecture à voix haute (le moteur est dans speech.py)
# ─────────────────────────────────────────────
def strip_markdown(text: str) -> str:
    """Retire la syntaxe markdown avant la lecture TTS (les blocs de code
    et la réflexion des modèles ne sont pas lus)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*\*|___)(.+?)\1", r"\2", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("|", " ")
    text = re.sub(r"^\s*([-*_]\s*){3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"─+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─────────────────────────────────────────────
#  Singletons partagés entre tous les modules
# ─────────────────────────────────────────────
logger = Logger()
task_manager = TaskManager(logger)
hotkey_manager = HotkeyManager(logger)
tts = TTSEngine(logger)
