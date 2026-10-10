"""
ollama_admin.py – Section Ollama : le serveur, les modèles installés, leur
téléchargement et les options des appels de Boostache.

Serveur : version, adresse, dossier des modèles (place prise et libre),
processeur graphique vu par Ollama (lu dans son journal) ; lancement de
l'application Ollama (ou de « ollama serve ») quand il ne répond pas, à la
demande ou au démarrage de Boostache (option).

Modèles : détails lus par /api/show (paramètres, quantification, contexte
maximal, capacités), modèles chargés en mémoire (/api/ps) et déchargement,
suppression, mises à jour : l'empreinte du manifeste installé est comparée à
celle du manifeste publié sur le registre (la même tant que le modèle n'a pas
été republié).

Téléchargements : « pull » en arrière-plan, progression additionnée sur les
couches du modèle, annulable (la connexion fermée, Ollama arrête). La place
nécessaire est comparée à celle du disque avant de commencer.

Bibliothèque : liste des modèles et variantes d'un modèle lues sur ollama.com
(pages HTML : le site n'a pas d'API) ; la recherche se fait dans l'interface.
En cas d'échec, elle renvoie au site.
"""

import ctypes
import hashlib
import html
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from chat import DEFAULT_CTX, _ollama, _unreachable, internal_model
from engine import logger
from storage import settings

SITE          = "https://ollama.com"
DOWNLOAD_PAGE = "https://ollama.com/download/windows"
REGISTRY      = "registry.ollama.ai"
CALL_TIMEOUT  = 20             # appels au serveur local : show, delete, ps… (s)
WEB_TIMEOUT   = 15             # ollama.com et registre (s)
START_WAIT    = 40             # Ollama lancé : attente de sa réponse (s)
AUTOSTART_GRACE = 15           # démarrage de Boostache : Ollama laissé démarrer seul pendant… (s)
GPU_RESERVE   = 480_000_000    # mémoire de la carte graphique qu'Ollama garde libre (octets, ~457 Mio)
SIZES_MAX     = 40             # modèles dont les tailles sont demandées en une fois
LIBRARY_TTL   = 3600           # bibliothèque d'ollama.com gardée (s)
EMIT_EVERY    = 0.25           # progression d'un téléchargement : un événement au plus toutes les… (s)
SPEED_WINDOW  = 4.0            # débit mesuré sur les dernières secondes
DISK_MARGIN   = 300_000_000    # place gardée libre en plus du modèle (octets)
# Manifestes demandés au registre : liste de variantes par moteur (Ollama 0.40 et plus), sinon simple
# (sans la liste, le registre sert directement la variante pour Windows, llamacpp)
MANIFEST_SINGLE = ", ".join((
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
))
MANIFEST_TYPES = "application/vnd.ollama.manifest.list.v2+json, " + MANIFEST_SINGLE

# Fonctions de Boostache qui utilisent un modèle → réglage où il est choisi
USES = {"chat": "last_model", "help": "help_model", "assist": "assist_model", "live": "live_model"}

# Étapes d'un « pull » (statut envoyé par Ollama → texte affiché)
_PHASES = (
    ("pulling manifest", "Préparation…"),
    ("verifying", "Vérification…"),
    ("writing manifest", "Installation…"),
    ("removing", "Nettoyage…"),
    ("success", "Installé"),
)

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008


