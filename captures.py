"""
captures.py – Onglet Captures : capture d'une zone de l'écran avec l'outil
Capture de Windows (Win+Maj+S), puis retouche dans l'éditeur de l'interface.

Une capture = trois fichiers dans captures/ :
    capture_<slot>.json       nom et date
    capture_<slot>.png        image retouchée
    capture_<slot>_orig.png   capture d'origine (restaurée par la gomme)
L'ordre des onglets est dans captures/meta.json.
"""

import base64
import io
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from clipboard_listener import clipboard_sequence, set_clipboard_image
from engine import logger
from storage import capture_store, settings, CACHE_DIR

IMAGE_EXTS    = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
EXPORT_EXTS   = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
SNIP_TIMEOUT  = 30              # secondes laissées pour sélectionner la zone
MAX_SIDE      = 16384           # limite des canvas du navigateur
PNG_DATA_URL  = "data:image/png;base64,"


# ─────────────────────────────────────────────
#  Capture d'une zone (partagée avec l'onglet Conversations)
# ─────────────────────────────────────────────
def _open_snipping_tool():
    import keyboard
    keyboard.send("windows+shift+s")


def _clipboard_image():
    """Image du presse-papiers (bitmap, ou premier fichier image copié), ou None."""
    from PIL import Image, ImageGrab
    grabbed = ImageGrab.grabclipboard()
    if isinstance(grabbed, Image.Image):
        return grabbed
    if isinstance(grabbed, list):
        for path in grabbed:
            if os.path.splitext(path)[1].lower() in IMAGE_EXTS and os.path.isfile(path):
                return _open_image(path)
    return None


class Snipper:
    """Lance l'outil Capture de Windows et attend l'image sélectionnée, qui
    arrive dans le presse-papiers.

    hooks : objet fournissant is_visible(), hide(), show() — la fenêtre est
    masquée pendant la sélection pour ne pas cacher l'écran."""

    def __init__(self, hooks):
        self._hooks = hooks
        self._lock = threading.Lock()
        self._active = False
        self._deadline = 0.0
        self._on_done = None

    def request(self, on_done):
        """on_done(image: PIL.Image | None, error: str | None) est appelé depuis
        un thread de travail ; image None = sélection annulée ou expirée.

        Une demande faite pendant une sélection en cours (Impr. écran après un
        Échap, par exemple) rouvre l'outil : c'est elle qui recevra l'image."""
        with self._lock:
            self._on_done = on_done
            self._deadline = time.monotonic() + SNIP_TIMEOUT
            reopen, self._active = self._active, True
        target = self._reopen if reopen else self._run
        threading.Thread(target=target, daemon=True, name="screenshot").start()

    @staticmethod
    def _reopen():
        try:
            _open_snipping_tool()
        except Exception:
            pass

    def _run(self):
        was_visible = self._hooks.is_visible()
        if was_visible:
            self._hooks.hide()
            time.sleep(0.3)
        image, error = None, None
        try:
            seq = clipboard_sequence()
            _open_snipping_tool()
            while True:
                time.sleep(0.3)
                with self._lock:
                    if time.monotonic() > self._deadline:
                        break
                current = clipboard_sequence()
                if current == seq:
                    continue
                try:
                    image = _clipboard_image()
                except Exception:
                    continue            # presse-papiers occupé : nouvel essai
                seq = current
                if image is not None:
                    break
        except Exception as e:
            error = str(e)
        with self._lock:
            self._active = False
            on_done = self._on_done
        try:
            on_done(image, error)
        except Exception as e:
            logger.log(f"Capture d'écran : {e}")
        finally:
            if was_visible:
                self._hooks.show()


# ─────────────────────────────────────────────
#  Helpers images
# ─────────────────────────────────────────────
def _open_image(path: str):
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        return im.copy()


