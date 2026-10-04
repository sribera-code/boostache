"""
webpane.py – Sites web intégrés à la fenêtre (WhatsApp Web, Gmail).

Ces sites refusent d'être affichés dans une iframe : chacun tourne dans un
contrôle WebView2 posé sur la fenêtre, au-dessus de la zone réservée par sa
vue dans l'interface (qui transmet sa position). Chaque site a son propre
profil persistant (DATA_DIR/<clé>) : la session reste ouverte d'un lancement
à l'autre.

Créé à la première ouverture de la section, le contrôle reste ensuite chargé
(masqué) pour que le nombre de non-lus reste à jour.

Assistant de réponse : la conversation ouverte est lue dans la page, envoyée
à un modèle Ollama local, et le texte choisi est placé dans la zone de saisie
du site — jamais envoyé automatiquement. Les sous-classes (whatsapp.py,
gmail.py) fournissent la lecture de la page, les consignes et l'insertion.
"""

import json
import threading
import time
from urllib.parse import urlparse

from chat import NUM_CTX, _ollama
from engine import logger
from storage import DATA_DIR
from winutil import open_url

BACKGROUND_RGB = (22, 22, 22)

# Les raccourcis de navigation de Boostache restent actifs quand le site a le
# focus ; un clic dans le site en fait le volet actif (écran partagé)
SHORTCUTS_JS = r"""
(() => {
  if (window.top !== window) return;
  const post = (msg) => window.chrome.webview.postMessage(msg);
  addEventListener("keydown", (ev) => {
    let key = "";
    if (ev.ctrlKey && !ev.altKey && !ev.shiftKey && /^[1-9,]$/.test(ev.key)) key = ev.key;
    else if (ev.ctrlKey && ev.shiftKey && !ev.altKey && ev.key.toLowerCase() === "s") key = "split";
    else if (ev.key === "F6" && !ev.ctrlKey && !ev.altKey && !ev.shiftKey) key = "F6";
    if (!key) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    post({ type: "key", key });
  }, true);
  addEventListener("focus", () => post({ type: "focus" }));
})();
"""
RELAYED_KEYS = (*"123456789,", "split", "F6")

STYLE_RULES = (
    "N'utilise que l'alphabet et la langue des messages (aucun mot ni caractère d'une autre langue). "
    "Évite les formes du type « désolé(e) » : si le genre de l'utilisateur n'est pas connu, tourne la "
    "phrase autrement. "
)
SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {"replies": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3}},
    "required": ["replies"],
}
IMPROVE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
QUOTES = "\"'«»“”‘’ "


def clean(text) -> str:
    """Réponse du modèle sans guillemets ni espaces autour."""
    return str(text or "").strip().strip(QUOTES).strip()


def same_text(a, b) -> bool:
    """Égalité aux espaces près (l'éditeur du site peut les normaliser)."""
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


