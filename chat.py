"""
chat.py – Onglet Conversations : chat LLM local via Ollama.

Streaming token par token, réflexion des modèles « thinking » (champ
thinking), pièces jointes (texte/code et images), persistance des
conversations et des onglets ouverts.
"""

import base64
import hashlib
import html
import io
import os
import re
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

from clipboard_listener import set_clipboard_content, set_clipboard_image
from engine import logger
from storage import settings, conversations, chat_meta, ATTACHMENTS_DIR, CACHE_DIR

try:
    import ollama as _ollama
    OLLAMA_OK = True
except ImportError:
    _ollama = None
    OLLAMA_OK = False


IMAGE_EXTS     = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_TEXT_BYTES = 1_000_000
TITLE_LEN      = 24
DELTA_INTERVAL = 0.04          # regroupement des tokens envoyés à l'interface

_LEGACY_FILE_BLOCK = re.compile(r"\[Fichier : (.+?)\]\n```\n.*?\n```(?:\n\n)?", re.S)


def ollama_server_running(timeout: float = 0.6) -> bool:
    """Vérifie qu'un serveur Ollama répond sur OLLAMA_HOST (défaut localhost:11434)."""
    if not OLLAMA_OK:
        return False
    host_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if "://" not in host_url:
        host_url = "http://" + host_url
    parsed = urlparse(host_url)
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 11434),
                                      timeout=timeout):
            return True
    except OSError:
        return False


# ─────────────────────────────────────────────
#  Helpers pièces jointes
# ─────────────────────────────────────────────
def _thumbnail(path: str) -> str | None:
    """Miniature JPEG (data URL) pour l'affichage dans l'interface."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.thumbnail((320, 320))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (34, 34, 34))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _store_image(path: str) -> str:
    """Copie une image jointe dans conversations/attachments (PNG normalisé,
    nommé par empreinte) et retourne son chemin — c'est ce chemin qui est
    enregistré dans l'historique et envoyé à Ollama."""
    from PIL import Image
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, "PNG")
    data = buf.getvalue()
    dest = ATTACHMENTS_DIR / f"{hashlib.sha1(data).hexdigest()[:16]}.png"
    if not dest.exists():
        dest.write_bytes(data)
    return str(dest)


def _load_image_ref(ref: str):
    """Image d'un message envoyé : chemin du fichier joint, ou base64 (anciennes
    conversations)."""
    from PIL import Image
    source = ref if os.path.isfile(ref) else io.BytesIO(base64.b64decode(ref))
    with Image.open(source) as im:
        im.load()
        return im.copy()


