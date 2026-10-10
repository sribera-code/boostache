"""
live.py – Assistant live : tant qu'il est actif, la fenêtre utilisée est
capturée à intervalle régulier, son texte lu (OCR, voir ocr.py) et un modèle
Ollama fait dessus la tâche du mode choisi : proposer une réponse au dernier
message, résumer, expliquer, corriger le texte en cours d'écriture, ou suivre
une consigne libre. On peut aussi lui poser une question sur la fenêtre du moment.

Un petit modèle local juge mal s'il a « quelque chose d'utile » à dire, mais
fait bien une tâche précise : chaque mode en est une, et rend toujours un
résultat (ou dit que la tâche ne s'applique pas à cette fenêtre).

Économe : le modèle n'est appelé que si de nouvelles lignes de texte sont
apparues depuis la dernière analyse de cette fenêtre (une heure qui change,
un survol ne comptent pas) ; un résultat déjà donné (même dernier message,
même sujet) n'est pas répété. Rien n'est capturé pendant une absence (ni
clavier ni souris depuis IDLE_PAUSE) ou tant qu'Ollama ne répond pas.
L'intervalle court à partir de la fin de l'analyse précédente : les appels ne
s'empilent jamais.

Les captures ne quittent pas ce PC : analysées par Ollama en local, gardées
dans le cache le temps d'être affichées, effacées à la fermeture.
"""

import base64
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from difflib import SequenceMatcher

import ocr
from chat import HELP_MAX_TEXT, MODEL_IMG_SIDE, keep_alive, num_ctx
from engine import logger, strip_markdown, tts
from storage import CACHE_DIR, settings
from winutil import active_window, idle_seconds, window_screenshot

INTERVALS    = (15, 30, 60, 120, 300)  # secondes entre deux captures (réglage live_interval)
MAX_ITEMS    = 40                      # résultats gardés, les plus récents
RECENT_ITEMS = 10                      # résultats comparés au nouveau (déjà donné ?)
MAX_WINDOWS  = 8                       # fenêtres dont on retient le dernier état analysé
IDLE_PAUSE   = 300                     # sans clavier ni souris depuis (s) : plus de capture
IDLE_CHECK   = 5                       # en pause : reprise vérifiée toutes les (s)
CALL_TIMEOUT = 180                     # réponse d'Ollama attendue au plus (s)
THUMB_SIDE   = 480                     # miniatures affichées (pixels)
LIVE_DIR     = CACHE_DIR / "boostache_live"   # vidé aussi au démarrage (storage.clear_cache)
# Fenêtre inchangée : moins de SAME_PIXELS des pixels d'une version réduite
# (DIFF_WIDTH de large) ont changé de plus de DIFF_LEVEL niveaux de gris
DIFF_WIDTH   = 320
DIFF_LEVEL   = 32
SAME_PIXELS  = 0.001
# Même résultat (même dernier message, même sujet…) : textes aussi proches (difflib),
# ou autant de mots (4 lettres et plus) en commun
SAME_TEXT    = 0.8
SAME_WORDS   = 0.6

SYSTEM = (
    "Tu es un assistant qui suit en direct la fenêtre que l'utilisateur utilise sur son PC. Tu reçois une "
    "capture de cette fenêtre et le texte qu'elle affiche, lu par reconnaissance de caractères dans l'ordre "
    "de lecture : il peut contenir des erreurs de lecture, corrige-les mentalement. Fais la tâche demandée "
    "et réponds en JSON, en français (sauf si la tâche demande une autre langue). situation : ce que "
    "l'utilisateur est en train de faire, en une phrase courte."
)
_STR = {"type": "string"}
_LIST = {"type": "array", "items": {"type": "string"}}

