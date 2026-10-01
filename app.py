"""
app.py – Fenêtre principale de Boostache (interface web via pywebview/WebView2).

Assemble les services (conversations, consoles, notes, captures,
presse-papiers…), gère le cycle de vie de la fenêtre (masquée au lieu d'être
fermée, rappelée depuis le tray ou un raccourci) et fournit l'état initial à
l'interface.
"""

import sys
import threading
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

import custom_tasks
from api import Api
from bridge import Bridge
from captures import CapturesService, Snipper
from chat import ChatService, OLLAMA_OK, ollama_server_running
from clipboard_listener import ClipboardListener
from engine import ICON_PATH, logger, tts, hotkey_manager
from gmail import GmailPane
from notes import NotesService
from storage import DATA_DIR, settings, clipboard_history
from terminals import TerminalService
from whatsapp import WhatsAppPane
from winutil import (PrintScreenHook, bring_to_front, dark_title_bar, is_maximized, is_minimized,
                     restore_window, work_area_size)

WEB_DIR = Path(__file__).resolve().parent / "web"
VIRTUAL_HOST = "boostache.example"
MIN_SIZE = (760, 520)
TITLEBAR_RGB = (20, 20, 20)
PREVIEW_LEN = 2000
PRINT_SCREEN = "print screen"


def _force_dark_titlebar():
    """pywebview aligne la barre de titre sur le thème Windows ; l'interface
    étant toujours sombre, on la force en sombre."""
    try:
        from webview.platforms import winforms
        winforms.BrowserView.BrowserForm.is_dark_theme = lambda self: True
    except Exception:
        pass


def _interface_url() -> str:
    """URL de l'interface.

    WebView2 sert le dossier web/ directement depuis le disque via un hôte
    virtuel (https://boostache.example/), sans serveur HTTP : le serveur
    intégré de pywebview perd des requêtes quand le navigateur charge les
    modules en rafale. Repli sur un serveur local si l'accroche échoue."""
    try:
        from webview.platforms import edgechromium
        from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind

        original = edgechromium.EdgeChrome.on_webview_ready

        def on_webview_ready(self, sender, args):
            if args.IsSuccess:
                sender.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    VIRTUAL_HOST, str(WEB_DIR), CoreWebView2HostResourceAccessKind.Allow)
            return original(self, sender, args)

        edgechromium.EdgeChrome.on_webview_ready = on_webview_ready
        return f"https://{VIRTUAL_HOST}/index.html"
    except Exception as e:
        logger.log(f"Hôte virtuel WebView2 indisponible ({e}) : serveur local utilisé.")
        return _start_local_server()


def _start_local_server() -> str:
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        request_queue_size = 128
        daemon_threads = True

    server = Server(("127.0.0.1", 0), partial(Handler, directory=str(WEB_DIR)))
    threading.Thread(target=server.serve_forever, daemon=True, name="ui-http").start()
    return f"http://127.0.0.1:{server.server_address[1]}/index.html"


