"""
custom_tasks.py – Tâches planifiées créées depuis l'interface (onglet Tâches).

Elles s'ajoutent à celles de tasks.py dans task_manager.registered et sont
persistées dans les réglages (clé "custom_tasks").
"""

import re
import threading

import schedule

from engine import logger, task_manager, hotkey_manager
from storage import settings

UNITS = {"secondes": "seconds", "minutes": "minutes", "heures": "hours"}


def _make_job_fn(label: str, code: str):
    def fn():
        try:
            exec(code, {"logger": logger,   # noqa: S102
                        "__builtins__": __builtins__})
        except Exception as exc:
            logger.log(f"⚠ Erreur tâche '{label}' : {exc}")
    return fn


def _period_str(entry: dict) -> str:
    if entry.get("period_str"):
        return entry["period_str"]
    job = entry["job"]
    try:
        if job.at_time:
            return f"Chaque jour à {job.at_time.strftime('%H:%M')}"
        unit = {"seconds": "s", "minutes": "min", "hours": "h", "days": "j",
                "weeks": "sem."}.get(job.unit, job.unit)
        return f"Toutes les {job.interval} {unit}"
    except Exception:
        return "—"


def rows() -> list[dict]:
    """Tâches affichées dans l'interface (tasks.py + tâches perso)."""
    out = []
    for i, entry in enumerate(task_manager.registered):
        job = entry["job"]
        nxt = job.next_run.strftime("%d/%m %H:%M:%S") if job.next_run else "—"
        out.append({
            "idx":            i,
            "label":          entry["label"],
            "period":         _period_str(entry),
            "next_run":       nxt,
            "custom":         bool(entry.get("custom")),
            "sched_type":     entry.get("sched_type", "interval"),
            "interval_value": entry.get("interval_value", 30),
            "interval_unit":  entry.get("interval_unit", "minutes"),
            "at_time":        entry.get("at_time", "09:00"),
            "action_code":    entry.get("action_code", ""),
        })
    return out


def _validate(data: dict) -> dict:
    label = (data.get("label") or "").strip() or "Tâche"
    sched_type = "fixed" if data.get("sched_type") == "fixed" else "interval"
    unit = data.get("interval_unit") if data.get("interval_unit") in UNITS else "minutes"
    try:
        value = int(data.get("interval_value") or 1)
    except (TypeError, ValueError):
        raise ValueError("L'intervalle doit être un nombre entier.")
    if value < 1:
        raise ValueError("L'intervalle doit être supérieur à 0.")
    at_time = (data.get("at_time") or "09:00").strip()
    if sched_type == "fixed" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at_time):
        raise ValueError("L'heure doit être au format HH:MM (ex. 09:30).")
    code = (data.get("action_code") or "").strip()
    try:
        compile(code or "pass", f"<tâche {label}>", "exec")
    except SyntaxError as e:
        raise ValueError(f"Code Python invalide (ligne {e.lineno}) : {e.msg}")
    return {"label": label, "sched_type": sched_type, "interval_value": value,
            "interval_unit": unit, "at_time": at_time, "action_code": code}


def _register(t: dict, insert_at: int | None = None):
    fn = _make_job_fn(t["label"], t["action_code"])
    if t["sched_type"] == "interval":
        v = t["interval_value"]
        job = getattr(schedule.every(v), UNITS[t["interval_unit"]]).do(fn)
        short = {"secondes": "s", "minutes": "min", "heures": "h"}[t["interval_unit"]]
        period = f"Toutes les {v} {short}"
    else:
        job = schedule.every().day.at(t["at_time"]).do(fn)
        period = f"Chaque jour à {t['at_time']}"
    entry = {**t, "job": job, "custom": True, "period_str": period}
    if insert_at is None:
        task_manager.registered.append(entry)
    else:
        task_manager.registered.insert(insert_at, entry)
    return entry


def _persist():
    keys = ("label", "sched_type", "interval_value", "interval_unit", "at_time", "action_code")
    settings.set("custom_tasks", [
        {k: e.get(k) for k in keys}
        for e in task_manager.registered if e.get("custom")
    ])


def load_persisted():
    for raw in settings.get("custom_tasks", []) or []:
        try:
            _register(_validate(raw))
        except Exception as e:
            logger.log(f"Tâche perso ignorée ({raw.get('label', '?')}) : {e}")


def save(data: dict, edit_idx: int | None = None, insert_after: int | None = None):
    """Crée ou modifie une tâche perso. Lève ValueError si la saisie est invalide."""
    t = _validate(data)
    if edit_idx is not None:
        old = task_manager.registered[edit_idx]
        if not old.get("custom"):
            raise ValueError("Seules les tâches créées depuis l'interface sont modifiables.")
        schedule.cancel_job(old["job"])
        task_manager.registered.pop(edit_idx)
        _register(t, insert_at=edit_idx)
        logger.log(f"Tâche modifiée : {t['label']}")
    else:
        pos = insert_after + 1 if insert_after is not None else None
        entry = _register(t, insert_at=pos)
        logger.log(f"Tâche custom ajoutée : {t['label']} ({entry['period_str']})")
    _persist()


def delete(idx: int):
    entry = task_manager.registered[idx]
    if not entry.get("custom"):
        raise ValueError("Les tâches de tasks.py se suppriment dans le code.")
    schedule.cancel_job(entry["job"])
    task_manager.registered.pop(idx)
    _persist()
    logger.log(f"Tâche supprimée : {entry['label']}")


def run_now(idx: int):
    entry = task_manager.registered[idx]
    threading.Thread(target=entry["job"].job_func, daemon=True).start()
    logger.log(f"Tâche exécutée manuellement : {entry['label']}")


# ─────────────────────────────────────────────
#  Raccourcis (bindings.py)
# ─────────────────────────────────────────────
def hotkey_rows() -> list[dict]:
    return [{"idx": i, "combo": e["combo"], "label": e["label"]}
            for i, e in enumerate(hotkey_manager.registered)]


def run_hotkey(idx: int):
    entry = hotkey_manager.registered[idx]
    cb = entry.get("callback")
    if cb:
        threading.Thread(target=cb, daemon=True).start()
    logger.log(f"Raccourci déclenché manuellement : {entry['combo']}")
