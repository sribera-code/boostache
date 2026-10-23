"""
llm.py – Appels à Ollama des sections : file d'attente commune et nouvel essai.

Toutes les sections parlent au même Ollama local, qui n'a qu'une carte graphique
(ou le processeur) : deux appels en même temps se partagent la machine, et deux
modèles différents se chassent l'un l'autre de la mémoire. Chaque appel passe donc
par la file (gate) avec une priorité :

- INTERACTIVE : ce que l'utilisateur attend (conversation, question, suggestion de
  réponse, résumé d'un fichier affiché…) — jamais retenu ;
- ANNOUNCE : résumé d'une annonce vocale — attend la fin des appels interactifs,
  ANNOUNCE_WAIT s au plus ;
- BATCH : série lancée par l'utilisateur (résumé des e-mails de Gmail) — passe entre
  deux demandes interactives ;
- BACKGROUND : en arrière-plan (vecteurs des documents).

Hors INTERACTIVE, un appel à la fois, par priorité puis dans l'ordre d'arrivée.

Le moteur d'Ollama (llama-server) s'arrête parfois net en chargeant un modèle
(« process has terminated: exit status 0xc0000409 ») ; l'appel suivant le relance :
l'appel est refait une fois (un flux, seulement s'il n'a encore rien rendu).

L'état de la file (appels en cours et en attente) est envoyé à l'interface
(gate.on_change → évènement « ollama:activity », section Ollama).
"""

import itertools
import threading
import time

from engine import logger

try:
    import ollama as _ollama
except ImportError:
    _ollama = None

INTERACTIVE, ANNOUNCE, BATCH, BACKGROUND = 0, 1, 2, 3
ANNOUNCE_WAIT = 30.0           # s au plus derrière les appels interactifs (l'annonce ne doit pas trop tarder)
POLL = 0.5                     # s entre deux vérifications d'une attente (arrêt demandé, ANNOUNCE_WAIT)
RETRY_DELAY = 1.5              # s avant de refaire un appel dont le moteur s'est arrêté
CHANGE_DELAY = 0.3             # s de regroupement des changements envoyés à l'interface
# Moteur arrêté en cours de route (pas absent : chat.RUNNER_MISSING)
CRASH_MARKS = ("process has terminated", "unexpectedly stopped")


def crashed(e: Exception) -> bool:
    """Le moteur d'Ollama s'est arrêté pendant l'appel : le refaire le relance."""
    text = str(e)
    return (_ollama is not None and isinstance(e, _ollama.ResponseError)
            and "not found" not in text and any(m in text for m in CRASH_MARKS))


class _Job:
    __slots__ = ("label", "priority", "ticket", "queued", "started")

    def __init__(self, label: str, priority: int, ticket: int):
        self.label, self.priority, self.ticket = label, priority, ticket
        self.queued = time.time()
        self.started = 0.0


