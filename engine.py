"""
engine.py – Machinerie interne de Vigil
Logger, TaskManager, HotkeyManager, création de l'icône tray.
Ne pas modifier sauf pour étendre le moteur lui-même.
"""

import os
import threading
import time
import datetime

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
#  Icône tray — charge vigil.ico
# ─────────────────────────────────────────────
def create_tray_icon(size: int = 64) -> Image.Image:
    """Charge vigil.ico depuis le dossier du projet."""
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
    """

    def __init__(self):
        self._handlers: list = []
        self._lock = threading.Lock()

    def subscribe(self, handler):
        with self._lock:
            self._handlers.append(handler)

    def log(self, message: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            for h in self._handlers:
                try:
                    h(line)
                except Exception:
                    pass


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

    def remove_all(self):
        keyboard.unhook_all_hotkeys()


# ─────────────────────────────────────────────
#  Singletons partagés entre tous les modules
# ─────────────────────────────────────────────
logger = Logger()
task_manager = TaskManager(logger)
hotkey_manager = HotkeyManager(logger)