# Modes : tâche, champs JSON, champ clé (même valeur qu'un résultat récent : pas répété),
# champ du texte principal, titre (ou champ du titre), texte quand la tâche ne s'applique pas.
# plain : texte à copier tel quel (pas du Markdown) ; announce : notification et voix pour
# chaque nouveau résultat ; in_place : un nouveau résultat sur la même fenêtre remplace le
# précédent (texte en cours d'écriture).
MODES = {
    "reply": {
        "task": ("Si la fenêtre montre une conversation (messagerie, e-mail, commentaires…), rédige la réponse "
                 "que l'utilisateur pourrait envoyer au dernier message reçu (pas à un message qu'il a écrit "
                 "lui-même) : prête à copier, naturelle, dans la langue et le ton de la conversation, sans "
                 "signature ni information inventée. dernier_message : ce dernier message reçu, recopié. "
                 "S'il n'y a pas de conversation, laisse dernier_message et reponse vides."),
        "props": {"situation": _STR, "dernier_message": _STR, "reponse": _STR},
        "key": "dernier_message", "text": "reponse", "quote": "dernier_message", "plain": True,
        "title": "Réponse proposée",
        "empty": "Pas de conversation à laquelle répondre dans cette fenêtre.",
        "announce": True,
    },
    "summary": {
        "task": ("Donne les 3 à 5 points clés de ce que l'utilisateur lit dans cette fenêtre, chacun en une "
                 "phrase courte, avec les chiffres, noms et dates importants. Ne recopie pas le texte. "
                 "sujet : le sujet en quelques mots. S'il n'y a rien à lire, laisse sujet et points vides."),
        "props": {"situation": _STR, "sujet": _STR, "points": _LIST},
        "key": "sujet", "text": "points", "title_field": "sujet",
        "title": "Résumé",
        "empty": "Rien à résumer dans cette fenêtre.",
        "announce": True,
    },
    "explain": {
        "task": ("Explique ce qui peut bloquer ou interroger l'utilisateur dans cette fenêtre. S'il y a un "
                 "message d'erreur ou un avertissement : une phrase sur sa cause probable, puis les étapes "
                 "numérotées pour le résoudre. Sinon : les termes, réglages ou chiffres peu évidents, "
                 "simplement. explication : en Markdown, adressée directement à l'utilisateur (vouvoiement), "
                 "120 mots au plus, en citant ce qui est affiché. sujet : ce que tu expliques, en quelques mots."),
        "props": {"situation": _STR, "sujet": _STR, "explication": _STR},
        "key": "sujet", "text": "explication", "title_field": "sujet",
        "title": "Explication",
        "empty": "Rien à expliquer dans cette fenêtre.",
        "announce": True,
    },
    "correct": {
        "task": ("Repère le texte que l'utilisateur est en train d'écrire (zone de saisie, e-mail ou document "
                 "en cours de rédaction) et corrige-le : orthographe, grammaire, ponctuation, formulations "
                 "maladroites, sans changer le sens ni le ton. Ignore ce qui vient visiblement d'une erreur de "
                 "lecture. texte_corrige : seulement le texte qu'il écrit (sans destinataire, objet, boutons ni "
                 "autre élément de la fenêtre), entier et corrigé, prêt à copier ; corrections : chaque "
                 "correction, sous la forme « avant → après ». S'il n'écrit aucun texte, laisse tout vide."),
        "props": {"situation": _STR, "texte_corrige": _STR, "corrections": _LIST},
        "key": "texte_corrige", "text": "texte_corrige", "notes": "corrections", "plain": True,
        "title": "Texte corrigé",
        "empty": "Aucun texte en cours d'écriture dans cette fenêtre.",
        "announce": False, "in_place": True,
    },
    "custom": {
        "task": ("Applique cette consigne de l'utilisateur à ce que montre la fenêtre : « {goal} ». "
                 "titre : ton résultat en quelques mots. Si la consigne ne s'applique pas à cette fenêtre, "
                 "laisse titre et resultat vides."),
        "props": {"situation": _STR, "titre": _STR, "resultat": _STR},
        "key": "resultat", "text": "resultat", "title_field": "titre",
        "title": "Consigne",
        "empty": "La consigne ne s'applique pas à cette fenêtre.",
        "announce": True,
    },
}
QUESTION = {
    "task": ("Réponds à cette question de l'utilisateur en t'appuyant sur ce que montre la fenêtre : « {question} ». "
             "Concrètement : jusqu'à 6 phrases, ou une courte liste d'étapes."),
    "props": {"situation": _STR, "reponse": _STR},
    "key": None, "text": "reponse", "title": "Réponse",
    "empty": "Le modèle n'a pas répondu.",
}


