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

# En premier (avant pywebview) : journal sur disque, exceptions, logging et sorties
# standard capturés dès le démarrage — pythonw.exe n'a pas de console
import diagnostics
diagnostics.install()

import pystray

from engine import create_tray_icon, logger, task_manager, hotkey_manager
from storage import clear_cache
from app import BoostacheApp
import tasks
import bindings

WM_RBUTTONUP = 0x0205
PIN_TITLE_MAX = 40


class TrayIcon(pystray.Icon):
    """Menu reconstruit à chaque clic droit : sous Windows, pystray ne le
    construit qu'une fois, et ses entrées suivent ici l'état du moment (fenêtre
    visée, enregistrement en cours). on_menu() est appelé juste avant."""
    on_menu = None

    def _on_notify(self, wparam, lparam):
        if lparam == WM_RBUTTONUP:
            if self.on_menu:
                self.on_menu()
            self._update_menu()
        super()._on_notify(wparam, lparam)


class TrayApp:
    def __init__(self, debug: bool = False, show: bool = False):
        self.app = BoostacheApp(debug=debug, show_on_start=show)
        self.app.notifier = self._notify
        self._tray = None  # type: ignore
        self._pin_target = None   # fenêtre visée par « Garder au premier plan », fixée à l'ouverture du menu

    def _build_tray(self):
        recorder = self.app.recorder
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir", self._on_open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._pin_label, self._on_toggle_topmost,
                             checked=lambda item: bool(self._pin_target and self._pin_target["topmost"]),
                             enabled=lambda item: self._pin_target is not None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Capture d'écran", self._on_capture),
            pystray.MenuItem(lambda item: "Arrêter l'enregistrement" if recorder.recording
                             else "Démarrer l'enregistrement", self._on_toggle_recording,
                             enabled=lambda item: recorder.available),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Recharger", self._on_reload),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", self._on_quit),
        )
        self._tray = TrayIcon("Boostache", create_tray_icon(), "Boostache", menu)
        self._tray.on_menu = self._before_menu

    def _before_menu(self):
        # Le clic a donné le focus à la barre des tâches : on vise la fenêtre d'avant
        self._pin_target = self.app.pin_target()

    def _pin_label(self, item) -> str:
        target = self._pin_target
        if not target:
            return "Garder au premier plan"
        title = target["title"]
        if len(title) > PIN_TITLE_MAX:
            title = title[:PIN_TITLE_MAX - 1].rstrip() + "…"
        return f"Garder au premier plan : {title.replace('&', '&&')}"   # & : lettre soulignée des menus

    def _run_tray(self):
        self._build_tray()
        self._tray.run()

    def _notify(self, title: str, text: str):
        """Notification Windows, émise par l'icône du tray."""
        if self._tray:
            self._tray.notify(text, title)

    def _on_open(self, icon=None, item=None):
        threading.Thread(target=self.app.show, daemon=True).start()

    # Hors du thread du tray : réglage de Boostache appliqué par sa fenêtre, démarrage
    # et arrêt de l'enregistrement qui attendent le périphérique audio
    def _on_toggle_topmost(self, icon=None, item=None):
        if self._pin_target:
            threading.Thread(target=self.app.toggle_topmost, args=(self._pin_target,), daemon=True).start()

    def _on_capture(self, icon=None, item=None):
        self.app.captures.snip()

    def _on_toggle_recording(self, icon=None, item=None):
        threading.Thread(target=self.app.toggle_recording, daemon=True).start()

    def _on_reload(self, icon=None, item=None):
        logger.log("Redémarrage de Boostache…")
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            # Fenêtre affichée une fois rechargé (--show)
            args = [*sys.argv, *(["--show"] if "--show" not in sys.argv else [])]
            subprocess.Popen([sys.executable, *args],
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
