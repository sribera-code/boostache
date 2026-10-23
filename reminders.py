"""
reminders.py – Rappels : un texte à une date et une heure, dit à voix haute (comme
une annonce de l'Assistant : le son des autres applications baisse) et en
notification Windows.

Créés depuis un message arrivé (« Me le rappeler » : l'échéance repérée dans son
résumé propose la veille et le jour même), la recherche (Nouveau rappel) ou la
section Tâches. Gardés dans DATA_DIR/reminders.json ; un rappel passé pendant que
Boostache était fermé est donné au lancement, « en retard ». Les rappels donnés
restent affichés RECENT_DAYS jours (pour les remettre à plus tard).
"""

import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from assistant import DAYS, MONTHS, duck_level
from engine import logger, tts
from storage import DATA_DIR, read_json, write_json

FILE = DATA_DIR / "reminders.json"
RECENT_DAYS = 7                # rappels donnés gardés dans la liste
CHECK = 30.0                   # s au plus entre deux vérifications
SPEAK_WAIT = 60.0              # s d'attente au plus de la fin d'une lecture en cours
MAX_TEXT = 300
MAX_DETAILS = 1200
MORNING = 9                    # heure des rappels « demain », « la veille »…

_MONTHS_RE = "|".join(MONTHS + ("fevrier", "aout", "decembre"))
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:er)?\s+({_MONTHS_RE})(?:\s+(\d{{4}}))?\b", re.I)
_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b")
_PLAIN = str.maketrans("éû", "eu")


def deadline(text: str, today: date | None = None) -> date | None:
    """Première date du texte (« avant le 25 octobre », « le 3/11 », « demain »), à venir :
    sans année, la prochaine (le 5 janvier lu en décembre : l'an prochain)."""
    today = today or date.today()
    found = []
    for m in _DAY_MONTH.finditer(text or ""):
        month = m.group(2).lower().translate(_PLAIN)
        names = [n.translate(_PLAIN) for n in MONTHS]
        found.append((m.start(), int(m.group(1)), names.index(month) + 1, m.group(3)))
    for m in _NUMERIC.finditer(text or ""):
        year = m.group(3)
        found.append((m.start(), int(m.group(1)), int(m.group(2)), ("20" + year) if year and len(year) == 2 else year))
    if re.search(r"\bdemain\b", text or "", re.I):
        found.append((re.search(r"\bdemain\b", text, re.I).start(), *_dmy(today + timedelta(days=1))))
    for _, day, month, year in sorted(found):
        try:
            d = date(int(year) if year else today.year, month, day)
        except ValueError:
            continue
        if not year and d < today - timedelta(days=1):
            try:
                d = d.replace(year=d.year + 1)
            except ValueError:
                continue
        if d >= today:
            return d
    return None


def _dmy(d: date):
    return d.day, d.month, str(d.year)


def day_label(dt: datetime, today: date | None = None) -> str:
    """« aujourd'hui », « demain », « jeudi 24 octobre » (l'année si ce n'est pas celle-ci)."""
    today = today or date.today()
    d = dt.date()
    if d == today:
        return "aujourd'hui"
    if d == today + timedelta(days=1):
        return "demain"
    text = f"{DAYS[d.weekday()]} {d.day}{'er' if d.day == 1 else ''} {MONTHS[d.month - 1]}"
    return text if d.year == today.year else f"{text} {d.year}"


def hour_label(dt: datetime) -> str:
    return f"{dt.hour} h" if not dt.minute else f"{dt.hour} h {dt.minute:02d}"


def when_label(dt: datetime, today: date | None = None) -> str:
    return f"{day_label(dt, today)} à {hour_label(dt)}"