# ─────────────────────────────────────────────
#  Installation, journal du serveur, disque
# ─────────────────────────────────────────────
def find_exe() -> Path | None:
    """ollama.exe : dans le PATH, sinon à son emplacement d'installation par défaut."""
    candidates = [shutil.which("ollama")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Programs", "Ollama", "ollama.exe"))
    return next((Path(c) for c in candidates if c and os.path.isfile(c)), None)


def _server_log() -> str:
    """Fin du journal du serveur lancé par l'application Ollama ("" s'il n'y en a pas)."""
    path = Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 400_000))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _gib(text: str) -> int:
    """« 6.0 GiB » → octets."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?)i?B", text or "")
    if not m:
        return 0
    return int(float(m.group(1)) * 1024 ** " KMGT".index(m.group(2) or " "))


def server_info() -> dict:
    """Ce que le dernier démarrage du serveur a noté dans son journal : dossier
    des modèles et cartes graphiques utilisables, avec la mémoire qu'Ollama peut
    y prendre (au mieux : vide si le serveur a été lancé autrement que par
    l'application Ollama)."""
    text = _server_log()
    start = text.rfind('msg="server config"')
    if start < 0:
        return {}
    text = text[start:]
    info = {}
    m = re.search(r"OLLAMA_MODELS:(.*?)(?= [A-Za-z_]+:|\])", text)
    if m and m.group(1).strip():
        info["models_dir"] = m.group(1).strip().replace("\\\\", "\\")
    # Mémoire disponible : notée à chaque chargement de modèle (« gpu memory »), sinon
    # celle libre au démarrage moins la réserve d'Ollama
    available = {}
    for line in re.findall(r'msg="gpu memory".*', text):
        gpu = re.search(r"\bid=(\S+)", line)
        value = re.search(r'available="([^"]+)"', line)
        if gpu and value:
            available[gpu.group(1)] = _gib(value.group(1))
    gpus = []
    for line in re.findall(r'msg="inference compute".*', text):
        library = re.search(r"library=(\S+)", line)
        if library and library.group(1).lower() == "cpu":
            continue
        total = re.search(r'total="([^"]+)"', line)
        free = re.search(r'available="([^"]+)"', line)
        gpu = re.search(r"\bid=(\S+)", line)
        name = re.search(r'description="([^"]+)"', line) or re.search(r"name=(\S+)", line)
        if total:
            vram = _gib(total.group(1))
            usable = available.get(gpu.group(1) if gpu else "", 0) or (
                max(0, _gib(free.group(1)) - GPU_RESERVE) if free else int(vram * 0.8))
            gpus.append({"name": name.group(1) if name else "GPU", "vram": vram, "available": usable})
    info["gpus"] = gpus
    return info


def _user_env(name: str) -> str:
    """Variable d'environnement, même définie (pour l'utilisateur) après le lancement de Boostache."""
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def models_dir() -> Path:
    path = server_info().get("models_dir") or _user_env("OLLAMA_MODELS")
    return Path(path) if path else Path.home() / ".ollama" / "models"


def _blobs_size(folder: Path) -> int:
    try:
        return sum(e.stat().st_size for e in os.scandir(folder / "blobs") if e.is_file())
    except OSError:
        return 0


def _disk_free(folder: Path) -> int | None:
    """Place libre sur le disque du dossier (le dossier peut ne pas encore exister)."""
    for p in (folder, *folder.parents):
        try:
            return shutil.disk_usage(p).free
        except OSError:
            continue
    return None


def ram_total() -> int:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_ext_virtual", ctypes.c_ulonglong)]
    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_phys)
    except Exception:
        pass
    return 0


# ─────────────────────────────────────────────
#  Noms de modèles, registre, ollama.com
# ─────────────────────────────────────────────
def normalize_name(text: str) -> str:
    """Nom d'un modèle à télécharger : « gemma4:e4b », mais aussi « ollama run
    gemma4:e4b » ou une adresse ollama.com/library/… ou huggingface.co/… copiée."""
    s = (text or "").strip().strip("\"'`")
    s = re.sub(r"^ollama\s+(?:run|pull)\s+", "", s, flags=re.I).strip()
    m = re.match(r"^https?://(?:www\.)?ollama\.com/(?:library/)?([^?#\s]+)", s)
    if m:
        s = re.sub(r"/(?:tags|blobs/.*)$", "", m.group(1).rstrip("/"))
    m = re.match(r"^https?://(?:www\.)?(?:huggingface\.co|hf\.co)/([^?#\s]+)", s)
    if m:
        s = "hf.co/" + m.group(1).rstrip("/")
    if len(s) > 200 or not re.fullmatch(r"[\w.\-]+(?:/[\w.\-]+)*(?::[\w.\-]+)?", s):
        return ""
    # Sans variante, Ollama prend « latest » (et l'affiche ainsi dans la liste)
    return s if ":" in s.rsplit("/", 1)[-1] else f"{s}:latest"


def registry_ref(name: str) -> tuple[str, str, str] | None:
    """(hôte, dépôt, étiquette) du manifeste publié d'un modèle ; None pour un
    modèle sans registre (créé sur ce PC, modèle en ligne « cloud »)."""
    base, tag = name, "latest"
    if ":" in name.rsplit("/", 1)[-1]:
        base, tag = name.rsplit(":", 1)
    parts = base.split("/")
    if len(parts) == 1:
        host, repo = REGISTRY, f"library/{parts[0]}"
    elif len(parts) == 2:
        host, repo = REGISTRY, base
    elif len(parts) == 3 and "." in parts[0]:
        host, repo = parts[0], f"{parts[1]}/{parts[2]}"
    else:
        return None
    if "cloud" in tag:
        return None
    return host, repo, tag


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment or ""))).strip()