class WebPane:
    """Contrôle WebView2 d'un site. Les méthodes publiques sont appelables
    depuis n'importe quel thread ; tout le travail WinForms est renvoyé au
    thread de l'interface."""

    key = ""                 # préfixe des événements, dossier du profil
    name = ""                # nom affiché dans les messages
    url = ""
    hosts: tuple = ()        # domaines autorisés en navigation (et leurs sous-domaines)
    read_js = ""             # lecture de la conversation ouverte : { ok, …, draft }

    def __init__(self, app):
        self._app = app
        self._view = None             # contrôle WebView2 (thread de l'interface)
        self._state = "idle"          # idle | loading | ready | error
        self._message = ""
        self._unread = 0
        self._bounds = None
        self._wanted = False          # la vue du site est affichée dans l'interface
        self._focus = False           # donner le clavier au site à son affichage
        self._busy = threading.Lock()  # une génération à la fois

    def snapshot(self) -> dict:
        return {"state": self._state, "message": self._message, "unread": self._unread}

    # ─────────────────────────────────────────
    #  À préciser par site
    # ─────────────────────────────────────────
    def allowed_host(self, host: str) -> bool:
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def unread_from_title(self, title: str) -> int:
        return self._unread

    def insert(self, text: str) -> dict:
        """Place le texte dans la zone de saisie du site, sans l'envoyer."""
        raise NotImplementedError

    def _suggest(self, model: str, page: dict, hint: str) -> dict:
        raise NotImplementedError

    def _improve(self, model: str, page: dict, hint: str) -> dict:
        raise NotImplementedError

    # ─────────────────────────────────────────
    #  API (appelée depuis l'interface)
    # ─────────────────────────────────────────
    def show(self, x: int, y: int, width: int, height: int, focus: bool = False):
        self._bounds = (int(x), int(y), max(1, int(width)), max(1, int(height)))
        self._wanted = True
        self._focus = self._focus or bool(focus)
        self._ui(self._apply)

    def hide(self):
        self._wanted = False
        self._ui(self._apply)

    def reload(self):
        self._ui(self._reload)

    def suggest(self, model: str, hint: str = "") -> dict:
        """Trois propositions de réponse à la conversation ouverte."""
        return self._generate(model, hint, self._suggest)

    def improve(self, model: str, hint: str = "") -> dict:
        """Réécrit le brouillon de la zone de saisie."""
        return self._generate(model, hint, self._improve)

    # ─────────────────────────────────────────
    #  Assistant (threads de l'API, jamais celui de l'interface)
    # ─────────────────────────────────────────
    def _generate(self, model: str, hint: str, fn) -> dict:
        chat = self._app.chat
        if chat is None or not chat.available:
            return {"ok": False, "error": "Ollama est indisponible."}
        if model not in chat.models:
            model = chat.models[0] if chat.models else ""
        if not model:
            chat.check()            # lancé depuis le dernier test ?
            return {"ok": False, "error": chat.models_error or "Aucun modèle Ollama installé."}
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "Une génération est déjà en cours."}
        try:
            page = self._eval(self.read_js)
            if not page.get("ok"):
                return page
            return fn(model, page, (hint or "").strip()[:500])
        except Exception as e:
            logger.log(f"{self.name} : assistant ({model}) en échec : {e}")
            return {"ok": False, "error": f"Échec de la génération : {chat.failure(e)}"}
        finally:
            self._busy.release()

    def _improved(self, model: str, draft: str, text) -> dict:
        """Fin commune d'« Améliorer le brouillon » : insertion du texte du modèle."""
        text = clean(text)
        if not text:
            return {"ok": False, "error": "Le modèle n'a rien proposé."}
        res = self.insert(text)
        if not res.get("ok"):
            return res
        return {"ok": True, "text": text, "draft": draft, "model": model}

    @staticmethod
    def _ask(model: str, system: str, prompt: str, schema: dict) -> dict:
        resp = _ollama.chat(model=model, format=schema, think=False,
                            options={"temperature": 0.7, "num_ctx": NUM_CTX},
                            messages=[{"role": "system", "content": system},
                                      {"role": "user", "content": prompt}])
        data = json.loads(resp.message.content or "{}")
        return data if isinstance(data, dict) else {}

    def _focus_page(self):
        """Donne le clavier au site (le clic dans la barre de l'assistant l'a
        donné à l'interface) : sans lui, les éditeurs ignorent execCommand."""
        self._ui(lambda: self._view is not None and self._view.Focus())
        for _ in range(20):
            if self._eval("({ ok: document.hasFocus() })").get("ok"):
                return
            time.sleep(0.05)

    def _eval(self, script: str, timeout: float = 8.0) -> dict:
        """Exécute un script dans la page et retourne son résultat
        (attend la réponse : à ne pas appeler depuis le thread de l'interface)."""
        if self._view is None or self._state != "ready":
            return {"ok": False, "error": f"{self.name} n'est pas chargé."}
        done = threading.Event()
        box = {}

        def start():
            from System import Action, String
            from System.Threading.Tasks import Task

            def finish(task):
                try:
                    box["result"] = json.loads(task.Result)
                except Exception as e:
                    box["error"] = e
                done.set()

            try:
                self._view.CoreWebView2.ExecuteScriptAsync(script).ContinueWith(Action[Task[String]](finish))
            except Exception as e:     # contrôle fermé entre-temps
                box["error"] = e
                done.set()

        self._ui(start)
        if not done.wait(timeout):
            return {"ok": False, "error": f"{self.name} ne répond pas."}
        result = box.get("result")
        if not isinstance(result, dict):
            logger.log(f"{self.name} : lecture de la page impossible ({box.get('error') or result!r})")
            return {"ok": False, "error": f"Lecture de la page {self.name} impossible."}
        return result

    # ─────────────────────────────────────────
    #  Thread de l'interface
    # ─────────────────────────────────────────
    def _ui(self, fn):
        form = getattr(self._app.window, "native", None)
        if form is None:
            return
        try:
            from System import Action

            def run():
                try:
                    fn()
                except Exception as e:
                    logger.log(f"{self.name} : erreur ({e})")

            form.BeginInvoke(Action(run))
        except Exception as e:
            logger.log(f"{self.name} : appel impossible ({e})")

    def _apply(self):
        if self._wanted and self._view is None and self._state != "error":
            self._create()
        view = self._view
        if view is None:
            return
        if self._bounds:
            from System.Drawing import Rectangle
            view.Bounds = Rectangle(*self._bounds)
        visible = self._wanted and self._state == "ready"
        if view.Visible != visible:
            if not visible and view.ContainsFocus:
                # Rend le clavier à l'interface avant de masquer : sinon WinForms
                # le donne au premier élément tabulable de la page.
                self._app.window.native.browser.webview.Focus()
            view.Visible = visible
            if visible:
                view.BringToFront()
        if visible and self._focus:
            self._focus = False
            view.Focus()

    def _create(self):
        # Charge les assemblies WebView2 embarquées par pywebview
        from webview.platforms import edgechromium  # noqa: F401
        import System.Windows.Forms as WinForms
        from System.Drawing import Color
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2

        profile = DATA_DIR / self.key
        profile.mkdir(parents=True, exist_ok=True)
        view = WebView2()
        props = CoreWebView2CreationProperties()
        props.UserDataFolder = str(profile)
        props.AdditionalBrowserArguments = "--disable-features=ElasticOverscroll"
        view.CreationProperties = props
        view.DefaultBackgroundColor = Color.FromArgb(255, *BACKGROUND_RGB)
        view.Visible = False
        A = WinForms.AnchorStyles
        view.Anchor = A.Top | A.Bottom | A.Left | A.Right
        view.CoreWebView2InitializationCompleted += self._on_initialized

        self._app.window.native.Controls.Add(view)
        self._view = view
        self._set_state("loading")
        view.EnsureCoreWebView2Async(None)

    def _on_initialized(self, sender, args):
        if not args.IsSuccess:
            err = args.InitializationException
            self._set_state("error", f"WebView2 n'a pas pu démarrer : {err.Message if err else 'erreur inconnue'}")
            self._dispose()
            return
        core = self._view.CoreWebView2
        s = core.Settings
        s.AreDevToolsEnabled = bool(self._app.debug)
        s.IsStatusBarEnabled = False
        core.NewWindowRequested += self._on_new_window
        core.NavigationStarting += self._on_navigation
        core.PermissionRequested += self._on_permission
        core.DocumentTitleChanged += self._on_title
        core.WebMessageReceived += self._on_message
        core.ProcessFailed += self._on_process_failed
        core.AddScriptToExecuteOnDocumentCreatedAsync(SHORTCUTS_JS)
        core.Navigate(self.url)
        self._set_state("ready")
        self._apply()

    def _reload(self):
        if self._view is not None and self._state == "ready":
            self._view.CoreWebView2.Reload()
            return
        # Premier chargement raté ou processus arrêté : on repart de zéro
        self._dispose()
        self._set_state("idle")
        self._apply()

    def _dispose(self):
        view, self._view = self._view, None
        if view is None:
            return
        try:
            self._app.window.native.Controls.Remove(view)
            view.Dispose()
        except Exception:
            pass

    # ─────────────────────────────────────────
    #  Événements WebView2 (thread de l'interface)
    # ─────────────────────────────────────────
    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https"):
            return True          # about:blank, blob:, data:… : internes à la page
        return self.allowed_host((parsed.hostname or "").lower())

    def _on_new_window(self, sender, args):
        # Liens des messages (target=_blank) : navigateur par défaut
        args.Handled = True
        open_url(args.Uri)

    def _on_navigation(self, sender, args):
        if not self._allowed(args.Uri):
            args.Cancel = True
            open_url(args.Uri)

    def _on_permission(self, sender, args):
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind as Kind
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState as State
        # Micro et caméra : demande habituelle de WebView2 (mémorisée dans le profil)
        if args.PermissionKind in (Kind.Notifications, Kind.ClipboardRead) and self._allowed(args.Uri):
            args.State = State.Allow

    def _on_title(self, sender, args):
        unread = self.unread_from_title(sender.DocumentTitle or "")
        if unread != self._unread:
            self._unread = unread
            self._app.bridge.emit(f"{self.key}:unread", {"count": unread})

    def _on_message(self, sender, args):
        try:
            msg = json.loads(args.WebMessageAsJson)
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "key" and msg.get("key") in RELAYED_KEYS:
            self._app.bridge.emit("webpane:key", {"key": msg["key"]})
        elif msg.get("type") == "focus":
            self._app.bridge.emit("webpane:focus", {"pane": self.key})

    def _on_process_failed(self, sender, args):
        kind = str(args.ProcessFailedKind)
        if kind not in ("BrowserProcessExited", "RenderProcessExited"):
            return           # GPU, utilitaires… : WebView2 les relance seul
        logger.log(f"{self.name} : processus WebView2 arrêté ({kind}).")
        self._set_state("error", f"{self.name} s'est arrêté de façon inattendue.")
        self._apply()

    def _set_state(self, state: str, message: str = ""):
        self._state, self._message = state, message
        if message:
            logger.log(f"{self.name} : {message}")
        self._app.bridge.emit(f"{self.key}:state", {"state": state, "message": message})