def suggestions(text: str = "", now: datetime | None = None) -> list[dict]:
    """Moments proposés pour un rappel : dans une heure, ce soir, demain matin, et la veille
    et le jour d'une échéance trouvée dans le texte."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    out: list[dict] = []

    def add(label: str, at: datetime):
        stamp = at.isoformat(timespec="minutes")
        if at > now and all(o["at"] != stamp for o in out):
            out.append({"label": label, "at": stamp})

    add("Dans 1 heure", now + timedelta(hours=1))
    evening = now.replace(hour=18, minute=0)
    if now <= evening - timedelta(minutes=30):
        add("Ce soir à 18 h", evening)
    tomorrow = (now + timedelta(days=1)).replace(hour=MORNING, minute=0)
    add(f"Demain matin ({DAYS[tomorrow.weekday()]}, {MORNING} h)", tomorrow)
    due = deadline(text, now.date())
    if due:
        eve = datetime.combine(due - timedelta(days=1), datetime.min.time()).replace(hour=MORNING)
        day = datetime.combine(due, datetime.min.time()).replace(hour=MORNING)
        if eve > now + timedelta(hours=1):
            add(f"La veille de l'échéance ({day_label(eve, now.date())}, {MORNING} h)", eve)
        add(f"Le jour de l'échéance ({day_label(day, now.date())}, {MORNING} h)", day)
    if now.weekday() >= 4:          # vendredi à dimanche : lundi matin
        monday = (now + timedelta(days=7 - now.weekday())).replace(hour=MORNING, minute=0)
        add(f"Lundi matin ({MORNING} h)", monday)
    return out


def _parse(at) -> datetime | None:
    try:
        return datetime.fromisoformat(str(at)).replace(second=0, microsecond=0, tzinfo=None)
    except (TypeError, ValueError):
        return None


class ReminderService:
    def __init__(self, bridge):
        self._bridge = bridge
        self.notifier = None            # fn(titre, texte) : notification Windows (tray)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        data = read_json(FILE)
        items = data.get("items", []) if isinstance(data, dict) else []
        limit = datetime.now() - timedelta(days=RECENT_DAYS)
        self.items = [r for r in items if isinstance(r, dict) and _parse(r.get("at"))
                      and not (r.get("done") and (_parse(r.get("done")) or limit) < limit)]

    # ── Cycle de vie ──────────────────────────
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="reminders")
            self._thread.start()

    def shutdown(self):
        self._stop.set()
        self._wake.set()

    # ── Pour l'interface ──────────────────────
    def snapshot(self) -> list[dict]:
        """À venir (le plus proche d'abord), puis donnés (le plus récent d'abord)."""
        with self._lock:
            items = [dict(r) for r in self.items]
        pending = sorted((r for r in items if not r.get("done")), key=lambda r: r["at"])
        done = sorted((r for r in items if r.get("done")), key=lambda r: r["done"], reverse=True)
        for r in pending + done:
            r["when"] = when_label(_parse(r["at"]))
        return pending + done

    def add(self, text: str, at, details: str = "", source: dict | None = None) -> dict:
        text = " ".join(str(text or "").split())[:MAX_TEXT]
        when = _parse(at)
        if not text:
            return {"ok": False, "error": "Le rappel n'a pas de texte."}
        if when is None:
            return {"ok": False, "error": "Date ou heure invalide."}
        if when <= datetime.now() - timedelta(minutes=1):
            return {"ok": False, "error": "Ce moment est déjà passé."}
        item = {"id": uuid.uuid4().hex[:10], "text": text, "details": str(details or "").strip()[:MAX_DETAILS],
                "at": when.isoformat(timespec="minutes"), "created": datetime.now().isoformat(timespec="seconds"),
                "source": source if isinstance(source, dict) else None, "done": ""}
        with self._lock:
            self.items.append(item)
        self._changed()
        logger.info(f"Rappel ajouté pour {when_label(when)} : {text}")
        return {"ok": True, "reminder": {**item, "when": when_label(when)}}

    def delete(self, rid: str) -> bool:
        with self._lock:
            before = len(self.items)
            self.items = [r for r in self.items if r["id"] != rid]
            changed = len(self.items) != before
        if changed:
            self._changed()
        return changed

    def snooze(self, rid: str, at) -> dict:
        """Remet un rappel (donné ou non) à un autre moment."""
        when = _parse(at)
        if when is None or when <= datetime.now():
            return {"ok": False, "error": "Choisissez un moment à venir."}
        with self._lock:
            r = next((r for r in self.items if r["id"] == rid), None)
            if r is None:
                return {"ok": False, "error": "Ce rappel n'existe plus."}
            r.update(at=when.isoformat(timespec="minutes"), done="", late=False)
        self._changed()
        return {"ok": True, "when": when_label(when)}

    def _changed(self):
        with self._lock:
            data = {"items": list(self.items)}
        write_json(FILE, data)
        self._bridge.emit("reminders", {"items": self.snapshot()})
        self._wake.set()

    # ── Échéances ─────────────────────────────
    def _loop(self):
        while not self._stop.is_set():
            now = datetime.now()
            with self._lock:
                due = [r for r in self.items if not r.get("done") and _parse(r["at"]) <= now]
                for r in due:
                    r["done"] = now.isoformat(timespec="seconds")
                    r["late"] = (now - _parse(r["at"])) > timedelta(minutes=5)
                upcoming = [_parse(r["at"]) for r in self.items if not r.get("done")]
            if due:
                self._changed()
                for r in due:
                    self._fire(r)
            wait = CHECK
            if upcoming:
                wait = max(0.5, min(CHECK, (min(upcoming) - datetime.now()).total_seconds()))
            self._wake.wait(wait)
            self._wake.clear()

    def _fire(self, r: dict):
        planned = _parse(r["at"])
        head = f"Rappel en retard, prévu {when_label(planned)}" if r.get("late") else "Rappel"
        logger.info(f"{head} : {r['text']}")
        if self.notifier:
            try:
                self.notifier(head, r["text"])
            except Exception as e:
                logger.log(f"Rappel : notification impossible ({e})")
        self._bridge.emit("reminders:fired", {"reminder": {**r, "when": when_label(planned)}, "title": head})
        threading.Thread(target=self._speak, args=(f"{head}. {r['text']}. {r.get('details', '')}".strip(),),
                         daemon=True, name="reminder-voice").start()

    def _speak(self, text: str):
        waited = 0.0
        while tts.speaking and waited < SPEAK_WAIT and not self._stop.is_set():
            time.sleep(0.5)
            waited += 0.5
        tts.speak(text, source="reminder", duck=duck_level())