def _normalize(image):
    """RGB ou RGBA, dans les limites d'un canvas."""
    if image.width > MAX_SIDE or image.height > MAX_SIDE:
        raise ValueError(f"image trop grande ({image.width} × {image.height} px).")
    if image.mode not in ("RGB", "RGBA"):
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        image = image.convert("RGBA" if has_alpha else "RGB")
    return image


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _decode(data_url: str) -> bytes:
    """Octets d'un PNG reçu de l'interface (data URL)."""
    if not isinstance(data_url, str) or not data_url.startswith(PNG_DATA_URL):
        raise ValueError("image invalide")
    data = base64.b64decode(data_url[len(PNG_DATA_URL):])
    if not data.startswith(b"\x89PNG"):
        raise ValueError("image invalide")
    return data


def _data_url(data: bytes) -> str:
    return PNG_DATA_URL + base64.b64encode(data).decode("ascii")


def _write_bytes(path: Path, data: bytes):
    """Écriture atomique (fichier temporaire + remplacement)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _png_path(slot: int, orig: bool = False) -> Path:
    return capture_store.dir / f"capture_{slot}{'_orig' if orig else ''}.png"


# ─────────────────────────────────────────────
#  Onglets
# ─────────────────────────────────────────────
class Capture:
    def __init__(self, slot: int, label: str = "", created: str = ""):
        self.slot = slot
        self.label = label
        self.created = created or datetime.now().isoformat(timespec="seconds")

    @property
    def title(self) -> str:
        if self.label:
            return self.label
        try:
            return "Capture " + datetime.fromisoformat(self.created).strftime("%H:%M:%S")
        except ValueError:
            return "Capture"

    def to_ui(self) -> dict:
        return {"slot": self.slot, "label": self.label, "title": self.title,
                "created": self.created}

    def to_json(self) -> dict:
        return {"slot": self.slot, "label": self.label, "created": self.created}


class CapturesService:
    def __init__(self, bridge, snipper: Snipper, show_window):
        self._bridge = bridge
        self._snipper = snipper
        self._show_window = show_window
        self._lock = threading.RLock()
        self._caps: list[Capture] = []
        for slot in capture_store.order():
            data = capture_store.load(slot) or {}
            if _png_path(slot).exists():
                self._caps.append(Capture(slot, data.get("label", ""), data.get("created", "")))

    def _find(self, slot: int) -> Capture | None:
        with self._lock:
            return next((c for c in self._caps if c.slot == slot), None)

    def _next_slot(self) -> int:
        used = set(capture_store.existing_slots()) | {c.slot for c in self._caps}
        return max(used, default=0) + 1

    def _save_order(self):
        with self._lock:
            slots = [c.slot for c in self._caps]
        capture_store.save_order(slots)

    def _toast(self, text: str, kind: str = "info"):
        self._bridge.emit("toast", {"text": text, "kind": kind})

    def snapshot(self) -> dict:
        with self._lock:
            return {"tabs": [c.to_ui() for c in self._caps]}

    # ── Nouvelles captures ────────────────────
    def snip(self):
        """Capture d'une zone de l'écran (bouton « + » ou touche Impr. écran)."""
        self._snipper.request(self._on_snip)

    def _on_snip(self, image, error):
        if error:
            self._toast(f"Capture échouée : {error}", "error")
            return
        if image is None:
            self._toast("Capture d'écran annulée.")
            return
        try:
            self.add_image(image)
        except Exception as e:
            self._toast(f"Capture échouée : {e}", "error")
            return
        self._show_window()

    def add_image(self, image, label: str = "") -> dict:
        """Ouvre une image dans un nouvel onglet (envoyé à l'interface)."""
        data = _png_bytes(_normalize(image))
        with self._lock:
            cap = Capture(self._next_slot(), label)
            _write_bytes(_png_path(cap.slot), data)
            _write_bytes(_png_path(cap.slot, orig=True), data)
            capture_store.save(cap.slot, cap.to_json())
            self._caps.append(cap)
        self._save_order()
        logger.log(f"Capture ajoutée : {cap.title} ({image.width} × {image.height} px)")
        self._bridge.emit("capture:new", {"tab": cap.to_ui(), "image": _data_url(data)})
        return cap.to_ui()

    def paste(self) -> int:
        """Ouvre les images du presse-papiers. Retourne le nombre d'onglets créés."""
        from PIL import Image, ImageGrab
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception:
            return 0
        if isinstance(grabbed, Image.Image):
            self.add_image(grabbed)
            return 1
        if isinstance(grabbed, list):
            images = [p for p in grabbed if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
            return self.open_paths(images)["count"]
        return 0

    def open_paths(self, paths: list[str]) -> dict:
        count, errors = 0, []
        for path in paths or []:
            name = os.path.basename(path)
            if os.path.splitext(path)[1].lower() not in IMAGE_EXTS or not os.path.isfile(path):
                errors.append(f"{name} : ce n'est pas une image.")
                continue
            try:
                self.add_image(_open_image(path), label=os.path.splitext(name)[0])
                count += 1
            except Exception as e:
                errors.append(f"{name} : {e}")
        return {"count": count, "errors": errors}

    # ── Onglets ───────────────────────────────
    def load(self, slot: int) -> dict | None:
        """Images d'un onglet (data URLs), chargées à sa première ouverture."""
        with self._lock:
            if not self._find(slot):
                return None
            try:
                image = _png_path(slot).read_bytes()
            except OSError:
                return None
            orig = _png_path(slot, orig=True)
            return {"image": _data_url(image),
                    "orig": _data_url(orig.read_bytes()) if orig.exists() else None}

    def save(self, slot: int, image: str | None = None, orig: str | None = None) -> bool:
        """Enregistre l'image retouchée et/ou la capture d'origine (après recadrage)."""
        with self._lock:
            if not self._find(slot):
                return False
            for data_url, is_orig in ((image, False), (orig, True)):
                if data_url:
                    _write_bytes(_png_path(slot, is_orig), _decode(data_url))
        return True

    def close(self, slot: int) -> dict:
        with self._lock:
            cap = self._find(slot)
            if not cap:
                return {"removed": False}
            self._caps.remove(cap)
            capture_store.delete(slot)
            for orig in (False, True):
                _png_path(slot, orig).unlink(missing_ok=True)
        self._save_order()
        return {"removed": True}

    def rename(self, slot: int, label: str) -> dict | None:
        with self._lock:
            cap = self._find(slot)
            if not cap:
                return None
            cap.label = label.strip()
            capture_store.save(cap.slot, cap.to_json())
            return cap.to_ui()

    # ── Export ────────────────────────────────
    def copy(self, data_url: str) -> bool:
        from PIL import Image
        with Image.open(io.BytesIO(_decode(data_url))) as im:
            return set_clipboard_image(im)

    def default_filename(self, slot: int) -> str:
        cap = self._find(slot)
        if cap and cap.label:
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cap.label).strip(" .")
            if name:
                return f"{name}.png"
        created = cap.created if cap else datetime.now().isoformat(timespec="seconds")
        try:
            stamp = datetime.fromisoformat(created).strftime("%Y-%m-%d %H%M%S")
        except ValueError:
            stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        return f"Capture {stamp}.png"

    def export(self, path: str, data_url: str) -> str:
        """Enregistre l'image au format déduit de l'extension (PNG par défaut)."""
        from PIL import Image
        target = Path(path)
        if target.suffix.lower() not in EXPORT_EXTS:
            target = target.with_name(target.name + ".png")
        ext = target.suffix.lower()
        with Image.open(io.BytesIO(_decode(data_url))) as im:
            if ext in (".jpg", ".jpeg"):
                im.convert("RGB").save(target, "JPEG", quality=92)
            elif ext == ".bmp":
                im.convert("RGB").save(target, "BMP")
            elif ext == ".webp":
                im.save(target, "WEBP", quality=92)
            else:
                im.save(target, "PNG")
        settings.set("capture_save_dir", str(target.parent))
        return str(target)

    def temp_file(self, slot: int, data_url: str) -> str:
        """Copie temporaire (cache, vidé au démarrage) portant le nom de la capture,
        pour la joindre à une conversation."""
        path = CACHE_DIR / f"boostache_{int(time.time() * 1000)}" / self.default_filename(slot)
        _write_bytes(path, _decode(data_url))
        return str(path)
