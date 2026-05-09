"""
main.py – Point d'entrée de Boostache
Gère le system tray et orchestre le démarrage de l'application.
"""

import importlib
import threading

import pystray

from engine import create_tray_icon, logger, task_manager, hotkey_manager
from storage import clear_cache
from dashboard import DashboardWindow
import tasks
import bindings


class TrayApp:
    def __init__(self):
        self.dashboard = DashboardWindow()
        self._tray = None  # type: ignore

    def _build_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir", self._on_open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Recharger", self._on_reload),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", self._on_quit),
        )
        self._tray = pystray.Icon("Boostache", create_tray_icon(), "Boostache", menu)

    def _run_tray(self):
        self._build_tray()
        self._tray.run()

    def _on_open(self, icon=None, item=None):
        if self.dashboard.root:
            self.dashboard.root.after(0, self.dashboard.show)

    def _on_reload(self, icon=None, item=None):
        logger.log("Rechargement en cours…")
        hotkey_manager.remove_all()
        import schedule
        schedule.clear()
        task_manager.registered.clear()
        hotkey_manager.registered.clear()
        try:
            importlib.reload(tasks)
            importlib.reload(bindings)
            tasks.register()
            bindings.register(open_dashboard_fn=self._on_open)
            # Recharger les tâches custom persistées
            self.dashboard._task_load_persisted()
            logger.log("Rechargement terminé ✓")
        except Exception as e:
            logger.log(f"Erreur lors du rechargement : {e}")
        if self.dashboard.root:
            self.dashboard.root.after(0, self.dashboard._refresh_hotkeys)

    def _on_quit(self, icon=None, item=None):
        logger.log("Arrêt de Boostache…")
        task_manager.stop()
        hotkey_manager.remove_all()
        if self._tray:
            self._tray.stop()
        if self.dashboard.root:
            self.dashboard.root.after(0, self.dashboard.destroy)

    def run(self):
        tasks.register()
        bindings.register(open_dashboard_fn=self._on_open)
        threading.Thread(target=self._run_tray, daemon=True).start()
        task_manager.start()
        self.dashboard.run()


if __name__ == "__main__":
    clear_cache()
    TrayApp().run()
