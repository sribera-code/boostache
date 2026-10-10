"""
engine.py – Machinerie interne de Boostache
Logger, TaskManager, HotkeyManager, TTSEngine (speech.py), création de l'icône tray.
Ne pas modifier sauf pour étendre le moteur lui-même.
"""

import os
import re
import threading
import time
import datetime
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
#  Logger thread-safe
# ─────────────────────────────────────────────
class Logger:
    """
    Publie des messages horodatés vers tous les handlers abonnés.
    Thread-safe : peut être appelé depuis n'importe quel thread.

    Usage :
        logger.log("Mon message")
        logger.subscribe(ma_fonction)   # appelée avec (line: str)

    Les dernières lignes sont conservées (recent()) pour qu'une interface
    ouverte après coup puisse afficher ce qui s'est passé au démarrage.
    """

    def __init__(self, history: int = 2000):
        self._handlers: list = []
        self._lock = threading.Lock()
        self._history: deque[tuple[int, str]] = deque(maxlen=history)
        self._seq = 0

    def subscribe(self, handler):
        with self._lock:
            self._handlers.append(handler)

    def log(self, message: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            self._seq += 1
            self._history.append((self._seq, line))
            for h in self._handlers:
                try:
                    h(line)
                except Exception:
                    pass

    def recent(self) -> list[tuple[int, str]]:
        """Dernières lignes loguées, sous forme (numéro, ligne)."""
        with self._lock:
            return list(self._history)

    @property
    def last_seq(self) -> int:
        return self._seq

    def clear_history(self):
        with self._lock:
            self._history.clear()


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
