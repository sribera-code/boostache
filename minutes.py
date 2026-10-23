"""
minutes.py – Compte rendu d'un enregistrement (Enregistreur) : sa transcription mise
en forme par Ollama — en bref, décisions, actions (qui, quoi, pour quand), points
abordés, questions en suspens.

Le modèle remplit un schéma JSON (une liste d'actions à compléter : un petit modèle
en oublie beaucoup moins qu'en rédigeant librement), mis en forme ici en Markdown.
Une transcription trop longue pour le contexte du modèle est lue partie par partie
(notes de chaque partie, MINUTES_PARTS_MAX au plus, réparties), puis le compte rendu
est fait sur ces notes. Avancement par l'évènement minutes:state ; le compte rendu
se range à côté de l'enregistrement, <nom>.compte-rendu.md (renommé et supprimé
avec lui, recorder.py). Une demande à la fois.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

import llm
from chat import keep_alive, num_ctx
from engine import logger
from rag import ANSWER_RESERVE, CHARS_PER_TOKEN, MIN_BUDGET
from storage import settings

SUFFIX = ".compte-rendu.md"
MINUTES_PARTS_MAX = 12
LABEL = "Enregistreur : compte rendu"      # file d'attente d'Ollama (llm.py)

SYSTEM = (
    "Tu fais le compte rendu d'une réunion, d'un appel ou d'un enregistrement à partir de sa transcription "
    "automatique (des mots peuvent être mal reconnus, les personnes qui parlent ne sont pas indiquées). "
    "Réponds en français."
)
FINAL_INSTRUCTION = (
    "Fais le compte rendu en remplissant ces champs :\n"
    "- bref : 2 à 4 phrases, de quoi il s'agit et ce qui en ressort ;\n"
    "- decisions : ce qui a été décidé ou accepté, une phrase chacune ;\n"
    "- actions : TOUTES les choses que quelqu'un doit faire, une par élément. Repère chaque « X s'en charge », "
    "« X finit… pour lundi », « il faut… », « envoyez-moi… avant… », « passez prendre… ». qui : la personne "
    "chargée de l'action si la transcription la nomme, « vous » pour la personne à qui l'on parle, sinon \"\" ; "
    "quoi : verbe à l'infinitif et ce qu'il faut faire (« finir le devis ») ; quand : l'échéance telle qu'elle "
    "est dite (« lundi », « avant le 3 décembre »), sinon \"\". Attention : dans « il faut demander le tarif à "
    "Julie », Julie n'est pas la personne chargée de l'action ;\n"
    "- points : les sujets abordés, une phrase chacun, avec les chiffres et les dates cités ;\n"
    "- questions : ce qui reste à trancher ou à vérifier.\n"
    "N'invente rien : aucun nom, chiffre, date ou décision absent de la transcription ; corrige un mot "
    "manifestement mal reconnu d'après le contexte. Un champ sans rien à dire reste vide."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "bref": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "object", "properties": {
            "qui": {"type": "string"}, "quoi": {"type": "string"}, "quand": {"type": "string"}},
            "required": ["qui", "quoi", "quand"]}},
        "points": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["bref", "decisions", "actions", "points", "questions"],
}
PART_INSTRUCTION = (
    "Prends des notes sur cette partie, en liste, 12 lignes au plus : décisions, choses à faire (qui, quoi, "
    "pour quand), chiffres et dates cités, sujets abordés, questions laissées ouvertes. Rien d'autre, sans "
    "phrase d'introduction."
)


def minutes_path(path: Path) -> Path:
    """Compte rendu rangé à côté d'un enregistrement (ou d'un texte en direct)."""
    return path.with_name(path.stem + SUFFIX)


def _clean(value) -> str:
    return " ".join(str(value or "").split()).strip(" -•")


def _sentence(text: str) -> str:
    text = _clean(text)
    return text[:1].upper() + text[1:] if text else ""


def render(data: dict) -> str:
    """Compte rendu en Markdown, depuis les champs du schéma (sections vides omises)."""
    out = []
    bref = _clean(data.get("bref"))
    if bref:
        out += ["## En bref", "", bref]
    lists = [("Décisions", [_sentence(d) for d in data.get("decisions") or []])]
    actions = []
    for a in data.get("actions") or []:
        if not isinstance(a, dict) or not _clean(a.get("quoi")):
            continue
        who, what, when = _clean(a.get("qui")), _clean(a.get("quoi")), _clean(a.get("quand"))
        line = f"**{_sentence(who)}** : {what}" if who else _sentence(what)
        actions.append(f"{line} — {when}" if when else line)
    lists += [("Actions", actions), ("Points abordés", [_sentence(p) for p in data.get("points") or []]),
              ("Questions en suspens", [_sentence(q) for q in data.get("questions") or []])]
    for title, items in lists:
        items = [i for i in items if i]
        if items:
            out += ["", f"## {title}", "", *(f"- {i}" for i in items)]
    return "\n".join(out).strip()


def split_text(text: str, budget: int) -> list[str]:
    """Parties d'au plus budget caractères, coupées entre deux phrases (ou deux mots)."""
    parts = []
    text = text.strip()
    while len(text) > budget:
        cut = max(text.rfind(". ", 0, budget), text.rfind("\n", 0, budget))
        if cut < budget // 2:
            cut = text.rfind(" ", 0, budget)
        cut = cut + 1 if cut > 0 else budget
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


