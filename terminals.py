"""
terminals.py – Onglet Consoles : vrais terminaux (ConPTY via pywinpty),
affichés par xterm.js dans l'interface.

Chaque onglet fait tourner un shell interactif (PowerShell ou cmd). Le shell
publie son répertoire courant dans le titre de la console
("boostache-cwd:<chemin>"), ce qui permet de le suivre, de le restaurer au
prochain démarrage et d'ouvrir un terminal externe au bon endroit.
"""

import base64
import os
import shutil
import subprocess
import threading
import time

from engine import logger
from storage import console_store, settings

try:
    from winpty import PtyProcess
    PTY_OK = True
except ImportError:
    PtyProcess = None
    PTY_OK = False


TITLE_MARK = "boostache-cwd:"
HOME = os.path.expanduser("~")

# Enveloppe le prompt existant (profil utilisateur, oh-my-posh…) pour publier
# le répertoire courant dans le titre, sans changer l'apparence du prompt.
_PS_INIT = r"""
$global:__bst_prompt = $function:prompt
function global:prompt {
    $p = if ($global:__bst_prompt) { & $global:__bst_prompt } else { "PS $($executionContext.SessionState.Path.CurrentLocation)> " }
    $loc = $executionContext.SessionState.Path.CurrentLocation
    if ($loc.Provider.Name -eq 'FileSystem') { $Host.UI.RawUI.WindowTitle = 'boostache-cwd:' + $loc.ProviderPath }
    $p
}
"""

# Styles de l'ancien format (segments taggés) → séquences ANSI
_LEGACY_STYLE = {
    "prompt_tag": "\x1b[90m",
    "cmd_tag":    "\x1b[1;92m",
    "err_tag":    "\x1b[91m",
    "sys_tag":    "\x1b[3;90m",
    "stdin_tag":  "\x1b[3;37m",
    "out_tag":    "",
}


def available_shells() -> dict[str, str]:
    shells = {"powershell": "PowerShell"}
    if shutil.which("pwsh"):
        shells["pwsh"] = "PowerShell 7"
    shells["cmd"] = "Invite de commandes"
    return shells


def _legacy_to_ansi(data: dict) -> str:
    """Convertit le contenu d'une console de l'ancienne interface en texte ANSI."""
    segments = data.get("segments")
    if segments:
        text = "".join(_LEGACY_STYLE.get(s.get("g"), "") + s.get("t", "") + "\x1b[0m"
                       for s in segments if isinstance(s, dict))
    else:
        text = data.get("output") or ""
    if not text.strip():
        return ""
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _resolve_title_path(title: str) -> str | None:
    """Extrait le répertoire d'un titre "boostache-cwd:<chemin>".
    cmd suffixe le titre avec " - <commande>" pendant l'exécution : on
    retient le plus long préfixe qui est un dossier existant."""
    idx = title.find(TITLE_MARK)
    if idx < 0:
        return None
    raw = title[idx + len(TITLE_MARK):].strip().strip('"')
    if os.path.isdir(raw):
        return os.path.normpath(raw)
    pos = raw.rfind(" - ")
    while pos > 0:
        candidate = raw[:pos]
        if os.path.isdir(candidate):
            return os.path.normpath(candidate)
        pos = raw.rfind(" - ", 0, pos)
    return None


class Terminal:
    def __init__(self, slot: int, label: str = "", cwd: str = "",
                 shell: str = "powershell", buffer: str = ""):
        self.slot = slot
        self.label = label
        self.cwd = cwd if cwd and os.path.isdir(cwd) else HOME
        self.shell = shell
        self.buffer = buffer        # contenu sérialisé par xterm.js (restauration)
        self.proc = None
        self.session = 0            # incrémenté à chaque démarrage du shell
        self.lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None

    @property
    def title(self) -> str:
        if self.label:
            return self.label
        name = os.path.basename(self.cwd.rstrip("\\/"))
        return name or self.cwd

    def to_ui(self) -> dict:
        return {
            "slot":    self.slot,
            "label":   self.label,
            "title":   self.title,
            "cwd":     self.cwd,
            "shell":   self.shell,
            "running": self.running,
        }

    def to_json(self) -> dict:
        return {
            "slot":   self.slot,
            "label":  self.label,
            "cwd":    self.cwd,
            "shell":  self.shell,
            "buffer": self.buffer,
        }