_AGO_UNITS = {"second": ("seconde", "secondes"), "minute": ("minute", "minutes"), "hour": ("heure", "heures"),
              "day": ("jour", "jours"), "week": ("semaine", "semaines"), "month": ("mois", "mois"),
              "year": ("an", "ans")}


def _ago_fr(text: str) -> str:
    """« 3 weeks ago » → « il y a 3 semaines »."""
    m = re.match(r"\s*(\d+|an?)\s+(second|minute|hour|day|week|month|year)s?\s+ago", text or "")
    if not m:
        return (text or "").strip()
    n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    one, many = _AGO_UNITS[m.group(2)]
    return f"il y a {n} {one if n == 1 else many}"


def parse_library(page: str) -> list[dict]:
    """Modèles de la bibliothèque d'ollama.com (page de la bibliothèque ou d'une
    recherche), dans l'ordre de la page ; ceux qui ne tournent qu'en ligne sont écartés."""
    results, seen = [], set()
    for block in page.split("<li")[1:]:
        m = re.search(r'href="/library/([\w.\-]+)"', block)
        if not m or m.group(1) in seen:
            continue
        sizes = [_text(s) for s in re.findall(r'bg-\[#ddf4ff\][^>]*>([^<]+)<', block)]
        cloud = re.search(r'bg-cyan-50[^>]*>\s*cloud\s*<', block) is not None
        if cloud and not sizes:
            continue                       # modèle seulement en ligne (Ollama Cloud)
        seen.add(m.group(1))
        desc = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        pulls = re.search(r"<span\s*>\s*([\d.,]+\s*[KMB]?)\s*</span>\s*<span[^>]*>\s*(?:&nbsp;)?\s*Pulls", block)
        updated = re.search(r"Updated(?:&nbsp;)?\s*</span>\s*<span\s*>([^<]+)</span>", block)
        results.append({
            "name":         m.group(1),
            "description":  _text(desc.group(1)) if desc else "",
            "capabilities": [_text(c).lower() for c in re.findall(r'bg-indigo-50[^>]*>([^<]+)<', block)],
            "sizes":        sizes,
            "pulls":        pulls.group(1).replace(" ", "") if pulls else "",
            "updated":      _ago_fr(updated.group(1)) if updated else "",
        })
    return results


