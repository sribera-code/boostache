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

# Avant toute fenêtre : icône de Boostache dans la barre des tâches, pas celle de Python
from winutil import set_app_id
set_app_id("Boostache")

import pystray

from assistant import ANNOUNCE
from engine import badge_image, badge_tray_icon, create_tray_icon, logger, task_manager, hotkey_manager, tts
from storage import clear_cache
from app import BoostacheApp
from winutil import dark_menus
import tasks
import bindings

WM_RBUTTONUP = 0x0205
PIN_TITLE_MAX = 40
BADGE_TEXT = {"rec": "enregistrement en cours", "paused": "enregistrement en pause",
              "speak": "lecture d'une annonce"}


def _menu_text(text: str, limit: int) -> str:
    """Texte variable d'une entrée du menu : raccourci, & doublé (sinon lettre soulignée)."""
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text.replace("&", "&&")


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
        self._mail_counts = {}    # e-mails non lus / du jour dans Gmail, relevés à l'ouverture du menu
        self._recording = None    # "rec" | "paused" | None
        self._announcing = False  # annonce d'un nouveau message lue à voix haute
        self._badge = None        # pastille affichée (BADGE_TEXT)
        self._badge_lock = threading.Lock()

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
            pystray.MenuItem(self._recording_label, self._on_toggle_recording,
                             enabled=lambda item: recorder.available),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: self._digest_label("unread"), lambda: self._on_digest("unread"),
                             enabled=lambda item: self._digest_enabled("unread")),
            pystray.MenuItem(lambda item: self._digest_label("today"), lambda: self._on_digest("today"),
                             enabled=lambda item: self._digest_enabled("today")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Recharger", self._on_reload),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", self._on_quit),
        )
        base = create_tray_icon()
        self._icons = {None: base, **{kind: badge_tray_icon(base, kind) for kind in BADGE_TEXT}}
        self._badges = {kind: badge_image(kind) for kind in BADGE_TEXT}
        self._tray = TrayIcon("Boostache", base, "Boostache", menu)
        self._tray.on_menu = self._before_menu
        recorder.on_state = self._on_recording_state
        tts.subscribe(self._on_tts)

    # Pastille d'activité, sur l'icône du tray et le bouton de la barre des tâches :
    # l'enregistrement passe avant la lecture d'une annonce
    def _on_recording_state(self, recording: bool, paused: bool):
        self._recording = "paused" if paused else "rec" if recording else None
        self._update_badge()

    def _on_tts(self, state: dict):
        announcing = bool(state.get("speaking") and state.get("source") == ANNOUNCE)
        if announcing != self._announcing:
            self._announcing = announcing
            self._update_badge()

    def _update_badge(self):
        with self._badge_lock:
            kind = self._recording or ("speak" if self._announcing else None)
            if kind == self._badge:
                return
            self._badge = kind
            self._tray.icon = self._icons[kind]
            self._tray.title = f"Boostache — {BADGE_TEXT[kind]}" if kind else "Boostache"
            self.app.set_badge(self._badges.get(kind), BADGE_TEXT.get(kind, ""))

    def _before_menu(self):
        # Le clic a donné le focus à la barre des tâches : on vise la fenêtre d'avant
        self._pin_target = self.app.pin_target()
        self._mail_counts = self.app.panes["gmail"].digest_counts()

    def _pin_label(self, item) -> str:
        target = self._pin_target
        if not target:
            return "Garder au premier plan"
        return f"Garder au premier plan : {_menu_text(target['title'], PIN_TITLE_MAX)}"

    def _recording_label(self, item) -> str:
        recorder = self.app.recorder
        if recorder.recording:
            label = "Arrêter l'enregistrement"
        elif recorder.next_window():
            label = f"Démarrer l'enregistrement des {recorder.window_minutes()} dernières minutes"
        elif recorder.next_live():
            label = "Démarrer la transcription en direct"
        else:
            label = "Démarrer l'enregistrement"
        return f"{label} : {recorder.describe()}"

    # Résumer les e-mails de Gmail (liste affichée dans Gmail) : nombre connu dès
    # qu'une liste a été relevée ; grisé sans e-mail ou pendant un résumé
    def _digest_label(self, scope: str) -> str:
        n = self._mail_counts.get(scope)
        if scope == "unread":
            return ("Résumer les e-mails non lus" if n is None else "Aucun e-mail non lu" if n == 0
                    else "Résumer l'e-mail non lu" if n == 1 else f"Résumer les {n} e-mails non lus")
        return ("Résumer les e-mails d'aujourd'hui" if n is None else "Aucun e-mail aujourd'hui" if n == 0
                else "Résumer l'e-mail d'aujourd'hui" if n == 1 else f"Résumer les {n} e-mails d'aujourd'hui")

    def _digest_enabled(self, scope: str) -> bool:
        return not self._mail_counts.get("busy") and self._mail_counts.get(scope) != 0

    def _run_tray(self):
        dark_menus()     # comme la fenêtre de Boostache, toujours sombre
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

    def _on_digest(self, scope: str):
        threading.Thread(target=self.app.gmail_digest, args=(scope,), daemon=True).start()

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
