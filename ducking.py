"""
ducking.py – Baisse le son des autres applications pendant une lecture à voix
haute (annonces, rappels, réponses, notes…, speech.py) : chaque application ouverte
sur la sortie par défaut (vidéo, musique, appel… ; aussi celles qui se taisent à
cet instant, entre deux morceaux par exemple, et reprendraient) descend à une
part de son volume dans le mélangeur de Windows (réglage assistant_duck_level :
LEVELS, 0 : coupée), puis remonte à la fin de la lecture. Boostache lui-même
(la voix) n'est pas touché.

Réglage PAUSE : en plus, les lecteurs en cours de lecture qui se commandent par les
touches multimédia de Windows (YouTube dans le navigateur, Spotify…) sont mis en
pause, puis relancés à la fin s'ils sont toujours en pause ; les autres
applications (appel, jeu…) baissent à DEFAULT_LEVEL. Paquet winrt absent : la
baisse seule.

Windows retient le volume de chaque application d'une fois sur l'autre : les
volumes d'origine sont notés dans DATA_DIR/ducking.json pendant la baisse et
remis au lancement suivant si Boostache s'est arrêté avant de les remonter.
Un volume changé par l'utilisateur pendant la baisse n'est jamais écrasé.
"""

import asyncio
import os
import threading
import time

import comtypes

import wasapi
from storage import DATA_DIR, read_json, write_json

LEVELS = (50, 25, 10, 0)     # volume des autres applications pendant la lecture, en % du leur (0 : coupées)
DEFAULT_LEVEL = 10
PAUSE = "pause"         # lecteurs mis en pause, les autres applications baissées à DEFAULT_LEVEL
CHOICES = (*LEVELS, PAUSE)
MEDIA_TIMEOUT = 2       # attente au plus d'un lecteur qui se met en pause ou repart (s)
DOWN = 0.3              # durée de la baisse (s)
UP = 0.8                # durée de la remontée (s)
HOLD = 0.8              # nouvelle lecture dans ce délai : le son reste baissé
STEP = 1 / 30           # pas des fondus (s)
TOUCHED = 0.02          # écart au volume posé : l'utilisateur l'a changé
STATE_FILE = DATA_DIR / "ducking.json"