class Gate:
    def __init__(self):
        self._cond = threading.Condition()
        self._running: list[_Job] = []
        self._waiting: list[_Job] = []
        self._tickets = itertools.count()
        self._timer: threading.Timer | None = None
        self._sent = None
        self.on_change = None          # fn(snapshot) : la file a changé (regroupé sur CHANGE_DELAY)

    # ── Tour de chaque appel ──────────────────
    def acquire(self, label: str, priority: int = INTERACTIVE, stop: threading.Event | None = None):
        """Attend le tour de cet appel ; None si stop est levé pendant l'attente."""
        job = _Job(label, priority, next(self._tickets))
        with self._cond:
            self._waiting.append(job)
            self._changed()
            try:
                while self._blocked(job, time.time() - job.queued):
                    if stop is not None and stop.is_set():
                        return None
                    self._cond.wait(POLL)
            finally:
                self._waiting.remove(job)
                self._cond.notify_all()
                self._changed()
            job.started = time.time()
            self._running.append(job)
        return job

    def release(self, job):
        if job is None:
            return
        with self._cond:
            if job in self._running:
                self._running.remove(job)
            self._cond.notify_all()
            self._changed()

    def _blocked(self, job: _Job, waited: float) -> bool:
        if job.priority == INTERACTIVE:
            return False
        if any(j.priority != INTERACTIVE for j in self._running):
            return True                # un appel hors interactif à la fois
        if min(self._waiting, key=lambda j: (j.priority, j.ticket)) is not job:
            return True                # un autre passe avant
        if not any(j.priority == INTERACTIVE for j in self._running):
            return False
        return job.priority != ANNOUNCE or waited < ANNOUNCE_WAIT

    # ── État pour l'interface ─────────────────
    def snapshot(self) -> dict:
        with self._cond:
            running = sorted(self._running, key=lambda j: j.started)
            waiting = sorted(self._waiting, key=lambda j: (j.priority, j.ticket))
            return {"running": [{"label": j.label, "since": j.started} for j in running],
                    "waiting": [{"label": j.label, "since": j.queued} for j in waiting]}

    def _changed(self):
        # Sous self._cond : envoi différé, les appels qui s'enchaînent (vecteurs) n'en font qu'un
        if self.on_change is not None and self._timer is None:
            self._timer = threading.Timer(CHANGE_DELAY, self._send)
            self._timer.daemon = True
            self._timer.start()

    def _send(self):
        with self._cond:
            self._timer = None
        snap = self.snapshot()
        key = ([j["label"] for j in snap["running"]], [j["label"] for j in snap["waiting"]])
        if key == self._sent:
            return
        self._sent = key
        try:
            self.on_change(snap)
        except Exception as e:
            logger.log(f"Ollama : file d'attente non envoyée à l'interface ({e})")


gate = Gate()


# ─────────────────────────────────────────────
#  Appels
# ─────────────────────────────────────────────
def chat(label: str, priority: int = INTERACTIVE, *, client=None, stop: threading.Event | None = None, **kwargs):
    """client.chat (sinon celui du module ollama) à son tour, refait une fois si le moteur
    s'arrête. stream=True : un générateur, qui garde son tour jusqu'à sa fin ou sa
    fermeture et ne rend rien si stop est levé pendant l'attente ; sinon la réponse, ou
    None si stop est levé pendant l'attente."""
    call = (client or _ollama).chat
    if kwargs.get("stream"):
        return _stream(label, priority, stop, call, kwargs)
    return _call(label, priority, stop, call, kwargs)


def embed(label: str, priority: int = INTERACTIVE, *, client=None, stop: threading.Event | None = None, **kwargs):
    """client.embed à son tour (comme chat, sans flux)."""
    return _call(label, priority, stop, (client or _ollama).embed, kwargs)


def _retrying(label: str, e: Exception):
    logger.warning(f"Ollama : moteur arrêté net ({label}) : nouvel essai.", details=str(e))
    time.sleep(RETRY_DELAY)


def _call(label, priority, stop, call, kwargs):
    job = gate.acquire(label, priority, stop)
    if job is None:
        return None
    try:
        try:
            return call(**kwargs)
        except Exception as e:
            if not crashed(e):
                raise
            _retrying(label, e)
        return call(**kwargs)
    finally:
        gate.release(job)


def _stream(label, priority, stop, call, kwargs):
    job = gate.acquire(label, priority, stop)
    if job is None:
        return
    inner = None
    try:
        for attempt in (1, 2):
            inner = call(**kwargs)
            try:
                first = next(inner)
            except StopIteration:
                return
            except Exception as e:
                if attempt == 2 or not crashed(e):
                    raise
                _close(inner)
                _retrying(label, e)
                continue
            break
        yield first
        yield from inner
    finally:
        _close(inner)
        gate.release(job)


def _close(stream):
    if stream is not None and hasattr(stream, "close"):
        try:
            stream.close()
        except Exception:
            pass