class TerminalService:
    def __init__(self, bridge):
        self._bridge = bridge
        self._lock = threading.RLock()
        self._terms: list[Terminal] = []
        self._closing = False
        self.shells = available_shells()
        self._restore()

    # ─────────────────────────────────────────
    #  Restauration / persistance
    # ─────────────────────────────────────────
    def _default_shell(self) -> str:
        shell = settings.get("console_shell", "powershell")
        return shell if shell in self.shells else "powershell"

    def _restore(self):
        for slot in console_store.order():
            data = console_store.load(slot) or {}
            shell = data.get("shell") or self._default_shell()
            if shell not in self.shells:
                shell = self._default_shell()
            self._terms.append(Terminal(
                slot,
                label=data.get("label", ""),
                cwd=data.get("cwd", ""),
                shell=shell,
                buffer=data.get("buffer") or _legacy_to_ansi(data),
            ))
        if not self._terms:
            t = Terminal(self._next_slot(), shell=self._default_shell())
            self._terms.append(t)
            self._persist(t)
            self._save_order()

    def _next_slot(self) -> int:
        used = set(console_store.existing_slots()) | {t.slot for t in self._terms}
        return max(used, default=0) + 1

    def _persist(self, t: Terminal):
        console_store.save(t.slot, t.to_json())

    def _save_order(self):
        with self._lock:
            slots = [t.slot for t in self._terms]
        console_store.save_order(slots)

    def _find(self, slot: int) -> Terminal | None:
        with self._lock:
            return next((t for t in self._terms if t.slot == slot), None)

    # ─────────────────────────────────────────
    #  API
    # ─────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "available": PTY_OK,
                "tabs":      [t.to_ui() for t in self._terms],
                "shells":    self.shells,
            }

    def saved_buffer(self, slot: int) -> str:
        t = self._find(slot)
        return t.buffer if t else ""

    def new(self, after_slot: int | None = None) -> dict:
        with self._lock:
            t = Terminal(self._next_slot(), shell=self._default_shell())
            idx = next((i for i, x in enumerate(self._terms) if x.slot == after_slot), None)
            if idx is None:
                self._terms.append(t)
            else:
                self._terms.insert(idx + 1, t)
        self._persist(t)
        self._save_order()
        return t.to_ui()

    def close(self, slot: int) -> dict:
        """Ferme un onglet. Le dernier onglet est réinitialisé plutôt que fermé."""
        t = self._find(slot)
        if not t:
            return {"removed": False}
        self._kill(t)
        with self._lock:
            if len(self._terms) <= 1:
                t.label, t.buffer, t.cwd = "", "", HOME
                t.shell = self._default_shell()
                self._persist(t)
                return {"removed": False, "reset": t.to_ui()}
            self._terms.remove(t)
        console_store.delete(slot)
        self._save_order()
        return {"removed": True}

    def rename(self, slot: int, label: str) -> dict | None:
        t = self._find(slot)
        if not t:
            return None
        t.label = label.strip()
        self._persist(t)
        return t.to_ui()

    def set_shell(self, slot: int, shell: str) -> dict | None:
        """Change le shell d'un onglet (effectif au prochain démarrage du terminal)."""
        t = self._find(slot)
        if not t or shell not in self.shells:
            return None
        t.shell = shell
        self._persist(t)
        return t.to_ui()

    def start(self, slot: int, cols: int, rows: int) -> dict:
        if not PTY_OK:
            return {"ok": False, "error": "pywinpty n'est pas installé (pip install pywinpty)."}
        t = self._find(slot)
        if not t:
            return {"ok": False, "error": "Console introuvable."}
        cols, rows = max(20, int(cols)), max(5, int(rows))
        with t.lock:
            if t.proc is not None:
                self._safe_resize(t.proc, cols, rows)
                return {"ok": True, "session": t.session}
            argv, env = self._command(t.shell)
            cwd = t.cwd if os.path.isdir(t.cwd) else HOME
            try:
                proc = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
            except Exception as e:
                logger.log(f"Console {t.title} : démarrage impossible ({e})")
                return {"ok": False, "error": str(e)}
            t.proc = proc
            t.session += 1
            session = t.session
        threading.Thread(target=self._reader, args=(t, proc, session),
                         daemon=True, name=f"pty-{slot}").start()
        threading.Thread(target=self._watch, args=(t, proc, session),
                         daemon=True, name=f"pty-watch-{slot}").start()
        self._bridge.emit("term:tab", t.to_ui())
        return {"ok": True, "session": session}

    def write(self, slot: int, data: str):
        t = self._find(slot)
        proc = t.proc if t else None
        if proc is not None:
            try:
                proc.write(data)
            except Exception:
                pass

    def submit(self, slot: int, line: str):
        """Remplace la saisie en cours par `line` et la valide.
        Échap (efface la saisie, cmd comme PSReadLine) doit partir seul : suivi
        d'un caractère dans la même écriture, ConPTY le lit comme Alt+<car.>
        (« Set-Location » devenait « et-Location », « cls » devenait « ls »)."""
        self.write(slot, "\x1b")
        time.sleep(0.05)
        self.write(slot, line + "\r")

    def resize(self, slot: int, cols: int, rows: int):
        t = self._find(slot)
        if t and t.proc is not None:
            self._safe_resize(t.proc, max(20, int(cols)), max(5, int(rows)))

    def on_title(self, slot: int, title: str):
        """Titre émis par le shell : met à jour le répertoire courant."""
        path = _resolve_title_path(title or "")
        t = self._find(slot)
        if not path or not t or os.path.normcase(path) == os.path.normcase(t.cwd):
            return
        t.cwd = path
        self._persist(t)
        self._bridge.emit("term:tab", t.to_ui())

    def save_buffer(self, slot: int, buffer: str):
        t = self._find(slot)
        if t is None:
            return
        t.buffer = buffer or ""
        self._persist(t)

    def stop(self, slot: int):
        """Arrête le shell d'un onglet (sans le signaler comme terminé)."""
        t = self._find(slot)
        if t:
            self._kill(t)

    def change_dir(self, slot: int, path: str) -> bool:
        t = self._find(slot)
        if path and os.path.isfile(path):
            path = os.path.dirname(path)
        if not t or not path or not os.path.isdir(path):
            return False
        path = os.path.normpath(path)
        if t.proc is not None:
            if t.shell == "cmd":
                cmd = f'cd /d "{path}"'
            else:
                cmd = "Set-Location -LiteralPath '" + path.replace("'", "''") + "'"
            self.submit(slot, cmd)
        else:
            t.cwd = path
            self._persist(t)
            self._bridge.emit("term:tab", t.to_ui())
        return True

    def open_external(self, slot: int) -> bool:
        """Ouvre un vrai terminal Windows dans le répertoire de l'onglet."""
        t = self._find(slot)
        cwd = t.cwd if t and os.path.isdir(t.cwd) else HOME
        shell = t.shell if t else self._default_shell()
        try:
            if shutil.which("wt"):
                subprocess.Popen(["wt.exe", "-d", cwd])
            else:
                exe = {"powershell": "powershell.exe", "pwsh": "pwsh.exe"}.get(shell, "cmd.exe")
                subprocess.Popen([exe], cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            logger.log(f"Terminal externe ouvert dans {cwd}")
            return True
        except Exception as e:
            logger.log(f"Impossible d'ouvrir le terminal externe : {e}")
            return False

    def shutdown(self):
        """Ferme tous les shells (en parallèle, 2 s maximum)."""
        self._closing = True
        with self._lock:
            terms = list(self._terms)
        threads = [threading.Thread(target=self._kill, args=(t,), daemon=True) for t in terms]
        for th in threads:
            th.start()
        deadline = time.monotonic() + 2.0
        for th in threads:
            th.join(max(0.0, deadline - time.monotonic()))

    # ─────────────────────────────────────────
    #  Interne
    # ─────────────────────────────────────────
    def _command(self, shell: str) -> tuple[list[str], dict]:
        env = dict(os.environ)
        env["TERM_PROGRAM"] = "Boostache"
        if shell in ("powershell", "pwsh"):
            exe = "pwsh.exe" if shell == "pwsh" else "powershell.exe"
            enc = base64.b64encode(_PS_INIT.encode("utf-16-le")).decode("ascii")
            return [exe, "-NoLogo", "-NoExit", "-EncodedCommand", enc], env
        env["PROMPT"] = "$E]0;" + TITLE_MARK + "$P$E\\$P$G"
        return ["cmd.exe"], env

    @staticmethod
    def _safe_resize(proc, cols: int, rows: int):
        try:
            proc.setwinsize(rows, cols)
        except Exception:
            pass

    def _watch(self, t: Terminal, proc, session: int):
        """ConPTY ne signale pas la fin du shell (la pseudo-console reste
        ouverte et la lecture ne se débloque que bien plus tard) : on surveille
        le processus lui-même et on annonce la fin dès qu'il se termine."""
        while proc.isalive():
            time.sleep(0.2)
        time.sleep(0.15)   # laisse passer les dernières lignes de sortie
        code = None
        try:
            code = proc.exitstatus
        except Exception:
            pass
        with t.lock:
            current = t.proc is proc
            if current:
                t.proc = None
        # Un shell fermé volontairement (onglet fermé / réinitialisé) ne signale rien
        if current and not self._closing:
            self._bridge.emit("term:exit", {"slot": t.slot, "session": session, "code": code})
            self._bridge.emit("term:tab", t.to_ui())
        try:
            proc.close(force=True)
        except Exception:
            pass

    def _reader(self, t: Terminal, proc, session: int):
        while True:
            try:
                data = proc.read(65536)
            except EOFError:
                break
            except Exception:
                if not proc.isalive():
                    break
                time.sleep(0.02)
                continue
            if data:
                self._bridge.emit("term:data", {"slot": t.slot, "session": session, "data": data})

    @staticmethod
    def _kill(t: Terminal):
        with t.lock:
            proc, t.proc = t.proc, None
        if proc is not None:
            try:
                proc.close(force=True)
            except Exception:
                pass