class BoostacheApp:
    def __init__(self, debug: bool = False, show_on_start: bool = False):
        self.debug = debug
        self.show_on_start = show_on_start
        self.window = None
        self._hwnd = 0
        self._visible = False
        self._was_minimized = False
        self._quitting = False
        self._size = tuple(settings.get("window_size", [980, 680]))
        self._size_timer: threading.Timer | None = None

        self.bridge = Bridge()
        logger.subscribe(self._on_log)
        tts.subscribe(lambda speaking, source: self.bridge.emit(
            "tts", {"speaking": speaking, "source": source}))

        self.snipper = Snipper(hooks=self)
        if OLLAMA_OK and ollama_server_running():
            self.chat = ChatService(self.bridge, self.snipper)
            self._chat_reason = ""
        else:
            self.chat = None
            self._chat_reason = ("librairie 'ollama' absente (pip install ollama)"
                                 if not OLLAMA_OK else "serveur Ollama injoignable")
            logger.log(f"Onglet Conversations masqué : {self._chat_reason}.")
        self.terminals = TerminalService(self.bridge)
        self.notes = NotesService()
        self.captures = CapturesService(self.bridge, self.snipper, show_window=self.show)
        # Sites intégrés, par clé (whatsapp, gmail)
        self.panes = {p.key: p for p in (WhatsAppPane(self), GmailPane(self))}
        self._clip_listener: ClipboardListener | None = None
        self._print_screen: PrintScreenHook | None = None
        self.api = Api(self)

    # ─────────────────────────────────────────
    #  Démarrage
    # ─────────────────────────────────────────
    def run(self):
        """Crée la fenêtre et lance la boucle d'interface (bloquant)."""
        custom_tasks.load_persisted()
        self._start_clipboard_listener()
        self._set_print_screen(bool(settings.get("print_screen_capture", True)))
        _force_dark_titlebar()

        w, h = self._initial_size()
        self.window = webview.create_window(
            "Boostache",
            url=_interface_url(),
            js_api=self.api,
            width=w,
            height=h,
            min_size=MIN_SIZE,
            hidden=True,
            on_top=bool(settings.get("always_on_top", True)),
            background_color="#161616",
            text_select=True,
        )
        self.bridge.attach(self.window)
        ev = self.window.events
        ev.before_show += self._on_before_show
        ev.closing += self._on_closing
        ev.minimized += self._on_minimized
        ev.resized += self._on_resized
        ev.loaded += self._on_loaded
        webview.start(self._after_start, gui="edgechromium", debug=self.debug,
                      icon=ICON_PATH, private_mode=True)
        self._shutdown()

    def _initial_size(self) -> tuple[int, int]:
        """Taille enregistrée, ramenée dans l'écran s'il est plus petit que celui
        où elle a été choisie (autre moniteur, bureau à distance…)."""
        w, h = int(self._size[0]), int(self._size[1])
        area = work_area_size()
        if area:
            w, h = min(w, int(area[0] * .95)), min(h, int(area[1] * .95))
        return max(MIN_SIZE[0], w), max(MIN_SIZE[1], h)

    def _after_start(self):
        if self.show_on_start:
            self.window.events.loaded.wait(20)
            self.show()

    def _start_clipboard_listener(self):
        try:
            self._clip_listener = ClipboardListener(self._on_clipboard_update)
            self._clip_listener.start()
        except Exception as e:
            logger.log(f"Clipboard listener indisponible : {e}")
            self._clip_listener = None

    def _set_print_screen(self, enabled: bool):
        """Impr. écran → capture d'une zone, ouverte dans l'onglet Captures."""
        if enabled and not self._print_screen:
            hook = PrintScreenHook(self.captures.snip)
            if not hook.start():
                logger.log("Impr. écran : interception de la touche impossible.")
                return
            self._print_screen = hook
            hotkey_manager.list_external(PRINT_SCREEN, self.captures.snip,
                                         label="Capturer une zone de l'écran (onglet Captures)")
        elif not enabled and self._print_screen:
            self._print_screen.stop()
            self._print_screen = None
            hotkey_manager.unlist(PRINT_SCREEN)
            logger.log("Impr. écran rendue à Windows.")

    # ─────────────────────────────────────────
    #  Événements fenêtre
    # ─────────────────────────────────────────
    def _on_before_show(self, window):
        # Thread de l'interface : on peut lire le handle natif sans risque
        try:
            self._hwnd = int(window.native.Handle.ToInt64())
        except Exception:
            self._hwnd = 0
        dark_title_bar(self._hwnd, TITLEBAR_RGB)

    def _on_closing(self):
        # Exécuté dans le thread de l'interface : ne rien bloquer ici
        if self._quitting:
            return True
        threading.Thread(target=self.hide, daemon=True).start()
        return False

    def _on_minimized(self):
        # Réduire = ranger dans le tray (restauration native au prochain affichage)
        self._was_minimized = True
        self.hide()

    def _on_resized(self, width, height):
        # Taille normale seulement : agrandie, la fenêtre rouvrirait en plein
        # écran sans l'être vraiment
        if (self._visible and not is_minimized(self._hwnd) and not is_maximized(self._hwnd)
                and width >= MIN_SIZE[0] and height >= MIN_SIZE[1]):
            self._size = (width, height)
            # Enregistrée dès la fin du redimensionnement : un « Recharger » (le
            # nouveau processus lit les réglages avant que l'ancien ne ferme) ou
            # une fermeture de session Windows ne la perdent plus
            if self._size_timer:
                self._size_timer.cancel()
            self._size_timer = threading.Timer(1.0, self._save_size)
            self._size_timer.daemon = True
            self._size_timer.start()

    def _on_loaded(self):
        try:
            self.window.dom.document.events.drop += DOMEventHandler(
                self._on_drop, prevent_default=True)
        except Exception as e:
            logger.log(f"Glisser-déposer indisponible : {e}")

    def _on_drop(self, event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            self.bridge.emit("files:dropped", {"paths": paths})

    # ─────────────────────────────────────────
    #  Affichage (appelable depuis n'importe quel thread)
    # ─────────────────────────────────────────
    def is_visible(self) -> bool:
        return self._visible

    def show(self):
        if not self.window or self._quitting:
            return
        try:
            self.window.show()
            if self._was_minimized or is_minimized(self._hwnd):
                restore_window(self._hwnd)
                self._was_minimized = False
            self._visible = True
            bring_to_front(self._hwnd)
            self.bridge.emit("window:shown")
        except Exception as e:
            logger.log(f"Affichage de la fenêtre impossible : {e}")

    def hide(self):
        if not self.window:
            return
        self.bridge.emit("window:hidden")
        try:
            self.window.hide()
        except Exception:
            pass
        self._visible = False
        self._save_size()

    def quit(self):
        """Ferme proprement l'application (depuis le tray)."""
        if self._quitting or not self.window:
            return
        self._collect_ui_state()
        self._quitting = True
        try:
            self.window.destroy()
        except Exception:
            pass

    def _collect_ui_state(self):
        """Récupère ce que l'interface n'a pas encore enregistré (notes en cours
        de frappe, contenu des terminaux) avant de fermer."""
        try:
            state = self.window.evaluate_js(
                "window.__boostache ? window.__boostache.collectState() : null")
        except Exception:
            state = None
        if not isinstance(state, dict):
            return
        for slot, content in (state.get("notes") or {}).items():
            self.notes.save(int(slot), content)
        for slot, buffer in (state.get("terminals") or {}).items():
            self.terminals.save_buffer(int(slot), buffer)
        for slot, images in (state.get("captures") or {}).items():
            try:
                self.captures.save(int(slot), images.get("image"), images.get("orig"))
            except Exception as e:
                logger.log(f"Capture {slot} non enregistrée : {e}")

    def _set_on_top(self, value: bool):
        """window.on_top de pywebview modifie la fenêtre WinForms depuis le
        thread appelant (non sûr, blocages possibles) : on passe par Invoke."""
        form = getattr(self.window, "native", None)
        if form is None:
            return
        try:
            from System import Func, Type

            def apply():
                form.TopMost = value
                return None

            form.Invoke(Func[Type](apply))
        except Exception as e:
            logger.log(f"Premier plan : réglage impossible ({e})")

    def _save_size(self):
        w, h = self._size
        if list(settings.get("window_size", [])) != [w, h]:
            settings.set("window_size", [w, h])

    def _shutdown(self):
        self._save_size()
        if self._print_screen:
            self._print_screen.stop()
        if self._clip_listener:
            try:
                self._clip_listener.stop()
            except Exception:
                pass
        self.terminals.shutdown()
        try:
            tts.stop()
        except Exception:
            pass
        self.bridge.stop()

    # ─────────────────────────────────────────
    #  Données envoyées à l'interface
    # ─────────────────────────────────────────
    def ui_ready(self) -> dict:
        self.bridge.discard_pending()
        state = {
            "settings": {k: settings.get(k) for k in (
                "system_prompt", "clipboard_max_items", "always_on_top", "console_shell",
                "tts_mode_chat", "tts_mode_console", "tts_mode_note", "sidebar_collapsed",
                "print_screen_capture", "assist_model")},
            "data_dir":  str(DATA_DIR),
            "chat":      self.chat.snapshot() if self.chat else
                         {"available": False, "reason": self._chat_reason},
            "consoles":  self.terminals.snapshot(),
            "notes":     self.notes.snapshot(),
            "captures":  self.captures.snapshot(),
            "panes":     {key: p.snapshot() for key, p in self.panes.items()},
            "clipboard": self.clipboard_items(),
            "logs":      logger.recent(),
            "tasks":     custom_tasks.rows(),
            "hotkeys":   custom_tasks.hotkey_rows(),
            "tts":       {"speaking": tts.speaking, "source": None},
            "visible":   self._visible,
            "win_build": sys.getwindowsversion().build,
        }
        self.bridge.set_ready(True)
        return state

    def set_setting(self, key: str, value):
        if key == "clipboard_max_items":
            try:
                value = max(1, min(int(value), 10000))
            except (TypeError, ValueError):
                return settings.get(key)
            settings.set(key, value)
            clipboard_history.trim(value)
            self.emit_clipboard()
        elif key == "always_on_top":
            value = bool(value)
            settings.set(key, value)
            self._set_on_top(value)
        elif key == "print_screen_capture":
            value = bool(value)
            settings.set(key, value)
            self._set_print_screen(value)
        elif key == "console_shell":
            if value not in self.terminals.shells:
                return settings.get(key)
            settings.set(key, value)
        elif key in ("tts_mode_chat", "tts_mode_console", "tts_mode_note"):
            if value not in ("last", "all", "sel"):
                return settings.get(key)
            settings.set(key, value)
        elif key in ("system_prompt", "sidebar_collapsed", "assist_model"):
            settings.set(key, value)
        else:
            return None
        return settings.get(key)

    def clipboard_items(self) -> list[dict]:
        items = []
        for it in clipboard_history.all():
            text = it.get("text", "")
            items.append({
                "id":      it["id"],
                "ts":      it.get("ts", ""),
                "preview": text[:PREVIEW_LEN],
                "length":  len(text),
                "lines":   text.count("\n") + 1,
            })
        return items

    def emit_clipboard(self):
        self.bridge.emit("clipboard", {"items": self.clipboard_items()})

    def _on_clipboard_update(self, text: str):
        """Appelé depuis le thread Win32 du listener."""
        try:
            max_items = int(settings.get("clipboard_max_items", 100))
        except Exception:
            max_items = 100
        if clipboard_history.add(text, max_items=max_items):
            self.emit_clipboard()

    def _on_log(self, line: str):
        self.bridge.emit("log", {"seq": logger.last_seq, "line": line})