def _size_bytes(text: str) -> int:
    """« 6.6GB - 9.5GB » → taille de la première variante (octets)."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?)B", text or "")
    return int(float(m.group(1)) * 1000 ** " KMGT".index(m.group(2) or " ")) if m else 0


def parse_tags(page: str, name: str) -> list[dict]:
    """Variantes (étiquettes) d'un modèle, d'après sa page « tags » sur ollama.com."""
    anchor = re.compile(r'<a href="/library/(' + re.escape(name) + r':[\w.\-]+)" class="group-hover:underline"')
    marks = list(anchor.finditer(page))
    variants, seen = [], set()
    for i, m in enumerate(marks):
        tag = m.group(1)
        if tag in seen:
            continue
        seen.add(tag)
        block = page[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        cols = [_text(c) for c in re.findall(r'class="col-span-2[^"]*"\s*>(.*?)</(?:p|div)>', block, re.S)]
        digest = re.search(r'font-mono text-\[11px\]">\s*([0-9a-f]{12})\s*<', block)
        size_text = cols[0] if cols else ""
        if tag.split(":", 1)[1].endswith("cloud") or (cols and not size_text):
            continue                       # variante en ligne seulement
        variants.append({
            "name":    tag,
            "size":    _size_bytes(size_text),
            "context": cols[1] if len(cols) > 1 else "",
            "input":   [x.strip().lower() for x in cols[2].split(",")] if len(cols) > 2 and cols[2] else [],
            "id":      digest.group(1) if digest else "",
        })
    return variants


def _short(e: Exception) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return text[:200]


def _go(n: int) -> str:
    """Octets → « 6,6 Go »."""
    return f"{n / 1e9:.1f}".replace(".", ",") + " Go"


def _pull_error(name: str, e: Exception) -> str:
    """Message d'un téléchargement en échec, pour l'interface. Les erreurs
    renvoyées par Ollama (ResponseError) viennent de son propre téléchargement ;
    les autres, de la connexion de Boostache à Ollama."""
    text = str(e).lower()
    if not isinstance(e, _ollama.ResponseError) and _unreachable(e):
        return "Ollama ne répond pas."
    if "file does not exist" in text or "not found" in text or "manifest unknown" in text:
        return f"« {name} » n'existe pas sur ollama.com : vérifie le nom et la variante (ex. gemma4:e4b)."
    if "no space" in text or "not enough space" in text or "disk full" in text:
        return "Plus assez de place sur le disque."
    if any(k in text for k in ("dial tcp", "lookup", "timeout", "tls", "connectex", "eof")):
        return f"Téléchargement impossible : pas de connexion à ollama.com ({_short(e)})."
    return _short(e)


def _grouped(listed) -> dict[str, list]:
    """Modèles listés par Ollama, regroupés par nom, sans ses entrées internes."""
    groups: dict[str, list] = {}
    for m in listed:
        if m.model and not internal_model(m.model):
            groups.setdefault(m.model, []).append(m)
    return groups


def _iso(value) -> str:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else ""


# ─────────────────────────────────────────────
#  Service
# ─────────────────────────────────────────────
class OllamaAdmin:
    """chat : chat.ChatService (présence d'Ollama, liste des modèles partagée) ;
    live : live.LiveService ou None (modèle de l'assistant live)."""

    def __init__(self, bridge, chat, live=None, is_visible=None, notify=None):
        self._bridge = bridge
        self._chat = chat
        self._live = live
        self._is_visible = is_visible or (lambda: True)
        self._notify = notify
        self._lock = threading.Lock()
        self._client = _ollama.Client(timeout=CALL_TIMEOUT)
        self._details: dict[str, dict] = {}      # empreinte du manifeste → détails (/api/show)
        self._updates: dict[str, bool | None] = {}   # modèle → mise à jour disponible
        self._pulls: dict[str, dict] = {}        # modèle → téléchargement
        self._starting = False
        self._web = None                         # httpx.Client vers ollama.com et le registre
        self._library = None                     # (time.monotonic(), modèles d'ollama.com)
        self._tags: dict[str, tuple] = {}        # modèle → (time.monotonic(), (variantes, en ligne seulement))
        if settings.get("ollama_autostart", False):
            threading.Thread(target=self._autostart, daemon=True, name="ollama-autostart").start()

    # ── État ──────────────────────────────────
    def host(self) -> str:
        try:
            return str(self._client._client.base_url).rstrip("/")
        except Exception:
            return "http://127.0.0.1:11434"

    def boot_state(self) -> dict:
        """État connu sans attendre le serveur (démarrage de l'interface)."""
        return {"available": True, "installed": find_exe() is not None, "online": self._chat.online,
                "starting": self._starting, "pulls": self._ui_pulls(), "default_ctx": DEFAULT_CTX}

    def state(self) -> dict:
        """Tout ce que montre la section (bloquant : appels au serveur local)."""
        info = server_info()
        folder = Path(info["models_dir"]) if info.get("models_dir") else models_dir()
        out = {
            "available": True, "installed": find_exe() is not None, "online": False,
            "starting": self._starting, "host": self.host(), "version": "",
            "models_dir": str(folder), "used": _blobs_size(folder), "free": _disk_free(folder),
            "gpus": info.get("gpus", []), "ram": ram_total(), "models": [], "running": [],
            "pulls": self._ui_pulls(), "uses": {}, "error": "", "default_ctx": DEFAULT_CTX,
        }
        try:
            listed = self._client.list().models
        except Exception as e:
            out["error"] = self._chat.failure(e) if _unreachable(e) else _short(e)
            if self._chat.online:
                self._chat.check(force=True)
            return out
        out["online"] = True
        self._chat.check(force=not self._chat.online)     # le reste de l'interface suit
        out["version"] = self._version()
        out["models"] = sorted((self._model_row(group) for group in _grouped(listed).values()),
                               key=lambda r: r["name"].lower())
        out["running"] = self.running().get("running", [])
        out["uses"] = self.uses()
        return out

    def _version(self) -> str:
        try:
            return self._client._client.get("/api/version", timeout=5).json().get("version", "")
        except Exception:
            return ""

    def _model_row(self, group: list) -> dict:
        """Une ligne par modèle : Ollama 0.40 liste une entrée par variante de
        moteur (même nom), toutes gardées sur le disque."""
        m = group[0]
        details = m.details
        info = self._detail(m.model, m.digest or "")
        return {
            "name":         m.model,
            "size":         sum(int(x.size or 0) for x in group),
            "variants":     len(group),
            "modified":     _iso(max((x.modified_at for x in group if x.modified_at), default=None)),
            "digests":      [x.digest or "" for x in group],
            "family":       getattr(details, "family", "") or "",
            "params":       getattr(details, "parameter_size", "") or "",
            "quant":        getattr(details, "quantization_level", "") or "",
            "capabilities": info["capabilities"],
            "context":      info["context"],
            "remote":       info["remote"] or "cloud" in m.model.rsplit(":", 1)[-1],
            "update":       self._updates.get(m.model),
            "registry":     registry_ref(m.model) is not None,
        }

    def _detail(self, name: str, digest: str) -> dict:
        """Capacités et contexte maximal d'un modèle (/api/show), gardés par empreinte."""
        cached = self._details.get(digest)
        if cached:
            return cached
        info = {"capabilities": [], "context": 0, "remote": False}
        try:
            resp = self._client.show(name)
            info["capabilities"] = list(resp.capabilities or [])
            for key, value in (resp.modelinfo or {}).items():
                if key.endswith(".context_length") and isinstance(value, int):
                    info["context"] = value
                    break
            info["remote"] = bool(getattr(resp, "remote_host", None))
        except Exception as e:
            logger.log(f"Ollama : détails de {name} illisibles ({_short(e)})")
            return info
        if digest:
            self._details[digest] = info
        return info

    def running(self) -> dict:
        """Modèles chargés en mémoire."""
        try:
            models = self._client.ps().models
        except Exception as e:
            return {"ok": False, "running": [], "error": _short(e)}
        return {"ok": True, "running": [{
            "name":    m.model or m.name or "",
            "size":    int(m.size or 0),
            "vram":    int(m.size_vram or 0),
            "expires": _iso(m.expires_at),
            "context": int(m.context_length or 0),
        } for m in models]}

    def uses(self) -> dict:
        """Modèle utilisé par chaque fonction de Boostache, parmi ceux qui
        conversent, avec ses replis quand le modèle choisi manque (comme le fait
        chaque fonction)."""
        models = list(self._chat.models)
        if not models:
            return {}

        def pick(*wanted):
            return next((m for m in wanted if m and m in models), models[0])

        last = settings.get("last_model", "")
        out = {"chat": pick(last), "assist": pick(settings.get("assist_model", ""), last)}
        try:
            out["help"] = self._chat.help_model() or ""
            if self._live is not None:
                out["live"] = self._live.model() or ""
        except Exception:
            pass
        return out

    # ── Serveur ───────────────────────────────
    def start(self) -> dict:
        """Lance Ollama : son application (icône de la zone de notification,
        mises à jour) si elle est installée, sinon « ollama serve » sans fenêtre."""
        exe = find_exe()
        if exe is None:
            return {"ok": False, "missing": True, "error": "Ollama n'est pas installé sur ce PC."}
        with self._lock:
            if self._starting:
                return {"ok": True}
            self._starting = True
        app = exe.with_name("ollama app.exe")
        try:
            if app.is_file():
                os.startfile(str(app))
            else:
                subprocess.Popen([str(exe), "serve"], cwd=str(exe.parent), close_fds=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=_CREATE_NO_WINDOW | _DETACHED_PROCESS)
        except OSError as e:
            self._starting = False
            return {"ok": False, "error": f"Lancement d'Ollama impossible : {_short(e)}"}
        logger.log(f"Ollama : lancement ({app.name if app.is_file() else 'ollama serve'}).")
        threading.Thread(target=self._wait_started, daemon=True, name="ollama-start").start()
        self._changed()
        return {"ok": True}

    def _wait_started(self):
        deadline = time.monotonic() + START_WAIT
        online = False
        while time.monotonic() < deadline:
            if self._chat.probe():
                online = True
                break
            time.sleep(1)
        self._starting = False
        if not online:
            logger.log(f"Ollama : lancé, mais ne répond toujours pas après {START_WAIT} s.")
            self._bridge.emit("toast", {"text": "Ollama a été lancé mais ne répond pas encore.", "kind": "error"})
        self._changed()

    def _autostart(self):
        """Réglage « Lancer Ollama avec Boostache » : lancé s'il ne répond toujours
        pas après AUTOSTART_GRACE secondes (à l'ouverture de session, l'application
        Ollama démarre peut-être en même temps que Boostache)."""
        deadline = time.monotonic() + AUTOSTART_GRACE
        while not self._chat.probe():
            if time.monotonic() >= deadline:
                if find_exe() is not None:
                    logger.log("Ollama ne répond pas au démarrage de Boostache : lancement.")
                    self.start()
                return
            time.sleep(3)

    def _changed(self):
        """Quelque chose a changé hors d'un appel de l'interface : elle relit l'état."""
        self._bridge.emit("ollama:changed", {"starting": self._starting})

    # ── Modèles ───────────────────────────────
    def unload(self, name: str) -> dict:
        """Libère la mémoire occupée par un modèle (il reste installé)."""
        try:
            self._client.generate(model=name, prompt="", keep_alive=0)
        except Exception as e:
            try:     # modèle d'embeddings : pas de génération
                self._client.embed(model=name, input="", keep_alive=0)
            except Exception:
                return {"ok": False, "error": self._chat.failure(e)}
        return {"ok": True}

    def delete(self, name: str) -> dict:
        if any(r["name"] == name for r in self.running().get("running", [])):
            self.unload(name)
        try:
            self._client.delete(name)
        except Exception as e:
            if "not found" in str(e).lower():
                return {"ok": False, "error": f"{name} n'est plus installé."}
            return {"ok": False, "error": self._chat.failure(e)}
        logger.log(f"Ollama : modèle {name} supprimé.")
        self._updates.pop(name, None)
        self._chat.model_changed(name)
        self._chat.probe()
        return {"ok": True}

    def check_updates(self) -> dict:
        """Compare chaque modèle installé à celui publié sur son registre : à jour
        si l'une de ses empreintes (une par variante de moteur) est publiée."""
        try:
            groups = {name: {m.digest or "" for m in group}
                      for name, group in _grouped(self._client.list().models).items() if registry_ref(name)}
        except Exception as e:
            return {"ok": False, "error": self._chat.failure(e)}

        def check(name):
            try:
                published = self._published(name)
            except LookupError:
                return name, None, None            # plus publié sous ce nom
            except Exception as e:
                return name, None, e
            return name, not (groups[name] & published), None

        self._http()                # créé avant les threads
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(check, groups))
        errors = [e for _, _, e in results if e is not None]
        if errors and len(errors) == len(results):
            return {"ok": False, "error": f"Registre d'Ollama injoignable ({_short(errors[0])})."}
        for name, update, error in results:
            if error is None:
                self._updates[name] = update
        found = sorted(name for name, update, _ in results if update)
        logger.log("Ollama : " + (f"mise à jour disponible pour {', '.join(found)}." if found
                                  else "modèles à jour."))
        return {"ok": True, "updates": {name: update for name, update, _ in results}}

    # ── Téléchargements ───────────────────────
    def _ui_pulls(self) -> list[dict]:
        with self._lock:
            return [self._ui_pull(j) for j in self._pulls.values()]

    @staticmethod
    def _ui_pull(job: dict) -> dict:
        return {k: job[k] for k in ("name", "status", "completed", "total", "speed", "done",
                                     "error", "cancelled", "update")}

    def pull(self, text: str) -> dict:
        name = normalize_name(text)
        if not name:
            return {"ok": False, "error": "Nom de modèle invalide (ex. gemma4:e4b)."}
        with self._lock:
            job = self._pulls.get(name)
            if job and not job["done"]:
                return {"ok": False, "error": f"{name} est déjà en cours de téléchargement."}
            job = {"name": name, "status": "Préparation…", "completed": 0, "total": 0, "speed": 0,
                   "expected": 0, "done": False, "error": "", "cancelled": False,
                   "update": name in self._chat.models,
                   "cancel": threading.Event(), "client": None}
            self._pulls[name] = job
        threading.Thread(target=self._run_pull, args=(job,), daemon=True, name=f"ollama-pull-{name}").start()
        return {"ok": True, "name": name, "pull": self._ui_pull(job)}

    def cancel(self, name: str):
        with self._lock:
            job = self._pulls.get(name)
        if not job or job["done"]:
            return
        job["cancel"].set()
        client = job["client"]
        if client is not None:
            try:     # coupe la connexion : la lecture en attente s'interrompt
                client._client.close()
            except Exception:
                pass

    def dismiss(self, name: str):
        """Retire un téléchargement terminé (ou en échec) de la liste."""
        with self._lock:
            job = self._pulls.get(name)
            if job and job["done"]:
                del self._pulls[name]

    def _emit_pull(self, job: dict):
        self._bridge.emit("ollama:pull", self._ui_pull(job))

    def _prepare(self, job: dict) -> str:
        """Lit le manifeste publié : taille totale du modèle (la progression d'Ollama
        ne la donne que couche après couche) et place libre sur le disque, couches
        déjà présentes déduites. Retourne un message si elle manque, "" sinon (ou
        si on ne peut pas le savoir)."""
        try:
            layers = self._published_layers(job["name"])
        except Exception:
            return ""
        folder = models_dir()
        need = 0
        for layer in layers:
            size = int(layer.get("size") or 0)
            job["expected"] += size
            digest = str(layer.get("digest", "")).replace(":", "-")
            if digest and not (folder / "blobs" / digest).is_file():
                need += size
        job["total"] = job["expected"]
        free = _disk_free(folder)
        if free is None or need + DISK_MARGIN <= free:
            return ""
        return (f"Pas assez de place pour {job['name']} : {_go(need)} nécessaires, "
                f"{_go(free)} libres sur {folder.anchor or folder}")

    def _run_pull(self, job: dict):
        name = job["name"]
        layers: dict[str, list[int]] = {}
        samples: list[tuple[float, int]] = []
        last_emit = 0.0
        stream, success = None, False
        logger.log(f"Ollama : téléchargement de {name}…")
        job["error"] = self._prepare(job)
        try:
            if job["error"]:
                return
            job["client"] = _ollama.Client(timeout=None)
            stream = job["client"].pull(name, stream=True)
            for p in stream:
                if job["cancel"].is_set():
                    break
                status = p.status or ""
                if p.digest and p.total:
                    layers[p.digest] = [int(p.completed or 0), int(p.total)]
                job["completed"] = sum(c for c, _ in layers.values())
                job["total"] = max(sum(t for _, t in layers.values()), job["expected"])
                job["status"] = next((label for key, label in _PHASES if status.startswith(key)),
                                     "Téléchargement" if p.digest else status or job["status"])
                now = time.monotonic()
                samples.append((now, job["completed"]))
                while samples and now - samples[0][0] > SPEED_WINDOW:
                    samples.pop(0)
                span = now - samples[0][0]
                job["speed"] = int((job["completed"] - samples[0][1]) / span) if span > 0.5 else job["speed"]
                if now - last_emit >= EMIT_EVERY:
                    last_emit = now
                    self._emit_pull(job)
                success = status == "success"
            if job["cancel"].is_set():
                job["cancelled"] = True
            elif not success:
                job["error"] = "Téléchargement interrompu par Ollama."
        except Exception as e:
            if job["cancel"].is_set():
                job["cancelled"] = True
            else:
                job["error"] = _pull_error(name, e)
        finally:
            if stream is not None and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
            if job["client"] is not None:
                try:
                    job["client"]._client.close()
                except Exception:
                    pass
                job["client"] = None
            self._pull_done(job)

    def _pull_done(self, job: dict):
        name = job["name"]
        job["done"], job["speed"] = True, 0
        if not job["error"] and not job["cancelled"]:
            job["completed"] = job["total"]       # dernière progression pas toujours envoyée
        if job["cancelled"]:
            job["status"] = "Annulé"
            logger.log(f"Ollama : téléchargement de {name} annulé.")
        elif job["error"]:
            job["status"] = "Échec"
            logger.log(f"Ollama : téléchargement de {name} en échec : {job['error']}")
        else:
            job["status"] = "Installé"
            logger.log(f"Ollama : {name} {'mis à jour' if job['update'] else 'installé'}.")
            self._updates[name] = False
            self._chat.model_changed(name)
            self._chat.probe()
            if self._notify and not self._is_visible():
                try:
                    self._notify("Ollama", f"{name} est {'à jour' if job['update'] else 'installé'}.")
                except Exception:
                    pass
        self._emit_pull(job)
        self._changed()

    # ── Registre et ollama.com ────────────────
    def _http(self):
        """Client HTTPS vérifié avec les certificats de Windows (truststore) :
        derrière un antivirus ou un proxy qui inspecte le HTTPS, ceux de Python sont refusés."""
        if self._web is None:
            import httpx
            try:
                import truststore
                verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except ImportError:
                verify = True
            self._web = httpx.Client(timeout=WEB_TIMEOUT, follow_redirects=True, verify=verify,
                                     headers={"User-Agent": "Boostache (Ollama)"})
        return self._web

    def _manifest(self, name: str, accept: str = MANIFEST_TYPES) -> bytes:
        """Manifeste publié d'un modèle (octets bruts : leur empreinte identifie la
        version). Le registre ne sert pas les variantes par leur empreinte : celle
        pour Windows s'obtient en ne demandant pas la liste (accept=MANIFEST_SINGLE)."""
        ref = registry_ref(name)
        if ref is None:
            raise LookupError(name)
        host, repo, tag = ref
        r = self._http().get(f"https://{host}/v2/{repo}/manifests/{tag}", headers={"Accept": accept})
        if r.status_code == 404:
            raise LookupError(name)
        r.raise_for_status()
        return r.content

    def _published(self, name: str) -> set[str]:
        """Empreintes de la version publiée : celle du manifeste et, pour une liste
        de variantes, celles des variantes (c'est ce qu'Ollama liste en local)."""
        body = self._manifest(name)
        digests = {hashlib.sha256(body).hexdigest()}
        try:
            for variant in json.loads(body).get("manifests") or []:
                digests.add(str(variant.get("digest", "")).removeprefix("sha256:"))
        except (ValueError, AttributeError):
            pass
        return digests

    def _published_layers(self, name: str) -> list[dict]:
        """Couches à télécharger (celles de la variante pour Windows)."""
        manifest = json.loads(self._manifest(name, MANIFEST_SINGLE))
        return [manifest.get("config") or {}, *(manifest.get("layers") or [])]

    def library(self, force: bool = False) -> dict:
        """Bibliothèque d'ollama.com, des plus téléchargés aux moins téléchargés
        (une seule page : la recherche se fait dans l'interface), gardée LIBRARY_TTL."""
        with self._lock:
            cached = self._library
        if cached and not force and time.monotonic() - cached[0] < LIBRARY_TTL:
            return {"ok": True, "models": cached[1]}
        try:
            r = self._http().get(f"{SITE}/library", params={"sort": "popular"})
            r.raise_for_status()
        except Exception as e:
            return {"ok": False, "error": f"ollama.com ne répond pas ({_short(e)})."}
        models = parse_library(r.text)
        if not models:
            return {"ok": False, "error": "Bibliothèque d'ollama.com illisible : sa présentation a peut-être changé."}
        with self._lock:
            self._library = (time.monotonic(), models)
        return {"ok": True, "models": models}

    def _fetch_tags(self, name: str) -> tuple[list[dict], bool]:
        """(variantes, ne tourne qu'en ligne) d'un modèle d'ollama.com, d'après
        sa page « tags », gardées LIBRARY_TTL. LookupError s'il n'existe pas."""
        with self._lock:
            cached = self._tags.get(name)
        if cached and time.monotonic() - cached[0] < LIBRARY_TTL:
            return cached[1]
        r = self._http().get(f"{SITE}/library/{quote(name)}/tags")
        if r.status_code == 404:
            raise LookupError(name)
        r.raise_for_status()
        variants = parse_tags(r.text, name)
        cloud = not variants and re.search(r'href="/library/' + re.escape(name) + r':[\w.\-]*cloud"', r.text) is not None
        with self._lock:
            self._tags[name] = (time.monotonic(), (variants, cloud))
        return variants, cloud

    def variants(self, name: str) -> dict:
        """Variantes (tailles, quantifications) d'un modèle d'ollama.com."""
        if not re.fullmatch(r"[\w.\-]+", name or ""):
            return {"ok": False, "error": "Nom de modèle invalide."}
        try:
            variants, cloud = self._fetch_tags(name)
        except LookupError:
            return {"ok": False, "error": f"{name} est introuvable sur ollama.com."}
        except Exception as e:
            return {"ok": False, "error": f"ollama.com ne répond pas ({_short(e)})."}
        if not variants:
            if cloud:
                return {"ok": False, "error": f"{name} ne tourne que sur les serveurs d'Ollama (Ollama Cloud) : "
                                              "il ne s'installe pas sur ce PC."}
            return {"ok": False, "error": "Variantes illisibles sur ollama.com."}
        return {"ok": True, "variants": variants}

    def sizes(self, names: list) -> dict:
        """Tailles des variantes principales (quantification par défaut) de
        plusieurs modèles d'ollama.com, pour dire lesquels conviennent à ce PC :
        nom → [{name, size, context, id}], ou None si on ne peut pas le savoir."""
        names = [n for n in dict.fromkeys(str(x) for x in names or []) if re.fullmatch(r"[\w.\-]+", n)]
        self._http()                # créé avant les threads

        def one(name):
            try:
                variants, _ = self._fetch_tags(name)
            except Exception:
                return name, None
            return name, [{k: v[k] for k in ("name", "size", "context", "id")} for v in variants
                          if "-" not in v["name"].split(":", 1)[1]]

        with ThreadPoolExecutor(max_workers=6) as pool:
            return dict(pool.map(one, names[:SIZES_MAX]))
