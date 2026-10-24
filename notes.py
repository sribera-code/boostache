"""
notes.py – Onglet Notes : un fichier JSON par note (notes/note_<slot>.json),
ordre des onglets dans notes/meta.json.

Une note peut être liée à un fichier texte (« Enregistrer dans un fichier »,
« Ouvrir un fichier ») : chaque enregistrement de la note réécrit alors le fichier
(enregistrement automatique), ou seulement à la demande (Ctrl+S), dans son encodage
et avec ses fins de ligne. Un fichier modifié en dehors de Boostache est relu si la
note n'a pas changé depuis ; sinon rien n'est écrasé (conflit : l'utilisateur choisit
la version à garder).
"""

import hashlib
import os
import re
import tempfile
import threading

import docreader
from engine import logger
from storage import note_store, settings
from winutil import recycle

TITLE_LEN = 24
MAX_BYTES = 4_000_000          # fichier texte ouvert dans une note
MAX_CHARS = 1_000_000          # texte d'un document (PDF, Word…) copié dans une note
IMPORTED_KINDS = {"pdf", "word", "sheet", "slides", "mail", "book"}   # texte extrait, fichier non lié


class UserError(Exception):
    """Message montré tel quel à l'utilisateur."""


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()


def _stat(path: str) -> tuple[int, int] | None:
    """(date de modification, taille) du fichier, None s'il n'existe plus."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_mtime_ns, st.st_size


def read_text(path: str) -> tuple[str, str, str]:
    """Fichier texte → (texte aux fins de ligne « \\n », encodage, fin de ligne d'origine)."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext in docreader.LEGACY:
        raise UserError(f"« {name} » est dans un ancien format : enregistrez-le en {docreader.LEGACY[ext]} pour l'ouvrir.")
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise UserError(f"« {name} » illisible ({e.strerror or e}).")
    if len(data) > MAX_BYTES:
        raise UserError(f"« {name} » est trop gros pour une note (plus de {MAX_BYTES // 1_000_000} Mo).")
    if docreader.binary(data):
        raise UserError(f"« {name} » n'est pas un fichier texte.")
    text, encoding = docreader.decode(data)
    crlf = text.count("\r\n")
    newline = "\n" if text.count("\n") - crlf > crlf else "\r\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding, newline


def _encode(text: str, encoding: str, newline: str) -> tuple[bytes, str]:
    """Texte → octets dans le format du fichier ; UTF-8 si un caractère manque à son encodage."""
    body = text.replace("\n", newline or "\r\n")
    try:
        return body.encode(encoding or "utf-8"), encoding or "utf-8"
    except (UnicodeEncodeError, LookupError):
        return body.encode("utf-8", "replace"), "utf-8"


def _write_file(path: str, data: bytes, keep_old: bool = False):
    """Écriture complète ou rien (fichier temporaire puis remplacement).
    keep_old : la version remplacée part d'abord à la corbeille (récupérable)."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".boostache-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if keep_old and os.path.exists(path):
            recycle(path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def default_filename(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title.rstrip("…")).strip(" .")
    return f"{name or 'Note'}.txt"


class Note:
    def __init__(self, slot: int, label: str = "", content: str = "", file: dict | None = None):
        self.slot = slot
        self.label = label
        self.content = content
        # Fichier lié : {path, encoding, newline, auto, mtime, size, hash} ; mtime, size et hash :
        # à la dernière écriture ou lecture ; auto : réécrit à chaque enregistrement de la note
        self.file = file if isinstance(file, dict) and file.get("path") else None
        self.file_state = ""       # "" | "conflict" | "missing" | "error" (non enregistré)
        self.file_error = ""

    @property
    def title(self) -> str:
        if self.label:
            return self.label
        if self.file:
            return os.path.splitext(os.path.basename(self.file["path"]))[0]
        first = self.content.strip().split("\n")[0].strip() if self.content.strip() else ""
        return first[:TITLE_LEN] + ("…" if len(first) > TITLE_LEN else "")

    @property
    def auto(self) -> bool:
        return bool(self.file and self.file.get("auto", True))

    def file_ui(self) -> dict | None:
        """pending : la note a des modifications qui ne sont pas dans le fichier."""
        if not self.file:
            return None
        path = self.file["path"]
        return {"path": path, "name": os.path.basename(path), "state": self.file_state, "error": self.file_error,
                "auto": self.auto, "pending": _digest(self.content) != self.file.get("hash")}

    def to_ui(self) -> dict:
        return {"slot": self.slot, "label": self.label, "title": self.title,
                "content": self.content, "file": self.file_ui()}

    def to_json(self) -> dict:
        data = {"slot": self.slot, "label": self.label, "content": self.content}
        if self.file:
            data["file"] = self.file
        return data


class NotesService:
    def __init__(self):
        self._lock = threading.RLock()
        self._notes: list[Note] = []
        for slot in note_store.order():
            data = note_store.load(slot) or {}
            self._notes.append(Note(slot, data.get("label", ""), data.get("content", ""), data.get("file")))
        if not self._notes:
            n = Note(self._next_slot())
            self._notes.append(n)
            note_store.save(n.slot, n.to_json())
            self._save_order()
        # Fichiers modifiés pendant que Boostache était fermé
        self.check()

    def _next_slot(self) -> int:
        used = set(note_store.existing_slots()) | {n.slot for n in self._notes}
        return max(used, default=0) + 1

    def _find(self, slot: int) -> Note | None:
        with self._lock:
            return next((n for n in self._notes if n.slot == slot), None)

    def _save_order(self):
        with self._lock:
            slots = [n.slot for n in self._notes]
        note_store.save_order(slots)

    def snapshot(self) -> dict:
        with self._lock:
            return {"tabs": [n.to_ui() for n in self._notes]}

    def _insert(self, after_slot: int | None) -> Note:
        with self._lock:
            n = Note(self._next_slot())
            idx = next((i for i, x in enumerate(self._notes) if x.slot == after_slot), None)
            if idx is None:
                self._notes.append(n)
            else:
                self._notes.insert(idx + 1, n)
        return n

    def new(self, after_slot: int | None = None) -> dict:
        n = self._insert(after_slot)
        note_store.save(n.slot, n.to_json())
        self._save_order()
        return n.to_ui()

    def close(self, slot: int) -> dict:
        """Ferme une note (son fichier reste où il est). La dernière note est vidée plutôt que fermée."""
        n = self._find(slot)
        if not n:
            return {"removed": False}
        with self._lock:
            if len(self._notes) <= 1:
                n.label, n.content, n.file, n.file_state = "", "", None, ""
                note_store.save(n.slot, n.to_json())
                return {"removed": False, "reset": n.to_ui()}
            self._notes.remove(n)
        note_store.delete(slot)
        self._save_order()
        return {"removed": True}

    def rename(self, slot: int, label: str) -> dict | None:
        n = self._find(slot)
        if not n:
            return None
        n.label = label.strip()
        note_store.save(n.slot, n.to_json())
        return n.to_ui()

    def save(self, slot: int, content: str, to_file: bool = False) -> dict | None:
        """Enregistre la note ; son fichier aussi s'il est en enregistrement automatique,
        ou si to_file (Ctrl+S)."""
        n = self._find(slot)
        if not n:
            return None
        with self._lock:
            n.content = content
            if n.file and (n.auto or to_file):
                self._write(n)
            note_store.save(n.slot, n.to_json())
            return n.to_ui()

    # ── Fichier lié ───────────────────────────
    def _record(self, n: Note, text: str):
        """Le fichier contient maintenant `text` : sa date et sa taille servent à voir s'il change ailleurs."""
        n.file["mtime"], n.file["size"] = _stat(n.file["path"]) or (0, 0)
        n.file["hash"] = _digest(text)
        n.file_state = n.file_error = ""

    def _write(self, n: Note, force: bool = False):
        """Réécrit le fichier lié avec la note. Sans force, rien n'est écrasé si le fichier a été
        modifié ailleurs (conflit) ou s'il a disparu ; avec force, la version écrasée part à la
        corbeille et un fichier disparu est recréé."""
        f = n.file
        path = f["path"]
        try:
            cur = _stat(path)
            other = False          # le fichier contient une version que la note n'a jamais vue
            if cur is None:
                if not force:
                    n.file_state = "missing"
                    return
                os.makedirs(os.path.dirname(path), exist_ok=True)
            elif cur != (f.get("mtime"), f.get("size")):
                text = read_text(path)[0]
                if text == n.content:
                    self._record(n, text)
                    return
                other = _digest(text) != f.get("hash")
                if other and not force:
                    n.file_state = "conflict"
                    return
            elif _digest(n.content) == f.get("hash"):
                n.file_state = n.file_error = ""
                return
            data, encoding = _encode(n.content, f.get("encoding"), f.get("newline"))
            if encoding != f.get("encoding"):
                logger.log(f"Notes : « {os.path.basename(path)} » réécrit en UTF-8 "
                           f"(caractère absent de l'encodage {f.get('encoding')}).", level="warning")
                f["encoding"] = encoding
            _write_file(path, data, keep_old=other)
            self._record(n, n.content)
        except (UserError, OSError) as e:
            error = str(e) if isinstance(e, UserError) else e.strerror or str(e)
            if (n.file_state, n.file_error) != ("error", error):     # une fois, pas à chaque frappe
                logger.log(f"Notes : enregistrement de {path} impossible ({error}).", level="warning")
            n.file_state, n.file_error = "error", error

    def _refresh(self, n: Note, dirty: bool = False) -> bool:
        """Revoit le fichier lié. Modifié ailleurs : relu si la note n'a pas changé depuis
        (→ True), conflit sinon. dirty : l'interface a des modifications pas encore envoyées."""
        f = n.file
        cur = _stat(f["path"])
        if cur is None:
            n.file_state, n.file_error = "missing", ""
            return False
        if cur == (f.get("mtime"), f.get("size")):
            if n.file_state == "missing":
                n.file_state = ""
            if n.auto and not dirty and _digest(n.content) != f.get("hash"):
                self._write(n)        # écriture restée en attente (fichier revenu, erreur passée…)
            return False
        try:
            text, encoding, newline = read_text(f["path"])
        except UserError as e:
            n.file_state, n.file_error = "error", str(e)
            return False
        if text == n.content or _digest(text) == f.get("hash"):
            self._record(n, text)
            if text != n.content and n.auto and not dirty:
                self._write(n)
            return False
        if dirty or _digest(n.content) != f.get("hash"):
            n.file_state, n.file_error = "conflict", ""
            return False
        n.content = text
        f["encoding"], f["newline"] = encoding, newline
        self._record(n, text)
        logger.log(f"Notes : « {os.path.basename(f['path'])} » modifié en dehors de Boostache, note mise à jour.")
        return True

    def check(self, dirty=()) -> list[dict]:
        """Fichiers des notes liées revus (retour sur les notes, fenêtre réactivée).
        → les notes dont l'état a changé ; "reloaded" : texte relu depuis le fichier."""
        dirty = {int(s) for s in dirty or ()}
        out = []
        with self._lock:
            for n in self._notes:
                if not n.file:
                    continue
                state, record = (n.file_state, n.file_error), dict(n.file)
                try:
                    reloaded = self._refresh(n, n.slot in dirty)
                except OSError as e:
                    reloaded = False
                    n.file_state, n.file_error = "error", e.strerror or str(e)
                if n.file != record:
                    note_store.save(n.slot, n.to_json())
                if reloaded or (n.file_state, n.file_error) != state:
                    out.append({**n.to_ui(), "reloaded": reloaded})
        return out

    def save_suggestion(self, slot: int) -> tuple[str, str]:
        """Dossier et nom proposés pour « Enregistrer dans un fichier »."""
        n = self._find(slot)
        if n and n.file:
            return os.path.dirname(n.file["path"]), os.path.basename(n.file["path"])
        folder = settings.get("notes_dir") or ""
        if not os.path.isdir(folder):
            folder = os.path.join(os.path.expanduser("~"), "Documents")
        return folder, default_filename(n.title if n else "")

    def save_as(self, slot: int, path: str) -> dict:
        """Enregistre la note dans `path` (choisi dans la fenêtre Enregistrer) et l'y lie."""
        n = self._find(slot)
        if not n:
            raise UserError("Cette note n'existe plus.")
        path = os.path.abspath(path)
        if not os.path.splitext(path)[1]:
            path += ".txt"
        with self._lock:
            other = next((x for x in self._notes if x is not n and x.file and _same(x.file["path"], path)), None)
            if other:
                raise UserError(f"« {os.path.basename(path)} » est déjà ouvert dans la note « {other.title} ».")
            keep = n.file if n.file and _same(n.file["path"], path) else {}
            data, encoding = _encode(n.content, keep.get("encoding", "utf-8"), keep.get("newline", "\r\n"))
            try:
                _write_file(path, data)
            except OSError as e:
                raise UserError(f"Enregistrement impossible ({e.strerror or e}).")
            n.file = {"path": path, "encoding": encoding, "newline": keep.get("newline", "\r\n"),
                      "auto": n.auto if n.file else bool(settings.get("notes_file_autosave"))}
            self._record(n, n.content)
            note_store.save(n.slot, n.to_json())
        settings.set("notes_dir", os.path.dirname(path))
        logger.log(f"Notes : note « {n.title} » enregistrée dans {path}.")
        return n.to_ui()

    def open_paths(self, paths: list[str], reuse: int | None = None, after: int | None = None) -> dict:
        """Ouvre des fichiers dans des notes : un fichier texte est lié à sa note, un document
        (PDF, Word…) y est copié. reuse : note vide à remplir d'abord ; after : les nouvelles
        notes suivent celle-ci. → {notes, order, errors}."""
        out, errors = [], []
        for raw in paths:
            try:
                n, how = self._open(str(raw), reuse, after)
            except UserError as e:
                errors.append(str(e))
                continue
            out.append({**n.to_ui(), "how": how})
            if how != "existing":
                if n.slot == reuse:
                    reuse = None
                after = n.slot
        if out:
            self._save_order()
        with self._lock:
            order = [n.slot for n in self._notes]
        return {"notes": out, "order": order, "errors": errors}

    def _open(self, path: str, reuse: int | None, after: int | None) -> tuple[Note, str]:
        path = os.path.abspath(path)
        name = os.path.basename(path)
        if os.path.isdir(path):
            raise UserError(f"« {name} » est un dossier : choisissez un fichier.")
        if not os.path.isfile(path):
            raise UserError(f"« {name} » introuvable.")
        with self._lock:
            existing = next((x for x in self._notes if x.file and _same(x.file["path"], path)), None)
        if existing:
            return existing, "existing"
        stem, ext = os.path.splitext(name)
        imported = docreader.kind(ext) in IMPORTED_KINDS
        if imported:
            try:
                segments, truncated = docreader.read(path, MAX_CHARS)
            except docreader.Unreadable as e:
                raise UserError(f"« {name} » : {e}.")
            except Exception as e:
                raise UserError(f"« {name} » illisible ({e}).")
            text = "\n\n".join(t.strip("\n") for t, _ in segments)[:MAX_CHARS]
        else:
            text, encoding, newline = read_text(path)
        with self._lock:
            target = self._find(reuse) if reuse is not None else None
            n = target if target and not target.file and not target.content.strip() else self._insert(after)
            n.content = text
            if imported:
                n.label = n.label or stem
            else:
                n.file = {"path": path, "encoding": encoding, "newline": newline,
                          "auto": bool(settings.get("notes_file_autosave"))}
                self._record(n, text)
            note_store.save(n.slot, n.to_json())
        settings.set("notes_dir", os.path.dirname(path))
        logger.log(f"Notes : {path} {'copié dans' if imported else 'ouvert dans'} la note {n.slot}.")
        return n, "imported" if imported else "opened"

    def reload(self, slot: int) -> dict:
        """Conflit tranché pour le fichier : la note reprend son texte."""
        n = self._find(slot)
        if not n or not n.file:
            raise UserError("Cette note n'est liée à aucun fichier.")
        with self._lock:
            text, encoding, newline = read_text(n.file["path"])
            n.content = text
            n.file["encoding"], n.file["newline"] = encoding, newline
            self._record(n, text)
            note_store.save(n.slot, n.to_json())
            return n.to_ui()

    def write(self, slot: int) -> dict:
        """Conflit tranché pour la note (l'autre version à la corbeille), fichier recréé, nouvel essai."""
        n = self._find(slot)
        if not n or not n.file:
            raise UserError("Cette note n'est liée à aucun fichier.")
        with self._lock:
            self._write(n, force=True)
            note_store.save(n.slot, n.to_json())
            return n.to_ui()

    def set_auto(self, slot: int, auto: bool) -> dict | None:
        """Enregistrement automatique dans le fichier ou à la demande ; le choix vaut aussi
        pour les prochains fichiers. Activé : les modifications en attente partent tout de suite."""
        n = self._find(slot)
        if not n or not n.file:
            return None
        with self._lock:
            n.file["auto"] = bool(auto)
            if n.auto:
                self._write(n)
            note_store.save(n.slot, n.to_json())
        settings.set("notes_file_autosave", bool(auto))
        return n.to_ui()

    def unlink(self, slot: int) -> dict | None:
        """La note reste dans Boostache, le fichier n'est plus modifié."""
        n = self._find(slot)
        if not n:
            return None
        with self._lock:
            if n.file and not n.label:
                n.label = n.title           # le nom du fichier reste celui de l'onglet
            n.file, n.file_state, n.file_error = None, "", ""
            note_store.save(n.slot, n.to_json())
            return n.to_ui()