class MinutesService:
    def __init__(self, bridge, chat, recorder):
        self._bridge = bridge
        self._chat = chat
        self._recorder = recorder
        self._lock = threading.Lock()
        self._job: dict | None = None        # {name, stop, text, state, phase}

    def model(self) -> str:
        """Modèle choisi pour l'Enregistreur, sinon celui des documents, de l'assistant ou des conversations."""
        models = self._chat.models if self._chat else []
        wanted = (settings.get("recorder_model", ""), settings.get("rag_model", ""),
                  settings.get("assistant_model", ""), settings.get("last_model", ""))
        return next((m for m in wanted if m and m in models), models[0] if models else "")

    # ── Pour l'interface ──────────────────────
    def get(self, name: str) -> dict:
        """Compte rendu enregistré (ou en cours) d'un enregistrement."""
        with self._lock:
            job = dict(self._job) if self._job and self._job["name"] == name else None
        if job and job["state"] in ("reading", "writing"):
            return {"name": name, "state": job["state"], "phase": job["phase"], "text": job["text"],
                    "model": job["model"]}
        path = self._recorder._file(name)
        saved = minutes_path(path) if path else None
        if saved and saved.is_file():
            try:
                text = saved.read_text(encoding="utf-8")
                at = datetime.fromtimestamp(saved.stat().st_mtime).isoformat(timespec="minutes")
            except OSError as e:
                return {"name": name, "state": "error", "error": f"Compte rendu illisible ({e.strerror or e})."}
            body, _, meta = text.rpartition("\n<!-- ")
            model = meta.removesuffix(" -->\n").removesuffix(" -->") if body else ""
            return {"name": name, "state": "done", "text": (body or text).strip(), "model": model, "at": at}
        if job and job["state"] in ("error", "stopped"):
            return {"name": name, "state": job["state"], "error": job.get("error", ""), "text": job["text"]}
        return {"name": name, "state": "none"}

    def start(self, name: str) -> dict:
        chat = self._chat
        if chat is None or not chat.available:
            return {"ok": False, "error": "Ollama ne répond pas."}
        model = self.model()
        if not model:
            return {"ok": False, "error": chat.models_error or "Aucun modèle Ollama installé."}
        info = self._recorder.transcript(name)
        if not info or not info["text"].strip():
            return {"ok": False, "error": "Pas encore de texte pour cet enregistrement."}
        self.stop()
        job = {"name": name, "stop": threading.Event(), "text": "", "state": "reading", "phase": "", "model": model}
        with self._lock:
            self._job = job
        threading.Thread(target=self._run, args=(job, info["text"]), daemon=True, name="minutes").start()
        return {"ok": True, "model": model}

    def stop(self):
        with self._lock:
            job = self._job
        if job and job["state"] in ("reading", "writing"):
            job["stop"].set()

    # ── Rédaction ─────────────────────────────
    def _emit(self, job: dict, **extra):
        self._bridge.emit("minutes:state", {"name": job["name"], "state": job["state"], "phase": job["phase"],
                                            "model": job["model"], **extra})

    def _run(self, job: dict, text: str):
        name, model, stop = job["name"], job["model"], job["stop"]
        budget = max(MIN_BUDGET, int((num_ctx() - ANSWER_RESERVE) * CHARS_PER_TOKEN))
        try:
            parts = split_text(text, budget)
            if len(parts) == 1:
                source = f"# Transcription\n\n{parts[0]}"
            else:
                if len(parts) > MINUTES_PARTS_MAX:
                    step = len(parts) / MINUTES_PARTS_MAX
                    parts = [parts[int(i * step)] for i in range(MINUTES_PARTS_MAX)]
                per_part = max(500, budget // len(parts))
                notes = []
                for i, part in enumerate(parts, 1):
                    job["phase"] = f"Lecture de la partie {i}/{len(parts)}…"
                    self._emit(job)
                    note = self._generate(model, [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": f"# Partie {i}/{len(parts)} de la transcription\n\n{part}\n\n"
                                                    f"# Demande\n\n{PART_INSTRUCTION}"}], stop)
                    if stop.is_set():
                        raise _Stopped()
                    notes.append(f"## Partie {i}\n\n{note.strip()[:per_part]}")
                source = (f"# Notes prises sur les {len(parts)} parties de la transcription, dans l'ordre\n\n"
                          + "\n\n".join(notes))
            job["state"], job["phase"] = "writing", f"Rédaction avec {model}…"
            self._emit(job)
            resp = llm.chat(LABEL, model=model, format=SCHEMA, think=False, keep_alive=keep_alive(),
                            options={"temperature": 0.2, "num_ctx": num_ctx()},
                            messages=[{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": f"{source}\n\n# Demande\n\n{FINAL_INSTRUCTION}"}])
            if stop.is_set():
                raise _Stopped()
            if resp.done_reason == "length":
                raise RuntimeError("Réponse du modèle coupée : son contexte est trop petit (section Ollama).")
            data = json.loads(resp.message.content or "{}")
            job["text"] = render(data if isinstance(data, dict) else {})
            if not job["text"]:
                raise RuntimeError("Le modèle n'a rien rédigé.")
            self._save(name, job["text"].strip(), model)
            job["state"], job["phase"] = "done", ""
            self._emit(job, text=job["text"].strip(), at=datetime.now().isoformat(timespec="minutes"))
        except _Stopped:
            job["state"], job["phase"] = "stopped", ""
            self._emit(job)
        except Exception as e:
            error = self._chat.failure(e) if self._chat else str(e)
            logger.log(f"Enregistreur : compte rendu de {name} ({model}) en échec : {error}")
            job["state"], job["phase"], job["error"] = "error", "", error
            self._emit(job, error=error)

    def _generate(self, model: str, messages: list[dict], stop: threading.Event) -> str:
        text, stream = "", None
        try:
            stream = llm.chat(LABEL, model=model, messages=messages, stream=True, think=False,
                              options={"temperature": 0.2, "num_ctx": num_ctx()}, keep_alive=keep_alive())
            for chunk in stream:
                if stop.is_set():
                    break
                text += chunk.message.content or ""
        finally:
            _close(stream)
        return text

    def _save(self, name: str, text: str, model: str):
        path = self._recorder._file(name)
        if not path:
            return
        try:
            # Le modèle en commentaire de fin (invisible une fois le Markdown affiché)
            minutes_path(path).write_text(f"{text}\n\n<!-- {model} -->\n", encoding="utf-8")
        except OSError as e:
            logger.log(f"Enregistreur : compte rendu de {name} non enregistré ({e.strerror or e})")


class _Stopped(Exception):
    pass


def _close(stream):
    if stream is not None and hasattr(stream, "close"):
        try:
            stream.close()
        except Exception:
            pass