class Ducker:
    """hold() / release() : tant qu'une lecture le tient, les autres applications sont baissées."""

    def __init__(self, logger):
        self._logger = logger
        self._cond = threading.Condition()
        self._users = 0
        self._level = DEFAULT_LEVEL / 100     # celui de la dernière lecture qui a demandé la baisse
        self._pause = False                   # … et si elle met les lecteurs en pause
        self._closed = False
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()       # baisse (et pause) faite, jusqu'au début de la remontée

    def hold(self, level: float | str = DEFAULT_LEVEL / 100) -> threading.Event:
        """level : part de leur volume gardée par les autres applications (0 : coupées),
        ou PAUSE. Renvoie un événement levé une fois les autres sons baissés (ou en pause) :
        la voix l'attend pour ne pas parler par-dessus."""
        with self._cond:
            if self._closed:
                done = threading.Event()
                done.set()
                return done
            self._pause = level == PAUSE
            self._level = DEFAULT_LEVEL / 100 if self._pause else max(0.0, min(float(level), 1.0))
            self._users += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="ducking")
                self._thread.start()
            self._cond.notify_all()
            return self._ready

    def release(self):
        with self._cond:
            self._users = max(0, self._users - 1)
            self._cond.notify_all()

    def close(self):
        """Fermeture de Boostache : les volumes remontent tout de suite."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
            thread = self._thread
        if thread:
            thread.join(3)

    def recover(self):
        """Volumes restés baissés (Boostache arrêté pendant une lecture) : remis, en arrière-plan."""
        if STATE_FILE.exists():
            threading.Thread(target=self._recover, daemon=True, name="ducking-recover").start()

    # ── Interne ────────────────────────────
    def _loop(self):
        with wasapi.com_thread():
            while True:
                with self._cond:
                    while not self._users and not self._closed:
                        self._cond.wait()
                    if self._closed:
                        self._thread = None
                        return
                with self._cond:
                    pause = self._pause
                ducked, paused = [], []
                try:
                    ducked = self._duck()
                    paused = self._pause_media() if pause else []
                finally:
                    self._ready.set()
                with self._cond:
                    while not self._closed:
                        if self._users:
                            self._cond.wait()
                        elif not self._cond.wait(HOLD) and not self._users:
                            break           # HOLD écoulé sans nouvelle lecture
                    closed = self._closed
                    self._ready.clear()     # une lecture qui arrive pendant la remontée attend la baisse suivante
                self._resume_media(paused)
                self._restore(ducked, fade=not closed)

    def _duck(self) -> list[dict]:
        try:
            sessions = wasapi.app_sessions(os.getpid())
        except (comtypes.COMError, OSError) as e:
            self._logger.log(f"Baisse du son des autres applications impossible : {e}", level="warning")
            return []
        ducked = []
        for s in sessions:
            try:
                before = s["volume"].GetMasterVolume()
            except comtypes.COMError:
                continue
            if before > 0.01:
                ducked.append({**s, "before": before, "set": before * self._level})
        level = "coupé" if not self._level else f"baissé à {round(self._level * 100)} %"
        if ducked:
            write_json(STATE_FILE, {"sessions": [{k: d[k] for k in ("key", "before", "set")} for d in ducked]})
            self._fade(ducked, "before", "set", DOWN)
            names = sorted({d["name"] for d in ducked}, key=str.lower)
            self._logger.log(f"Lecture à voix haute : son {level} pour {', '.join(names[:5])}"
                             + (f" et {len(names) - 5} autres" if len(names) > 5 else "") + ".")
        else:
            self._logger.log("Lecture à voix haute : aucune autre application sur la sortie audio "
                             "par défaut, rien à baisser.")
        return ducked

    def _restore(self, ducked: list[dict], fade: bool):
        # Volume changé pendant la lecture (par l'utilisateur) : il reste tel quel
        kept = []
        for d in ducked:
            try:
                if abs(d["volume"].GetMasterVolume() - d["set"]) <= TOUCHED:
                    kept.append(d)
            except comtypes.COMError:
                pass                        # application fermée
        self._fade(kept, "set", "before", UP if fade else 0)
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def _pause_media(self) -> list:
        """Lecteurs en cours de lecture mis en pause : leurs sessions (à relancer)."""
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Manager,
                GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status)
        except ImportError:
            self._logger.log("Mise en pause des lecteurs impossible : paquet winrt absent "
                             "(pip install -r requirements.txt) ; leur son est seulement baissé.", level="warning")
            return []

        async def pause():
            manager = await asyncio.wait_for(Manager.request_async(), MEDIA_TIMEOUT)
            paused = []
            for session in manager.get_sessions():
                try:
                    info = session.get_playback_info()
                    if (info.playback_status == Status.PLAYING and info.controls.is_pause_enabled
                            and await asyncio.wait_for(session.try_pause_async(), MEDIA_TIMEOUT)):
                        paused.append(session)
                except (OSError, asyncio.TimeoutError):
                    continue                # lecteur fermé ou qui ne répond pas
            return paused

        try:
            paused = asyncio.run(pause())
        except (OSError, asyncio.TimeoutError) as e:
            self._logger.log(f"Mise en pause des lecteurs impossible : {e}", level="warning")
            return []
        if paused:
            names = sorted({_media_name(s) for s in paused}, key=str.lower)
            self._logger.log(f"Lecture à voix haute : mis en pause {', '.join(names)}.")
        return paused

    def _resume_media(self, paused: list):
        """Relance les lecteurs mis en pause, sauf ceux repartis ou arrêtés entre-temps."""
        if not paused:
            return
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status

        async def resume():
            for session in paused:
                try:
                    if session.get_playback_info().playback_status == Status.PAUSED:
                        await asyncio.wait_for(session.try_play_async(), MEDIA_TIMEOUT)
                except (OSError, asyncio.TimeoutError):
                    continue

        try:
            asyncio.run(resume())
        except OSError as e:
            self._logger.log(f"Lecteurs mis en pause non relancés : {e}", level="warning")

    @staticmethod
    def _fade(sessions: list[dict], start: str, end: str, duration: float):
        steps = max(1, round(duration / STEP))
        for k in range(1, steps + 1):
            t = k / steps
            for d in sessions:
                try:
                    d["volume"].SetMasterVolume(d[start] + (d[end] - d[start]) * t, None)
                except comtypes.COMError:
                    pass
            if k < steps:
                time.sleep(duration / steps)

    def _recover(self):
        data = read_json(STATE_FILE)
        saved = data.get("sessions", []) if isinstance(data, dict) else []
        fixed = 0
        try:
            with wasapi.com_thread():
                for s in wasapi.app_sessions(os.getpid()):
                    for d in saved:
                        try:
                            if d.get("key") == s["key"] and abs(s["volume"].GetMasterVolume() - d["set"]) <= TOUCHED:
                                s["volume"].SetMasterVolume(float(d["before"]), None)
                                fixed += 1
                                break
                        except (comtypes.COMError, KeyError, TypeError, ValueError):
                            continue
        except (comtypes.COMError, OSError) as e:
            self._logger.log(f"Volumes des applications non remis : {e}", level="warning")
            return
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        if fixed:
            self._logger.log(f"Volume remis pour {fixed} application{'s' if fixed > 1 else ''} "
                             "(baissé pendant une lecture, Boostache arrêté avant la fin).")


def _media_name(session) -> str:
    """« Chrome », « Spotify.exe », « SpotifyAB.SpotifyMusic_…!Spotify » → nom lisible."""
    try:
        app = session.source_app_user_model_id or ""
    except OSError:
        app = ""
    app = app.rsplit("!", 1)[-1].rsplit("\\", 1)[-1]
    return (app[:-4] if app.lower().endswith(".exe") else app) or "un lecteur"