def _message_html(text: str, images) -> str:
    """Message en HTML pour le presse-papiers : texte puis images (data URL)."""
    parts = [f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>"] if text else []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, "PNG")
        parts.append(f'<p><img src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode("ascii")}" '
                     f'width="{im.width}" height="{im.height}"></p>')
    return "".join(parts)


def _read_text_file(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw[:8192]:
        raise ValueError("fichier binaire non pris en charge")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _split_legacy(content: str) -> tuple[str, list[dict]]:
    """Anciennes conversations : les fichiers joints sont inclus dans le texte
    sous forme de blocs « [Fichier : nom] ```…``` »."""
    names: list[str] = []

    def _grab(m):
        names.append(m.group(1))
        return ""

    rest = _LEGACY_FILE_BLOCK.sub(_grab, content or "")
    return rest.strip(), [{"name": n, "type": "text"} for n in names]


def _ui_message(m: dict) -> dict | None:
    role = m.get("role")
    if role == "user":
        if "display" in m:
            text, atts = m.get("display", ""), list(m.get("attachments") or [])
        else:
            text, atts = _split_legacy(m.get("content", ""))
            atts += [{"name": "image", "type": "image"} for _ in (m.get("images") or [])]
        return {"role": "user", "text": text, "attachments": atts}
    if role == "assistant":
        return {"role": "assistant", "text": m.get("content", ""),
                "thinking": m.get("thinking", ""), "model": m.get("model", "")}
    return None


def _to_ollama(m: dict) -> dict:
    """Message au format Ollama (sans les champs d'affichage)."""
    out = {"role": m.get("role", "user"), "content": m.get("content", "")}
    images = [ref for ref in (m.get("images") or [])
              if isinstance(ref, str) and (os.path.isfile(ref) or len(ref) > 256)]
    if images:
        out["images"] = images
    return out


# ─────────────────────────────────────────────
#  Onglet de conversation
# ─────────────────────────────────────────────
class ChatTab:
    def __init__(self, conv_id: str | None = None, label: str = "", model: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.conv_id = conv_id
        self.label = label
        self.model = model
        self.messages: list[dict] = []
        self.pending: list[dict] = []     # pièces jointes pas encore envoyées
        self.streaming = False
        self.partial: dict | None = None  # réponse en cours de génération
        self.stop_evt = threading.Event()
        self.gen = 0                      # incrémenté quand l'onglet est réinitialisé

    @property
    def title(self) -> str:
        if self.label:
            return self.label
        for m in self.messages:
            if m.get("role") == "user":
                text = _ui_message(m)["text"].strip()
                first = text.split("\n")[0].strip() if text else ""
                if first:
                    return first[:TITLE_LEN] + ("…" if len(first) > TITLE_LEN else "")
        return ""

    def ui_attachments(self) -> list[dict]:
        return [{k: a.get(k) for k in ("id", "name", "type", "thumb", "path")}
                for a in self.pending]

    def to_ui(self) -> dict:
        return {
            "id":          self.id,
            "title":       self.title,
            "label":       self.label,
            "model":       self.model,
            "streaming":   self.streaming,
            "partial":     self.partial,
            "messages":    [u for u in (_ui_message(m) for m in self.messages) if u],
            "attachments": self.ui_attachments(),
        }


# ─────────────────────────────────────────────
#  Service
# ─────────────────────────────────────────────
class ChatService:
    """snipper : captures.Snipper, pour joindre une capture d'une zone de l'écran."""

    def __init__(self, bridge, snipper):
        self._bridge = bridge
        self._snipper = snipper
        self._lock = threading.RLock()
        self.tabs: list[ChatTab] = []
        self.models: list[str] = []
        self.models_error = ""
        self._restore()
        self.refresh_models()

    # ── Restauration / persistance ────────────
    def _restore(self):
        for entry in chat_meta.load():
            tab = ChatTab(label=entry.get("label", ""), model=entry.get("model", ""))
            conv_id = entry.get("conv_id")
            data = conversations.load(conv_id) if conv_id else None
            if data:
                tab.conv_id = conv_id
                tab.messages = data.get("messages") or []
                tab.model = tab.model or data.get("model", "")
            self.tabs.append(tab)
        if not self.tabs:
            tab = ChatTab()
            latest = conversations.load_latest()
            if latest and latest.get("messages"):
                tab.conv_id = latest.get("id")
                tab.messages = latest["messages"]
                tab.model = latest.get("model", "")
            self.tabs.append(tab)

    def _save_meta(self):
        with self._lock:
            entries = [{"conv_id": t.conv_id, "label": t.label, "model": t.model}
                       for t in self.tabs if t.conv_id or t.label]
        chat_meta.save(entries)

    def _find(self, tab_id: str) -> ChatTab | None:
        with self._lock:
            return next((t for t in self.tabs if t.id == tab_id), None)

    def _emit_tab(self, tab: ChatTab):
        self._bridge.emit("chat:tab", tab.to_ui())

    def _emit_attachments(self, tab: ChatTab):
        self._bridge.emit("chat:attachments", {"tab": tab.id, "attachments": tab.ui_attachments()})

    # ── Snapshot & modèles ────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "available":     True,
                "models":        self.models,
                "models_error":  self.models_error,
                "default_model": settings.get("last_model", ""),
                "tabs":          [t.to_ui() for t in self.tabs],
            }

    def refresh_models(self):
        def fetch():
            try:
                result = _ollama.list()
                self.models = [m.model for m in result.models]
                self.models_error = "" if self.models else "Aucun modèle installé (ollama pull <modèle>)."
            except Exception as e:
                self.models = []
                self.models_error = f"Ollama injoignable : {e}"
            self._bridge.emit("chat:models", {"models": self.models, "error": self.models_error,
                                              "default_model": settings.get("last_model", "")})
        threading.Thread(target=fetch, daemon=True, name="ollama-models").start()

    # ── Onglets ───────────────────────────────
    def new_tab(self, after_id: str | None = None) -> dict:
        with self._lock:
            ref = self._find(after_id) if after_id else None
            tab = ChatTab(model=(ref.model if ref else "") or settings.get("last_model", ""))
            idx = self.tabs.index(ref) + 1 if ref else len(self.tabs)
            self.tabs.insert(idx, tab)
        return tab.to_ui()

    def close_tab(self, tab_id: str) -> dict:
        tab = self._find(tab_id)
        if not tab:
            return {"removed": False}
        tab.stop_evt.set()
        self._discard_pending(tab)
        with self._lock:
            if len(self.tabs) <= 1:
                tab.messages, tab.conv_id, tab.label, tab.partial = [], None, "", None
                tab.stop_evt = threading.Event()
                tab.streaming = False
                tab.gen += 1
                self._save_meta()
                return {"removed": False, "reset": tab.to_ui()}
            self.tabs.remove(tab)
        self._save_meta()
        return {"removed": True}

    def rename_tab(self, tab_id: str, label: str) -> dict | None:
        tab = self._find(tab_id)
        if not tab:
            return None
        tab.label = label.strip()
        self._save_meta()
        return tab.to_ui()

    def set_model(self, tab_id: str, model: str):
        tab = self._find(tab_id)
        if tab and model:
            tab.model = model
            settings.set("last_model", model)
            self._save_meta()

    # ── Envoi & streaming ─────────────────────
    def send(self, tab_id: str, text: str, model: str) -> dict:
        tab = self._find(tab_id)
        if not tab:
            return {"ok": False, "error": "Conversation introuvable."}
        if tab.streaming:
            return {"ok": False, "error": "Une réponse est déjà en cours."}
        text = (text or "").strip()
        if not text and not tab.pending:
            return {"ok": False, "error": ""}
        if not self.models:
            return {"ok": False, "error": self.models_error or "Aucun modèle disponible."}
        if not model or model not in self.models:
            return {"ok": False, "error": "Sélectionne un modèle valide."}

        blocks, images, atts = [], [], []
        try:
            for a in tab.pending:
                if a["type"] == "text":
                    blocks.append(f"[Fichier : {a['name']}]\n```\n{a['content']}\n```")
                    atts.append({"name": a["name"], "type": "text"})
                else:
                    images.append(_store_image(a["path"]))
                    atts.append({"name": a["name"], "type": "image", "thumb": a.get("thumb")})
        except Exception as e:
            return {"ok": False, "error": f"Pièce jointe illisible : {e}"}

        content = "\n\n".join(blocks + ([text] if text else []))
        message = {"role": "user", "content": content, "display": text, "attachments": atts}
        if images:
            message["images"] = images

        self._discard_pending(tab)
        with self._lock:
            tab.messages.append(message)
            tab.model = model
            tab.streaming = True
            tab.partial = {"content": "", "thinking": "", "model": model}
            tab.stop_evt = threading.Event()
        settings.set("last_model", model)
        self._emit_tab(tab)
        threading.Thread(target=self._stream, args=(tab, model),
                         daemon=True, name=f"chat-{tab.id}").start()
        return {"ok": True}

    def stop(self, tab_id: str):
        tab = self._find(tab_id)
        if tab:
            tab.stop_evt.set()

    def _stream(self, tab: ChatTab, model: str):
        system_prompt = settings.get("system_prompt", "").strip()
        with self._lock:
            payload = [_to_ollama(m) for m in tab.messages]
            stop_evt, gen = tab.stop_evt, tab.gen
        if system_prompt:
            payload.insert(0, {"role": "system", "content": system_prompt})

        content = thinking = ""
        buf_c = buf_t = ""
        last_flush = time.monotonic()
        error, stopped, stream = None, False, None

        def flush():
            nonlocal buf_c, buf_t, last_flush
            if (buf_c or buf_t) and tab.gen == gen:
                self._bridge.emit("chat:delta", {"tab": tab.id, "content": buf_c, "thinking": buf_t})
            buf_c = buf_t = ""
            last_flush = time.monotonic()

        try:
            stream = _ollama.chat(model=model, messages=payload, stream=True)
            for chunk in stream:
                if stop_evt.is_set():
                    stopped = True
                    break
                msg = chunk.message
                d_think = getattr(msg, "thinking", None) or ""
                d_text = msg.content or ""
                thinking += d_think
                content += d_text
                buf_t += d_think
                buf_c += d_text
                if tab.gen == gen:
                    tab.partial = {"content": content, "thinking": thinking, "model": model}
                if time.monotonic() - last_flush >= DELTA_INTERVAL:
                    flush()
        except Exception as e:
            error = str(e)
            if "connect" in error.lower() or "10061" in error:
                error = "Ollama injoignable. Vérifie que le serveur est lancé."
            logger.log(f"Chat ({model}) : {error}")
        finally:
            if stream is not None and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
        flush()

        saved = None
        with self._lock:
            # Onglet fermé ou réinitialisé pendant la génération : on jette la réponse
            alive = tab in self.tabs and tab.gen == gen
            if alive and content.strip():
                saved = {"role": "assistant", "content": content, "model": model}
                if thinking.strip():
                    saved["thinking"] = thinking
                tab.messages.append(saved)
            if alive:
                tab.streaming = False
                tab.partial = None
        if not alive:
            return
        if saved:
            tab.conv_id = conversations.save(tab.messages, model, tab.conv_id)
            self._save_meta()
        self._bridge.emit("chat:done", {
            "tab":     tab.id,
            "message": _ui_message(saved) if saved else None,
            "error":   error,
            "stopped": stopped,
            "title":   tab.title,
        })

    # ── Pièces jointes ────────────────────────
    def _add_file(self, tab: ChatTab, path: str, temp: bool = False) -> str | None:
        """Ajoute un fichier aux pièces jointes. Retourne un message d'erreur ou None."""
        name = os.path.basename(path)
        if os.path.isdir(path):
            return f"{name} : les dossiers ne peuvent pas être joints."
        if not os.path.isfile(path):
            return f"{name} : fichier introuvable."
        ext = os.path.splitext(path)[1].lower()
        att = {"id": uuid.uuid4().hex[:10], "name": name, "path": path, "temp": temp}
        if ext in IMAGE_EXTS:
            att.update(type="image", thumb=_thumbnail(path))
        else:
            if os.path.getsize(path) > MAX_TEXT_BYTES:
                return f"{name} : fichier trop volumineux (1 Mo maximum)."
            try:
                att.update(type="text", content=_read_text_file(path))
            except Exception as e:
                return f"{name} : {e}"
        with self._lock:
            tab.pending.append(att)
        return None

    def attach_paths(self, tab_id: str, paths: list[str]) -> dict:
        tab = self._find(tab_id)
        if not tab:
            return {"errors": ["Conversation introuvable."]}
        errors = [e for e in (self._add_file(tab, p) for p in paths or []) if e]
        self._emit_attachments(tab)
        return {"errors": errors}

    def detach(self, tab_id: str, att_id: str):
        tab = self._find(tab_id)
        if not tab:
            return
        with self._lock:
            removed = [a for a in tab.pending if a["id"] == att_id]
            tab.pending = [a for a in tab.pending if a["id"] != att_id]
        for a in removed:
            self._delete_temp(a)
        self._emit_attachments(tab)

    def _discard_pending(self, tab: ChatTab):
        with self._lock:
            pending, tab.pending = tab.pending, []
        for a in pending:
            self._delete_temp(a)
        self._emit_attachments(tab)

    @staticmethod
    def _delete_temp(att: dict):
        if att.get("temp"):
            try:
                os.unlink(att["path"])
            except Exception:
                pass

    def paste(self, tab_id: str) -> bool:
        """Colle une image ou des fichiers du presse-papiers comme pièces jointes.
        Retourne False s'il n'y a rien de tel (le collage texte normal s'applique)."""
        tab = self._find(tab_id)
        if not tab:
            return False
        try:
            from PIL import ImageGrab, Image
            grabbed = ImageGrab.grabclipboard()
        except Exception:
            return False
        if isinstance(grabbed, list):
            files = [p for p in grabbed if os.path.isfile(p)]
            if not files:
                return False
            errors = [e for e in (self._add_file(tab, p) for p in files) if e]
            self._emit_attachments(tab)
            if errors:
                self._bridge.emit("toast", {"text": errors[0], "kind": "error"})
            return True
        if isinstance(grabbed, Image.Image):
            path = str(CACHE_DIR / f"boostache_paste_{int(time.time() * 1000)}.png")
            grabbed.save(path, "PNG")
            self._add_file(tab, path, temp=True)
            self._emit_attachments(tab)
            return True
        return False

    # ── Copie des messages ────────────────────
    def _message(self, tab_id: str, index: int) -> dict | None:
        """Message à la position `index` de la liste affichée par l'interface."""
        tab = self._find(tab_id)
        if not tab:
            return None
        with self._lock:
            visible = [m for m in tab.messages if _ui_message(m)]
        return visible[index] if 0 <= index < len(visible) else None

    def _message_images(self, m: dict) -> list:
        images = []
        for ref in m.get("images") or []:
            try:
                images.append(_load_image_ref(ref))
            except Exception as e:
                logger.log(f"Image d'un message illisible : {e}")
        return images

    def copy_message(self, tab_id: str, index: int) -> bool:
        """Copie un message envoyé : son texte et ses images en pleine résolution."""
        m = self._message(tab_id, index)
        if not m:
            return False
        text = _ui_message(m)["text"]
        images = self._message_images(m)
        if not images:
            return set_clipboard_content(text=text)
        return set_clipboard_content(text=text, html=_message_html(text, images), image=images[0])

    def message_image(self, tab_id: str, index: int, k: int):
        """k-ième image d'un message (PIL), ou None."""
        m = self._message(tab_id, index)
        refs = (m or {}).get("images") or []
        if not 0 <= k < len(refs):
            return None
        try:
            return _load_image_ref(refs[k])
        except Exception:
            return None

    def copy_image(self, tab_id: str, index: int, k: int) -> bool:
        image = self.message_image(tab_id, index, k)
        return set_clipboard_image(image) if image is not None else False

    def screenshot(self, tab_id: str):
        self._snipper.request(lambda image, error: self._on_screenshot(tab_id, image, error))

    def _on_screenshot(self, tab_id: str, image, error):
        if error:
            self._bridge.emit("toast", {"text": f"Capture échouée : {error}", "kind": "error"})
            return
        if image is None:
            self._bridge.emit("toast", {"text": "Capture d'écran annulée."})
            return
        tab = self._find(tab_id)
        if not tab:
            return
        path = str(CACHE_DIR / f"boostache_screenshot_{int(time.time() * 1000)}.png")
        image.save(path, "PNG")
        self._add_file(tab, path, temp=True)
        self._emit_attachments(tab)
