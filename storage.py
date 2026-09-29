"""
storage.py – Persistance de Boostache
Réglages, conversations, consoles, notes, captures et historique du presse-papiers.

Le répertoire de données peut être redirigé avec la variable d'environnement
BOOSTACHE_DATA_DIR (utile pour tester sans toucher aux données réelles).
"""

import json
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path


def _default_data_dir() -> Path:
    override = os.environ.get("BOOSTACHE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    try:
        from platformdirs import user_data_dir
        return Path(user_data_dir("Boostache", "Boostache"))
    except ImportError:
        # Fallback : dossier AppData\Roaming\Boostache
        return (Path(os.environ.get("APPDATA", "~")) / "Boostache").expanduser()


DATA_DIR          = _default_data_dir()
SETTINGS_FILE     = DATA_DIR / "settings.json"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
ATTACHMENTS_DIR   = CONVERSATIONS_DIR / "attachments"
CONSOLES_DIR      = DATA_DIR / "consoles"
NOTES_DIR         = DATA_DIR / "notes"
CAPTURES_DIR      = DATA_DIR / "captures"
CACHE_DIR         = DATA_DIR / "cache"


def _ensure_dirs():
    for d in (DATA_DIR, CONVERSATIONS_DIR, CONSOLES_DIR, NOTES_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def read_json(path: Path):
    """Lit un fichier JSON, ou None s'il est absent ou illisible."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path: Path, data) -> bool:
    """Écrit un fichier JSON de façon atomique (fichier temporaire + remplacement),
    pour ne jamais laisser un fichier à moitié écrit en cas d'arrêt brutal."""
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        return False


# ─────────────────────────────────────────────
#  Nettoyage du cache au démarrage
# ─────────────────────────────────────────────
def clear_cache():
    """Supprime les fichiers temporaires (screenshots, images collées, captures
    jointes à une conversation)."""
    _ensure_dirs()
    for f in CACHE_DIR.glob("boostache_*.png"):
        try:
            f.unlink()
        except Exception:
            pass
    for d in CACHE_DIR.glob("boostache_*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


# ─────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────
_DEFAULTS = {
    "last_model":           "",
    "window_size":          [980, 680],
    "always_on_top":        True,
    "system_prompt":        "",
    "tts_mode_chat":        "last",        # "last" | "all"
    "tts_mode_console":     "last",        # "last" | "all"
    "tts_mode_note":        "all",         # "all" | "sel"
    "clipboard_max_items":  100,
    "console_shell":        "powershell",  # "powershell" | "pwsh" | "cmd"
    "sidebar_collapsed":    False,
    "print_screen_capture": True,          # Impr. écran → onglet Captures
    "capture_save_dir":     "",            # dernier dossier de « Enregistrer sous »
}


class SettingsManager:
    def __init__(self):
        _ensure_dirs()
        self._data: dict = {}
        self._lock = threading.Lock()
        self.load()

    def load(self):
        data = read_json(SETTINGS_FILE)
        self._data = data if isinstance(data, dict) else {}

    def save(self):
        with self._lock:
            merged = {**_DEFAULTS, **self._data}
        write_json(SETTINGS_FILE, merged)

    def get(self, key: str, default=None):
        return self._data.get(key, _DEFAULTS.get(key, default))

    def set(self, key: str, value):
        with self._lock:
            self._data[key] = value
        self.save()


# ─────────────────────────────────────────────
#  Conversations
# ─────────────────────────────────────────────
class ConversationManager:
    """
    Sauvegarde / charge les conversations Ollama.
    Chaque conversation = un fichier JSON dans conversations/.
    Format : {"id": "...", "title": "...", "model": "...",
               "created_at": "...", "messages": [...]}
    Les messages suivent le format Ollama (role / content / images) avec
    quelques champs d'affichage en plus (display, attachments, thinking, model).
    """

    def __init__(self):
        _ensure_dirs()
        self._lock = threading.Lock()

    def new(self, model: str) -> str:
        now = datetime.now()
        conv_id = now.strftime("%Y%m%d_%H%M%S")
        # Deux conversations créées dans la même seconde : suffixe
        n = 1
        while self._path(conv_id).exists():
            n += 1
            conv_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{n}"
        self._write(conv_id, {
            "id":         conv_id,
            "title":      f"Conversation du {now.strftime('%d/%m/%Y %H:%M')}",
            "model":      model,
            "created_at": now.isoformat(),
            "messages":   [],
        })
        return conv_id

    def save(self, messages: list[dict], model: str, conv_id: str | None = None) -> str:
        with self._lock:
            cid = conv_id or self.new(model)
            data = self._read(cid) or {"id": cid, "created_at": datetime.now().isoformat()}
            data["messages"] = messages
            data["model"]    = model
            # Titre auto : première ligne du premier message utilisateur
            if not data.get("title") or data["title"].startswith("Conversation"):
                for m in messages:
                    if m.get("role") == "user":
                        raw = m.get("display") or m.get("content", "")
                        first_line = raw.strip().split("\n")[0][:60]
                        if first_line:
                            data["title"] = first_line
                        break
            self._write(cid, data)
            return cid

    def load(self, conv_id: str) -> dict | None:
        return self._read(conv_id)

    def load_latest(self) -> dict | None:
        """Charge la conversation la plus récente."""
        for f in sorted(CONVERSATIONS_DIR.glob("*.json"), reverse=True):
            if f.name == "chat_meta.json":
                continue
            data = read_json(f)
            if isinstance(data, dict) and "messages" in data:
                return data
        return None

    def _path(self, conv_id: str) -> Path:
        return CONVERSATIONS_DIR / f"{conv_id}.json"

    def _read(self, conv_id: str) -> dict | None:
        data = read_json(self._path(conv_id))
        return data if isinstance(data, dict) else None

    def _write(self, conv_id: str, data: dict):
        write_json(self._path(conv_id), data)


class ChatMeta:
    """Onglets de conversation ouverts : conversations/chat_meta.json
    Format : {"tabs": [{"conv_id": "...", "label": "...", "model": "..."}]}"""

    PATH = CONVERSATIONS_DIR / "chat_meta.json"

    def load(self) -> list[dict]:
        data = read_json(self.PATH)
        tabs = data.get("tabs", []) if isinstance(data, dict) else []
        return [t for t in tabs if isinstance(t, dict)]

    def save(self, tabs: list[dict]):
        write_json(self.PATH, {"tabs": tabs})


# ─────────────────────────────────────────────
#  Consoles, notes & captures : un fichier JSON par onglet
# ─────────────────────────────────────────────
class SlotStore:
    """Onglets persistés sous forme de fichiers <prefix>_<slot>.json,
    ordonnés par un meta.json : {"order": [slots…]}."""

    def __init__(self, directory: Path, prefix: str):
        self.dir = directory
        self.prefix = prefix
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, slot: int) -> Path:
        return self.dir / f"{self.prefix}_{slot}.json"

    def load(self, slot: int) -> dict | None:
        data = read_json(self.path(slot))
        return data if isinstance(data, dict) else None

    def save(self, slot: int, data: dict):
        write_json(self.path(slot), data)

    def delete(self, slot: int):
        try:
            self.path(slot).unlink(missing_ok=True)
        except Exception:
            pass

    def existing_slots(self) -> list[int]:
        slots = []
        for p in self.dir.glob(f"{self.prefix}_*.json"):
            tail = p.stem[len(self.prefix) + 1:]
            if tail.isdigit():
                slots.append(int(tail))
        return sorted(slots)

    def order(self) -> list[int]:
        """Slots à restaurer, dans l'ordre. Si le meta est vide ou absent,
        retombe sur les fichiers présents dans le dossier."""
        data = read_json(self.dir / "meta.json")
        order = data.get("order", []) if isinstance(data, dict) else []
        order = [s for s in order if isinstance(s, int) and self.path(s).exists()]
        return order or self.existing_slots()

    def save_order(self, slots: list[int]):
        write_json(self.dir / "meta.json", {"order": slots})


# ─────────────────────────────────────────────
#  Historique du presse-papiers
# ─────────────────────────────────────────────
CLIPBOARD_FILE = DATA_DIR / "clipboard_history.json"


class ClipboardManager:
    """Historique des copies texte — strictement en mémoire.
    Aucune persistance sur disque : le contenu disparaît à la fermeture.
    Les items sont triés du plus récent au plus ancien ; chacun a un id stable."""

    def __init__(self):
        _ensure_dirs()
        self._items: list[dict] = []   # [{"id": n, "ts": iso, "text": "..."}]
        self._next_id = 1
        self._lock = threading.Lock()
        # Nettoyage d'un éventuel fichier résiduel d'anciennes versions
        try:
            CLIPBOARD_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def add(self, text: str, max_items: int = 100) -> bool:
        """Ajoute une entrée (ou remonte une entrée identique en tête).
        Retourne True si l'historique a changé."""
        if not text or not text.strip():
            return False
        with self._lock:
            if self._items and self._items[0].get("text") == text:
                return False
            self._items = [it for it in self._items if it.get("text") != text]
            self._items.insert(0, {
                "id":   self._next_id,
                "ts":   datetime.now().isoformat(timespec="seconds"),
                "text": text,
            })
            self._next_id += 1
            if max_items and len(self._items) > max_items:
                self._items = self._items[:max_items]
        return True

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def get(self, item_id: int) -> dict | None:
        with self._lock:
            return next((it for it in self._items if it["id"] == item_id), None)

    def delete(self, item_id: int):
        with self._lock:
            self._items = [it for it in self._items if it["id"] != item_id]

    def clear(self):
        with self._lock:
            self._items.clear()

    def trim(self, max_items: int):
        with self._lock:
            if max_items and len(self._items) > max_items:
                self._items = self._items[:max_items]


# Singletons
settings          = SettingsManager()
conversations     = ConversationManager()
chat_meta         = ChatMeta()
console_store     = SlotStore(CONSOLES_DIR, "console")
note_store        = SlotStore(NOTES_DIR, "note")
capture_store     = SlotStore(CAPTURES_DIR, "capture")
clipboard_history = ClipboardManager()
