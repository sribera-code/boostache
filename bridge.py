"""
bridge.py – Envoi d'événements Python → interface web.

Tous les événements passent par une file et un thread dédié : l'ordre est
garanti et les événements arrivés entre deux envois sont regroupés en un seul
appel JavaScript (window.run_js est bloquant avec WebView2 et ne doit jamais
être appelé depuis le thread de l'interface).
"""

import json
import queue
import threading

_FLUSH = object()
_STOP = object()
_MAX_PENDING = 5000


class Bridge:
    def __init__(self):
        self._window = None
        self._ready = False
        self._queue: queue.Queue = queue.Queue()
        self._pending: list = []
        threading.Thread(target=self._loop, daemon=True, name="ui-bridge").start()

    def attach(self, window):
        self._window = window

    def set_ready(self, ready: bool):
        """L'interface (re)chargée est prête : les événements en attente partent."""
        self._ready = ready
        if ready:
            self._queue.put(_FLUSH)

    def discard_pending(self):
        """Oublie les événements en attente (l'interface va recevoir un état complet)."""
        self._queue.put(("__discard__", None))

    def emit(self, event: str, payload=None):
        self._queue.put((event, payload))

    def stop(self):
        self._queue.put(_STOP)

    # ─────────────────────────────────────────
    def _loop(self):
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            batch = [] if item is _FLUSH else [item]
            # Récupère tout ce qui est déjà en file
            while len(batch) < 2000:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _STOP:
                    return
                if nxt is not _FLUSH:
                    batch.append(nxt)
            for it in batch:
                if it[0] == "__discard__":
                    self._pending.clear()
                else:
                    self._pending.append(it)
            if not self._ready or self._window is None or not self._pending:
                del self._pending[:-_MAX_PENDING]
                continue
            events, self._pending = self._coalesce(self._pending), []
            script = ("window.__boostache && window.__boostache.dispatch("
                      + json.dumps(events, ensure_ascii=False, default=str) + ")")
            try:
                self._window.run_js(script)
            except Exception:
                pass

    @staticmethod
    def _coalesce(items: list) -> list:
        """Fusionne les morceaux consécutifs de sortie terminal / de réponse LLM."""
        out: list = []
        for event, payload in items:
            if out and event == out[-1][0] and isinstance(payload, dict):
                prev = out[-1][1]
                if event == "term:data" and prev["slot"] == payload["slot"] \
                        and prev.get("session") == payload.get("session"):
                    prev["data"] += payload["data"]
                    continue
                if event == "chat:delta" and prev["tab"] == payload["tab"]:
                    prev["content"] += payload["content"]
                    prev["thinking"] += payload["thinking"]
                    continue
            if isinstance(payload, dict):
                payload = dict(payload)
            out.append((event, payload))
        return [{"event": e, "data": p} for e, p in out]
