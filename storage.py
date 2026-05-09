"""
storage.py – Persistance de Boostache
Settings (modèle choisi, préférences) et conversations.
"""

import json
import os
from datetime import datetime
from pathlib import Path

try:
    from platformdirs import user_data_dir
    DATA_DIR = Path(user_data_dir("Boostache", "Boostache"))
except ImportError:
    # Fallback : dossier AppData\Roaming\Boostache
    DATA_DIR = Path(os.environ.get("APPDATA", "~")) / "Boostache"
    DATA_DIR = DATA_DIR.expanduser()

SETTINGS_FILE      = DATA_DIR / "settings.json"
CONVERSATIONS_DIR  = DATA_DIR / "conversations"
CACHE_DIR          = DATA_DIR / "cache"


def _ensure_dirs():
    for d in (DATA_DIR, CONVERSATIONS_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
#  Nettoyage du cache au démarrage
# ─────────────────────────────────────────────
def clear_cache():
    """Supprime les fichiers temporaires (screenshots, images collées)."""
    _ensure_dirs()
    for f in CACHE_DIR.glob("boostache_*.png"):
        try:
            f.unlink()
        except Exception:
            pass


# ─────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────
_DEFAULTS = {
    "last_model":           "",
    "window_geometry":      "720x580",
    "always_on_top":        True,
}


class SettingsManager:
    def __init__(self):
        _ensure_dirs()
        self._data: dict = {}
        self.load()

    def load(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}

    def save(self):
        try:
            merged = {**_DEFAULTS, **self._data}
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, _DEFAULTS.get(key, default))

    def set(self, key: str, value):
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
    """

    def __init__(self):
        _ensure_dirs()
        self._current_id: str | None = None

    # ── Nouveau fichier ──────────────────────
    def new(self, model: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        conv_id = ts
        self._current_id = conv_id
        data = {
            "id":         conv_id,
            "title":      f"Conversation du {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            "model":      model,
            "created_at": datetime.now().isoformat(),
            "messages":   [],
        }
        self._write(conv_id, data)
        return conv_id

    # ── Sauvegarde ──────────────────────────
    def save(self, messages: list[dict], model: str,
             conv_id: str | None = None):
        cid = conv_id or self._current_id
        if not cid:
            cid = self.new(model)
        data = self._read(cid) or {}
        data["messages"] = messages
        data["model"]    = model
        # Titre auto : premiers 60 chars du premier message utilisateur
        if not data.get("title") or data["title"].startswith("Conversation"):
            for m in messages:
                if m.get("role") == "user":
                    raw = m.get("content", "")
                    # Tronquer si le contenu est long (ex: fichier injecté)
                    first_line = raw.split("\n")[0][:60]
                    if first_line:
                        data["title"] = first_line
                    break
        self._write(cid, data)
        self._current_id = cid
        return cid

    # ── Chargement ──────────────────────────
    def load(self, conv_id: str) -> dict | None:
        data = self._read(conv_id)
        if data:
            self._current_id = conv_id
        return data

    def load_latest(self) -> dict | None:
        """Charge la conversation la plus récente."""
        files = sorted(CONVERSATIONS_DIR.glob("*.json"), reverse=True)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                self._current_id = data.get("id")
                return data
            except Exception:
                continue
        return None

    def list_all(self) -> list[dict]:
        """Retourne toutes les conversations triées par date décroissante."""
        result = []
        for f in sorted(CONVERSATIONS_DIR.glob("*.json"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                result.append({
                    "id":         data.get("id", f.stem),
                    "title":      data.get("title", f.stem),
                    "model":      data.get("model", ""),
                    "created_at": data.get("created_at", ""),
                    "count":      len(data.get("messages", [])),
                })
            except Exception:
                continue
        return result

    @property
    def current_id(self) -> str | None:
        return self._current_id

    # ── I/O ─────────────────────────────────
    def _path(self, conv_id: str) -> Path:
        return CONVERSATIONS_DIR / f"{conv_id}.json"

    def _read(self, conv_id: str) -> dict | None:
        p = self._path(conv_id)
        if not p.exists():
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write(self, conv_id: str, data: dict):
        try:
            with open(self._path(conv_id), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# Singletons
settings      = SettingsManager()
conversations = ConversationManager()


# ─────────────────────────────────────────────
#  Consoles
# ─────────────────────────────────────────────
CONSOLES_DIR = DATA_DIR / "consoles"


class ConsoleManager:
    """
    Sauvegarde / charge l'état des consoles.
    Format : {"slot": N, "cwd": "...", "history": [...], "output": "..."}
    Un fichier par slot (console_1.json, console_2.json, …).
    """

    def __init__(self):
        _ensure_dirs()
        CONSOLES_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, slot: int, cwd: str, history: list[str], output: str):
        path = CONSOLES_DIR / f"console_{slot}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "slot":    slot,
                    "cwd":     cwd,
                    "history": history,
                    "output":  output,
                }, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def load(self, slot: int) -> dict | None:
        path = CONSOLES_DIR / f"console_{slot}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def list_slots(self) -> list[int]:
        """Retourne les numéros de slot persistés, triés."""
        slots = []
        for p in CONSOLES_DIR.glob("console_*.json"):
            try:
                n = int(p.stem.split("_")[1])
                slots.append(n)
            except Exception:
                pass
        return sorted(slots)

    def delete(self, slot: int):
        path = CONSOLES_DIR / f"console_{slot}.json"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


consoles = ConsoleManager()