def _signature(image):
    """Version réduite en niveaux de gris, pour savoir si la fenêtre a changé."""
    from PIL import Image
    height = max(1, round(image.height * DIFF_WIDTH / max(1, image.width)))
    return image.resize((DIFF_WIDTH, height), Image.BILINEAR).convert("L")


def _changed(a, b) -> float:
    """Part des pixels qui ont changé d'une signature à l'autre (1 : autre taille)."""
    from PIL import ImageChops
    if a.size != b.size:
        return 1.0
    return sum(ImageChops.difference(a, b).histogram()[DIFF_LEVEL:]) / (a.width * a.height)


def _lines(text: str) -> frozenset[str]:
    """Lignes de texte qui comptent, normalisées : seulement leurs mots (ni chiffres, ni
    ponctuation, ni casse), pour qu'une heure ou un compteur qui change ne compte pas."""
    lines = set()
    for line in text.splitlines():
        words = re.findall(r"[^\W\d_]+", line.casefold())
        if sum(len(w) for w in words) >= 4:
            lines.add(" ".join(words))
    return frozenset(lines)


def _thumbnail(image) -> str:
    im = image.copy()
    im.thumbnail((THUMB_SIDE, THUMB_SIDE))
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _model_image(image) -> bytes:
    """Capture telle qu'envoyée au modèle : réduite comme dans les conversations."""
    from PIL import Image
    im = image
    if max(im.size) > MODEL_IMG_SIDE:
        im = im.copy()
        im.thumbnail((MODEL_IMG_SIDE, MODEL_IMG_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w{4,}", text.casefold()))


def _same(a: str, b: str) -> bool:
    """Deux résultats disent-ils la même chose (même dernier message, même sujet…) ?"""
    a, b = a.casefold().strip()[:600], b.casefold().strip()[:600]
    if not a or not b:
        return False
    if SequenceMatcher(None, a, b).ratio() >= SAME_TEXT:
        return True
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= SAME_WORDS


def _cut(text: str, size: int) -> str:
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def _clean(value) -> str:
    return value.strip().strip("\"'«»“” ").strip() if isinstance(value, str) else ""


def _delete(path: str | None):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _ui_item(item: dict | None) -> dict | None:
    return {k: v for k, v in item.items() if k != "path"} if item else None


class LiveService:
    """chat : chat.ChatService (modèles, présence d'Ollama).
    is_visible() : la fenêtre de Boostache est affichée.
    notify(titre, texte) : notification Windows, quand la fenêtre est masquée (facultatif)."""

    def __init__(self, bridge, chat, is_visible=lambda: False, notify=None):
        self._bridge = bridge
        self._chat = chat
        self._is_visible = is_visible
        self._notify = notify
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._client = None
        self.running = False
        self._session = 0                 # incrémenté à chaque marche / arrêt / changement de mode
        self._request: dict | None = None # analyse demandée : {"question": …} ("" : maintenant)
        self._last_end = float("-inf")    # fin de la dernière capture (time.monotonic)
        # Fenêtres déjà vues, (hwnd, titre) → {sig : signature de la dernière capture lue,
        # lines : lignes de la dernière analyse, current : son résultat}
        self._windows: dict[tuple, dict] = {}
        self.items: list[dict] = []       # résultats, du plus récent au plus ancien
        self.current: dict | None = None  # résultat affiché en direct (fenêtre du moment)
        self.phase = "off"                # off | wait | idle | capture | read | think
        self.window = ""                  # fenêtre en cours d'analyse
        self.next_at = None               # prochaine capture (ms depuis 1970), pour le compte à rebours
        self.pending = False              # analyse demandée (maintenant, question) en attente ou en cours
        self.asking = ""                  # question en attente ou en cours
        self.note, self.note_kind = "", ""

    # ── État ──────────────────────────────────
    def model(self) -> str:
        """Modèle choisi pour l'assistant ; sinon celui de l'aide contextuelle ou de la
        dernière conversation s'il lit les images, sinon le premier qui les lit, sinon
        n'importe lequel (il n'aura que le texte de la fenêtre)."""
        models = self._chat.models
        chosen = settings.get("live_model", "")
        if chosen in models:
            return chosen
        wanted = [settings.get("help_model", ""), settings.get("last_model", ""), *models]
        wanted = [m for m in dict.fromkeys(wanted) if m in models]
        return next((m for m in wanted if self._chat.sees_images(m)), wanted[0] if wanted else "")

    @staticmethod
    def mode() -> str:
        value = settings.get("live_mode", "reply")
        return value if value in MODES else "reply"

    def _state(self) -> dict:
        model = self.model()
        with self._lock:
            return {"running": self.running, "phase": self.phase, "window": self.window,
                    "next_at": self.next_at, "pending": self.pending, "asking": self.asking,
                    "note": self.note, "note_kind": self.note_kind, "model": model,
                    "mode": self.mode(), "current": _ui_item(self.current)}

    def snapshot(self) -> dict:
        state = self._state()
        with self._lock:
            items = [_ui_item(i) for i in self.items]
        return {"available": True, "intervals": INTERVALS, "items": items, **state}

    def _set(self, **changes):
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)
        self.refresh()

    def refresh(self):
        """Envoie l'état à l'interface (modèle changé…)."""
        self._bridge.emit("live:state", self._state())

    def _emit_items(self):
        with self._lock:
            items = [_ui_item(i) for i in self.items]
        self._bridge.emit("live:items", {"items": items})

    @staticmethod
    def _interval() -> int:
        value = settings.get("live_interval", 30)
        return value if value in INTERVALS else 30

    # ── Marche / arrêt ────────────────────────
    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
            self._session += 1
            self._last_end = float("-inf")     # première capture tout de suite
            self.note, self.note_kind = "", ""
        logger.log(f"Assistant live activé (toutes les {self._interval()} s).")
        self._ensure_worker()
        self.refresh()

    def stop(self):
        with self._lock:
            if not self.running:
                return
            self.running = False
            self._session += 1
            self.next_at = None
            if not self.pending:
                self.phase, self.window = "off", ""
        self._wake.set()
        logger.log("Assistant live arrêté.")
        self.refresh()

    def set_mode(self):
        """Mode changé (réglage live_mode) : les fenêtres sont à analyser de nouveau,
        tout de suite si l'assistant tourne."""
        with self._lock:
            self._session += 1                 # une analyse en cours dans l'ancien mode est ignorée
            self._windows.clear()
            self.current = None
            self.note, self.note_kind = "", ""
            running = self.running
        if running:
            self.analyze_now()
        else:
            self.refresh()

    def analyze_now(self, question: str = ""):
        """Analyse la fenêtre utilisée tout de suite (même inchangée), en marche ou non :
        un nouveau résultat même s'il a déjà été donné. Avec une question, le modèle y répond."""
        question = (question or "").strip()[:1000]
        with self._lock:
            if not question and self._request:
                return                          # déjà demandée (avec ou sans question)
            self._request = {"question": question}
            self.pending = True
            if question:
                self.asking = question
        self._ensure_worker()
        self.refresh()

    def reschedule(self):
        """Intervalle modifié : la prochaine capture est recalculée."""
        self._wake.set()

    def shutdown(self):
        self.stop()
        shutil.rmtree(LIVE_DIR, ignore_errors=True)

    # ── Boucle ────────────────────────────────
    def _ensure_worker(self):
        with self._lock:
            if self._worker is None:
                self._worker = threading.Thread(target=self._loop, daemon=True, name="live")
                self._worker.start()
        self._wake.set()

    def _loop(self):
        while True:
            with self._lock:
                request, self._request = self._request, None
                if request is None and not self.running:
                    self._worker = None
                    self.phase, self.window, self.next_at = "off", "", None
                    break
                if request is not None:
                    self.asking = request["question"]
                session = self._session
            if request is None:
                wait = self._last_end + self._interval() - time.monotonic()
                if wait <= 0 and idle_seconds() >= IDLE_PAUSE:
                    if self.phase != "idle":
                        self._set(phase="idle", next_at=None)
                    wait = IDLE_CHECK
                elif wait > 0:
                    self._set(phase="wait", next_at=int((time.time() + wait) * 1000))
                if wait > 0:
                    self._wake.wait(wait)
                    self._wake.clear()
                    continue
            try:
                self._analyze(request, session)
            except Exception as e:
                logger.log(f"Assistant live : {e}")
                self._set(note=f"Analyse impossible : {e}", note_kind="error")
            with self._lock:
                self._last_end = time.monotonic()
                if request and self._request is None:     # sinon, une autre demande attend
                    self.pending, self.asking = False, ""
                self.window = ""
        self.refresh()

    def _analyze(self, request: dict | None, session: int):
        """Une capture : request None pour celles faites à intervalle régulier."""
        question = request["question"] if request else ""
        mode = self.mode()
        goal = (settings.get("live_goal", "") or "").strip()
        if request is None and session != self._session:
            return                              # arrêté entre-temps
        if not self._chat.available:
            self._chat.check()
            self._set(note="Ollama ne répond pas : l'assistant reprendra dès qu'il répondra.",
                      note_kind="error")
            return
        model = self.model()
        if not model:
            self._set(note=self._chat.models_error or "Aucun modèle Ollama installé.", note_kind="error")
            return
        if mode == "custom" and not goal and not question:
            self._set(note="Écris d'abord ta consigne (mode Libre).", note_kind="error")
            return

        self._set(phase="capture", next_at=None)
        window = active_window()
        image = window_screenshot(window["hwnd"]) if window else None
        if image is None:
            self._set(note="Aucune fenêtre à analyser (bureau, fenêtre réduite ou protégée).", note_kind="")
            return
        title, app = window["title"], window["app"]
        key, signature = (window["hwnd"], title), _signature(image)
        with self._lock:
            seen = self._windows.get(key)
        if request is None and seen and _changed(seen["sig"], signature) < SAME_PIXELS:
            self._unchanged(seen, "Fenêtre inchangée.")
            return

        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = str(LIVE_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.png")
        image.save(path, "PNG")
        self._set(phase="read", window=title or app)
        text = ocr.read_text(path)[:HELP_MAX_TEXT]
        lines = _lines(text)
        # Rien de nouveau à lire (survol, curseur, heure) : pas d'appel au modèle. Sans texte
        # lu (OCR indisponible), seul le changement de l'image compte.
        if request is None and seen and lines and not lines - seen["lines"]:
            _delete(path)
            with self._lock:
                seen["sig"] = signature
            self._unchanged(seen, "Pas de nouveau texte dans la fenêtre.")
            return

        cfg = QUESTION if question else MODES[mode]
        self._set(phase="think")
        try:
            data = self._ask_model(model, cfg, image, title, app, text, question=question, goal=goal)
        except Exception as e:
            _delete(path)
            error = self._chat.failure(e)
            logger.log(f"Assistant live ({model}) : {error}")
            self._set(note=error, note_kind="error")
            return
        if (request is None and session != self._session) or (not question and mode != self.mode()):
            _delete(path)                       # arrêté ou mode changé pendant l'analyse
            return
        self._record(request, mode, cfg, data, key, signature, lines, image, path, title, app, model)

    def _unchanged(self, seen: dict, note: str):
        """Fenêtre déjà analysée : son dernier résultat revient en direct."""
        with self._lock:
            back = seen["current"] is not self.current
            self.current = seen["current"]
        self._set(note=note if not back else "", note_kind="")

    def _record(self, request, mode, cfg, data, key, signature, lines, image, path, title, app, model):
        """Résultat d'une analyse : nouveau (mis en tête et annoncé), déjà donné (il revient
        en direct) ou sans objet pour cette fenêtre."""
        question = request["question"] if request else ""
        now = datetime.now().isoformat(timespec="seconds")
        situation = _clean(data.get("situation"))
        raw = data.get(cfg["text"])
        if isinstance(raw, list):
            body = "\n".join(f"- {p}" for p in (_clean(x) for x in raw) if p)
        elif cfg.get("plain"):
            body = _clean(raw)                  # texte à copier tel quel : sans guillemets autour
        else:
            body = raw.strip() if isinstance(raw, str) else ""
        raw_notes = data.get(cfg["notes"]) if cfg.get("notes") else None
        notes = [n for n in (_clean(x) for x in raw_notes) if n] if isinstance(raw_notes, list) else []
        found = _clean(data.get(cfg["key"])) if cfg["key"] else ""
        heading = _clean(data.get(cfg["title_field"])) if cfg.get("title_field") else ""
        thumb = _thumbnail(image)
        base = {"at": now, "window": title, "app": app, "mode": "question" if question else mode,
                "situation": situation, "thumb": thumb}

        if not body:
            _delete(path)
            current = {**base, "empty": True, "text": cfg["empty"]}
            new = None
        else:
            new = {**base, "id": uuid.uuid4().hex[:10], "title": heading or cfg["title"], "text": body,
                   "quote": _clean(data.get(cfg["quote"])) if cfg.get("quote") else "",
                   "notes": notes, "question": question, "model": model, "key": found or body, "path": path}
            current = new
        replaced = None
        with self._lock:
            if new and request is None:
                # Texte en cours d'écriture : le moindre mot ajouté compte
                same = (lambda a, b: a.casefold().split() == b.casefold().split()) if cfg.get("in_place") else _same
                recent = [i for i in self.items[:RECENT_ITEMS] if i["mode"] == mode]
                again = next((i for i in recent if same(new["key"], i["key"])), None)
                if again:
                    # Déjà donné (même dernier message, même sujet) : il revient en direct
                    current, new = again, None
                elif cfg.get("in_place") and self.current and self.current.get("id") \
                        and self.current["mode"] == mode and self.current["window"] == title:
                    # Texte en cours d'écriture : la correction précédente est remplacée
                    replaced = self.current
                    new["id"] = replaced["id"]
                    self.items = [new if i is replaced else i for i in self.items]
            if new and not replaced:
                self.items.insert(0, new)
            dropped, self.items = self.items[MAX_ITEMS:], self.items[:MAX_ITEMS]
            self.current = current
            self._windows.pop(key, None)
            self._windows[key] = {"sig": signature, "lines": lines, "current": current}
            while len(self._windows) > MAX_WINDOWS:
                self._windows.pop(next(iter(self._windows)))
        if new is None and body:
            _delete(path)
        for old in dropped + ([replaced] if replaced else []):
            _delete(old["path"])
        if new:
            self._bridge.emit("live:item", {"item": _ui_item(new), "replace": bool(replaced)})
        self._set(note="" if new or not body else "Déjà proposé : rien de nouveau.", note_kind="")
        if new and request is None and not replaced and cfg.get("announce"):
            self._announce(new)

    def _ask_model(self, model: str, cfg: dict, image, title: str, app: str, text: str,
                   question: str = "", goal: str = "") -> dict:
        window = f"Fenêtre : « {title or 'sans titre'} »" + (f" (application : {app})" if app else "") + "."
        parts = [window, f'Texte affiché dans la fenêtre :\n"""\n{text}\n"""' if text
                 else "(Aucun texte lu dans la fenêtre.)",
                 "Tâche : " + cfg["task"].format(goal=goal, question=question)]
        message = {"role": "user", "content": "\n\n".join(parts)}
        if self._chat.sees_images(model):
            message["images"] = [_model_image(image)]
        if self._client is None:
            import ollama
            self._client = ollama.Client(timeout=CALL_TIMEOUT)
        schema = {"type": "object", "properties": cfg["props"], "required": list(cfg["props"])}
        resp = self._client.chat(model=model, format=schema, think=False,
                                 options={"temperature": 0.4, "num_ctx": num_ctx()},
                                 keep_alive=keep_alive(),
                                 messages=[{"role": "system", "content": SYSTEM}, message])
        if resp.done_reason == "length":
            raise RuntimeError("Réponse du modèle coupée : son contexte est trop petit.")
        data = json.loads(resp.message.content or "{}")
        return data if isinstance(data, dict) else {}

    def _announce(self, item: dict):
        """Nouveau résultat : lu à voix haute et/ou notifié (fenêtre masquée), selon les réglages."""
        text = strip_markdown(item["text"])
        title = f"Réponse à « {item['quote']} »" if item["quote"] else item["title"]
        if settings.get("live_speak", False):
            tts.speak(f"{item['title']}. {text}", source="live")
        if settings.get("live_notify", True) and self._notify and not self._is_visible():
            try:
                self._notify(_cut(title, 63), _cut(text, 255))
            except Exception as e:
                logger.log(f"Assistant live : notification impossible ({e})")

    # ── Résultats ─────────────────────────────
    def _find(self, item_id: str) -> dict | None:
        with self._lock:
            return next((i for i in self.items if i["id"] == item_id), None)

    def _forget(self, removed: list[dict]):
        """Résultats retirés : plus en direct, plus rappelés pour leur fenêtre."""
        ids = {i["id"] for i in removed}
        with self._lock:
            if self.current and self.current.get("id") in ids:
                self.current = None
            for seen in self._windows.values():
                if seen["current"] and seen["current"].get("id") in ids:
                    seen["current"] = None
        for item in removed:
            _delete(item["path"])
        self._emit_items()
        self.refresh()

    def delete(self, item_id: str):
        with self._lock:
            removed = [i for i in self.items if i["id"] == item_id]
            self.items = [i for i in self.items if i["id"] != item_id]
        self._forget(removed)

    def clear(self):
        with self._lock:
            removed, self.items = self.items, []
        self._forget(removed)

    def image(self, item_id: str):
        """Capture d'un résultat (PIL), ou None."""
        item = self._find(item_id)
        if not item:
            return None
        from PIL import Image
        try:
            with Image.open(item["path"]) as im:
                im.load()
                return im.copy()
        except Exception:
            return None

    def image_url(self, item_id: str) -> str | None:
        """Capture d'un résultat en pleine résolution (data URL), ou None."""
        item = self._find(item_id)
        try:
            with open(item["path"], "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except (OSError, TypeError):
            return None

    def discuss(self, item_id: str) -> dict:
        """Ouvre une conversation d'aide sur la capture d'un résultat (Conversations)."""
        item = self._find(item_id)
        image = self.image(item_id)
        if not item or image is None:
            return {"ok": False, "error": "Capture introuvable."}
        if not self._chat.available:
            return {"ok": False, "error": "Ollama ne répond pas."}
        self._chat.open_help(image, item["window"], item["app"])
        return {"ok": True}
