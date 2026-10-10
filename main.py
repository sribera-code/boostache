"""
main.py – Point d'entrée de Boostache
Gère le system tray et orchestre le démarrage de l'application.

Options :
    --show    affiche la fenêtre dès le démarrage
    --debug   active les outils de développement (clic droit → Inspecter)
"""

import os
import subprocess
import sys
import threading

import pystray

from engine import create_tray_icon, logger, task_manager, hotkey_manager
from storage import clear_cache
from app import BoostacheApp
import tasks
import bindings


class TrayApp:
    def __init__(self, debug: bool = False, show: bool = False):
        self.app = BoostacheApp(debug=debug, show_on_start=show)
        self.app.notifier = self._notify
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

    def _notify(self, title: str, text: str):
        """Notification Windows, émise par l'icône du tray."""
        if self._tray:
            self._tray.notify(text, title)

    def _on_open(self, icon=None, item=None):
        threading.Thread(target=self.app.show, daemon=True).start()

    def _on_reload(self, icon=None, item=None):
        logger.log("Redémarrage de Boostache…")
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([sys.executable, *sys.argv],
                             close_fds=True, **kwargs)
        except Exception as e:
            logger.log(f"Échec du lancement de la nouvelle instance : {e}")
            return
        self._on_quit()

    def _on_quit(self, icon=None, item=None):
        logger.log("Arrêt de Boostache…")
        threading.Thread(target=self.app.quit, daemon=True).start()

    def run(self):
        tasks.register()
        bindings.register(open_dashboard_fn=self._on_open)
        threading.Thread(target=self._run_tray, daemon=True).start()
        task_manager.start()
        try:
            self.app.run()          # boucle d'interface (thread principal)
        finally:
            task_manager.stop()
            hotkey_manager.remove_all()
            if self._tray:
                self._tray.stop()


if __name__ == "__main__":
    clear_cache()
    TrayApp(debug="--debug" in sys.argv, show="--show" in sys.argv).run()
