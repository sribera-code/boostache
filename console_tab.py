"""
console_tab.py – Onglet console (terminal léger intégré)
"""

import json
import os
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from engine import logger, tts
from storage import DATA_DIR
from theme import (
    BG2, BG3, BG4, BG_CHAT, BORDER, FG, FG_DIM, ACCENT,
)
from ui_utils import DND_OK, DND_FILES, parse_drop_data


# ── Répertoire de persistance des consoles ────────────────────────────────────
CONSOLES_DIR = DATA_DIR / "consoles"
CONSOLES_DIR.mkdir(parents=True, exist_ok=True)


class ConsoleTab:
    """Onglet terminal léger : champ CWD, zone de sortie, champ d'entrée."""

    _counter = 0

    def __init__(self, inner_notebook: ttk.Notebook, dashboard,
                 slot: int | None = None):
        ConsoleTab._counter += 1
        self.num   = ConsoleTab._counter
        self.slot  = slot if slot is not None else self.num
        self.dashboard = dashboard
        self._proc     = None
        self._cwd      = os.path.expanduser("~")
        self._history:  list[str] = []
        self._hist_idx: int       = -1      # -1 = pas en navigation
        self._label: str = ""               # Label personnalisé
        self._tts_active = False

        self.frame = ttk.Frame(inner_notebook)
        inner_notebook.add(self.frame, text="  ·  ")   # Pas de titre par défaut
        is_new = not self._slot_path().exists()
        self._build()
        self._restore()
        if is_new:
            self._persist()   # enregistre seulement si nouveau slot

    @property
    def root(self):
        return self.dashboard.root

    # ─────────────────────────────────────────
    #  Construction
    # ─────────────────────────────────────────
    def _build(self):
        # ── Zone de sortie ────────────────────
        out_frame = tk.Frame(self.frame, bg=BG_CHAT)
        out_frame.pack(fill="both", expand=True)

        self._out_box = tk.Text(
            out_frame, state="disabled",
            bg=BG_CHAT, fg=FG, font=("Consolas", 9),
            relief="flat", borderwidth=0, wrap="word",
            padx=10, pady=8,
            selectbackground="#4A7EBB", selectforeground="#FFFFFF")
        out_vsb = ttk.Scrollbar(out_frame, orient="vertical",
                                 command=self._out_box.yview)
        self._out_box.configure(yscrollcommand=out_vsb.set)
        out_vsb.pack(side="right", fill="y")
        self._out_box.pack(fill="both", expand=True)

        self._out_box.tag_configure("cmd_tag",  foreground="#8EC88E",
                                     font=("Consolas", 9, "bold"),
                                     spacing1=6, lmargin1=10)
        self._out_box.tag_configure("out_tag",  foreground=FG,
                                     font=("Consolas", 9),
                                     lmargin1=10, lmargin2=10)
        self._out_box.tag_configure("err_tag",  foreground="#E07070",
                                     font=("Consolas", 9),
                                     lmargin1=10, lmargin2=10)
        self._out_box.tag_configure("sys_tag",  foreground=FG_DIM,
                                     font=("Consolas", 8, "italic"),
                                     lmargin1=10)
        self._out_box.tag_configure("stdin_tag", foreground="#C8A8E8",
                                     font=("Consolas", 9, "italic"),
                                     lmargin1=10)

        self._out_box.bind("<Button-3>", self._out_context_menu)
        self._out_box.bind("<Control-c>", self._copy_selection)
        self._out_box.bind("<Control-C>", self._copy_selection)
        tk.Frame(self.frame, bg=BORDER, height=1).pack(fill="x")

        # ── Bloc bas : CWD + input ─────────────
        bottom = tk.Frame(self.frame, bg=BG2, pady=4)
        bottom.pack(fill="x")

        # Ligne 1 : répertoire courant
        cwd_row = tk.Frame(bottom, bg=BG2)
        cwd_row.pack(fill="x", padx=8, pady=(2, 1))

        tk.Label(cwd_row, text="dir ›", bg=BG2, fg=FG_DIM,
                 font=("Consolas", 8)).pack(side="left", padx=(0, 4))

        self._cwd_var   = tk.StringVar(value=self._cwd)
        self._cwd_entry = tk.Entry(cwd_row, textvariable=self._cwd_var,
                                   bg=BG3, fg=FG_DIM, insertbackground=FG,
                                   relief="flat", font=("Consolas", 8),
                                   bd=3, selectbackground=BG4)
        self._cwd_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._cwd_entry.bind("<Return>",   lambda e: self._apply_cwd())
        self._cwd_entry.bind("<FocusOut>", lambda e: self._apply_cwd())

        tk.Button(cwd_row, text="📁", command=self._browse_cwd,
                  bg=BG2, fg=FG_DIM, activebackground=BG3,
                  relief="flat", cursor="hand2", bd=0,
                  font=("Segoe UI", 9), padx=2).pack(side="right")

        # Ligne 2 : input commande
        in_row = tk.Frame(bottom, bg=BG2)
        in_row.pack(fill="x", padx=8, pady=(1, 4))

        self._input = tk.Text(in_row, height=2,
                               bg=BG3, fg=FG, font=("Consolas", 9),
                               relief="flat", borderwidth=0,
                               insertbackground=FG,
                               selectbackground=BG4,
                               wrap="word", padx=8, pady=5)
        self._input.pack(side="left", fill="x", expand=True)
        self._input.bind("<Return>",       self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)
        self._input.bind("<Control-c>",    self._interrupt)
        self._input.bind("<Up>",           self._hist_up)
        self._input.bind("<Down>",         self._hist_down)
        self._input.bind("<Control-t>",    self._on_ctrl_t)
        self._input.bind("<Control-T>",    self._on_ctrl_t)

        self._run_btn = tk.Button(in_row, text="▶", command=self._run_cmd,
                                   bg=BG2, fg=FG, activebackground=BG2,
                                   relief="flat", cursor="hand2", bd=0,
                                   font=("Segoe UI", 18), padx=6)
        self._run_btn.pack(side="right", padx=(4, 0))

        self._tts_btn = tk.Button(in_row, text="🔊", command=self._tts_toggle,
                                   bg=BG2, fg=FG, activebackground=BG2,
                                   relief="flat", cursor="hand2", bd=0,
                                   font=("Segoe UI", 13), padx=4)
        self._tts_btn.pack(side="right", padx=(0, 4))

        # ── DnD ───────────────────────────────
        self.root.after(150, self._setup_dnd)

        self._write_sys(self._cwd)

    # ─────────────────────────────────────────
    #  DnD
    # ─────────────────────────────────────────
    def _setup_dnd(self):
        if not DND_OK:
            return
        try:
            # CWD : drop → extrait le répertoire du chemin déposé
            self._cwd_entry.drop_target_register(DND_FILES)
            self._cwd_entry.dnd_bind("<<Drop>>", self._on_cwd_drop)
            # Input : drop → insère le chemin du fichier tel quel
            self._input.drop_target_register(DND_FILES)
            self._input.dnd_bind("<<Drop>>", self._on_input_drop)
        except Exception as e:
            logger.log(f"Console DnD non disponible : {e}")

    def _on_cwd_drop(self, event):
        paths = parse_drop_data(event.data)
        if not paths:
            return
        p = paths[0]
        # Si c'est un fichier, on prend son répertoire parent
        if os.path.isfile(p):
            p = os.path.dirname(p)
        self._cwd_var.set(p)
        self._apply_cwd()

    def _on_input_drop(self, event):
        paths = parse_drop_data(event.data)
        if not paths:
            return
        # Insère le chemin à la position du curseur
        quoted = " ".join(
            f'"{p}"' if " " in p else p for p in paths
        )
        self._input.insert("insert", quoted)

    # ─────────────────────────────────────────
    #  CWD
    # ─────────────────────────────────────────
    def _apply_cwd(self):
        p = self._cwd_var.get().strip()
        if os.path.isdir(p):
            p = os.path.normpath(p)
            if p != os.path.normpath(self._cwd):
                self._cwd = p
                self._cwd_var.set(self._cwd)
                self._write_sys(f"→ {self._cwd}")
                self._persist()
            else:
                self._cwd = p   # normalise sans afficher
        else:
            self._cwd_var.set(self._cwd)   # remettre l'ancienne valeur

    def _browse_cwd(self):
        d = filedialog.askdirectory(title="Répertoire courant",
                                    initialdir=self._cwd)
        if d:
            self._cwd_var.set(d)
            self._apply_cwd()

    # ─────────────────────────────────────────
    #  Historique des commandes (↑ / ↓)
    # ─────────────────────────────────────────
    def _hist_up(self, event):
        """Remonte dans l'historique."""
        if not self._history:
            return "break"
        if self._hist_idx == -1:
            # Sauvegarder la saisie en cours avant navigation
            self._hist_draft = self._input.get("1.0", "end").strip()
            self._hist_idx = len(self._history) - 1
        elif self._hist_idx > 0:
            self._hist_idx -= 1
        self._input.delete("1.0", "end")
        self._input.insert("1.0", self._history[self._hist_idx])
        self._input.mark_set("insert", "end")
        return "break"

    def _hist_down(self, event):
        """Descend dans l'historique."""
        if self._hist_idx == -1:
            return "break"
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self._input.delete("1.0", "end")
            self._input.insert("1.0", self._history[self._hist_idx])
        else:
            # Retour à la saisie en cours
            self._hist_idx = -1
            self._input.delete("1.0", "end")
            draft = getattr(self, "_hist_draft", "")
            if draft:
                self._input.insert("1.0", draft)
        self._input.mark_set("insert", "end")
        return "break"

    def _hist_push(self, cmd: str):
        """Ajoute une commande à l'historique (dédupliqué, max 200)."""
        if cmd and (not self._history or self._history[-1] != cmd):
            self._history.append(cmd)
            if len(self._history) > 200:
                self._history.pop(0)
        self._hist_idx   = -1
        self._hist_draft = ""
        # Persiste l'historique immédiatement (output sera mis à jour après exec)
        self._persist()

    # ─────────────────────────────────────────
    #  Exécution
    # ─────────────────────────────────────────
    def _on_enter(self, event):
        if not (event.state & 0x1):   # Shift non enfoncé
            self._run_cmd()
            return "break"

    def _run_cmd(self):
        text = self._input.get("1.0", "end").strip()
        if not text:
            return
        self._input.delete("1.0", "end")
        self._hist_idx = -1

        # ── Process en attente d'input → envoyer sur stdin ──
        if self._proc is not None:
            try:
                self._proc.stdin.write(text + "\n")
                self._proc.stdin.flush()
                self._write_out(text, "stdin_tag")
            except Exception as e:
                self._write_err(f"stdin: {e}")
            return

        cmd = text
        self._hist_push(cmd)

        # Commande cd intégrée
        if cmd == "cd" or cmd.startswith("cd ") or cmd.startswith("cd\t"):
            arg = cmd[2:].strip()
            if not arg:
                self._write_sys(f"Répertoire : {self._cwd}")
                return
            target = os.path.expandvars(os.path.expanduser(arg))
            if not os.path.isabs(target):
                target = os.path.join(self._cwd, target)
            target = os.path.normpath(target)
            if os.path.isdir(target):
                self._cwd = target
                self._cwd_var.set(self._cwd)
                self._write_sys(f"→ {self._cwd}")
                self._persist()
            else:
                self._write_err(f"cd: répertoire introuvable : {target}")
            return

        self._write_out(cmd, "cmd_tag")
        self._set_running(True)
        # Auto-label avec le basename de l'exécutable si l'onglet n'est pas renommé
        if not self._label:
            exe = re.split(r'[\s|&;><]', cmd.strip())[0]
            exe = os.path.splitext(os.path.basename(exe))[0]
            if exe:
                self._label = exe
                if self.root:
                    nb = self.dashboard._console_notebook
                    try:
                        nb.tab(self.frame, text=f"  {exe}  ")
                    except Exception:
                        pass
        threading.Thread(target=self._exec, args=(cmd,), daemon=True).start()

    def _exec(self, cmd: str):
        import subprocess
        # Regex pour supprimer toutes les séquences d'échappement ANSI/VT100
        _ANSI = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-9;?]*[A-Za-z]|\][^\x07]*\x07)')

        try:
            self._proc = subprocess.Popen(
                cmd, shell=True, cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1)

            def read_stream(stream, tag):
                for line in stream:
                    line = _ANSI.sub("", line).rstrip("\n")
                    if not line:
                        continue
                    if self.root:
                        t, l = tag, line
                        self.root.after(0, lambda t=t, l=l: self._write_out(l, t))
                stream.close()

            t1 = threading.Thread(target=read_stream,
                                   args=(self._proc.stdout, "out_tag"), daemon=True)
            t2 = threading.Thread(target=read_stream,
                                   args=(self._proc.stderr, "err_tag"), daemon=True)
            t1.start(); t2.start()
            t1.join();  t2.join()
            rc = self._proc.wait()
            self._proc = None
            if self.root:
                if rc != 0:
                    self.root.after(0, lambda: self._write_sys(f"← code {rc}"))
                self.root.after(0, self._persist)
                self.root.after(0, lambda: self._set_running(False))
        except Exception as e:
            if self.root:
                err = str(e)
                self.root.after(0, lambda: self._write_err(err))
                self.root.after(0, self._persist)
                self.root.after(0, lambda: self._set_running(False))
            self._proc = None

    def _run_in_external_terminal(self, cmd: str):
        """Lance dans un vrai terminal Windows (ConPTY complet, flèches, TUI…)."""
        import subprocess
        try:
            if cmd:
                args = f'start cmd.exe /K "cd /d {self._cwd} && {cmd}"'
            else:
                args = f'start cmd.exe /K "cd /d {self._cwd}"'
            subprocess.Popen(args, shell=True, cwd=self._cwd)
            msg = f"↗ Terminal externe — {cmd}" if cmd else f"↗ Terminal externe — {self._cwd}"
            self._write_sys(msg)
        except Exception as e:
            self._write_err(f"Impossible d'ouvrir le terminal : {e}")

    def _set_running(self, running: bool):
        """Met à jour l'apparence pour indiquer qu'un process est en cours."""
        if running:
            self._input.configure(bg="#2A2A3A")
            self._run_btn.configure(text="■", fg="#E07070",
                                    command=self._interrupt)
            self._write_sys("⏳ process en cours — Entrée envoie sur stdin, Ctrl+C interrompt")
        else:
            self._input.configure(bg=BG3)
            self._run_btn.configure(text="▶", fg=FG,
                                    command=self._run_cmd)

    def _interrupt(self, event=None):
        if self._proc:
            try:
                self._proc.terminate()
                self._write_sys("Processus interrompu")
                self._proc = None
                self._set_running(False)
            except Exception:
                pass
        return "break"

    # ─────────────────────────────────────────
    #  Terminal externe
    # ─────────────────────────────────────────
    def _on_ctrl_t(self, event=None):
        """Ctrl+T : lance la commande en cours dans un terminal externe."""
        cmd = self._input.get("1.0", "end").strip()
        if cmd:
            self._input.delete("1.0", "end")
            self._run_in_external_terminal(cmd)
        else:
            self._run_in_external_terminal("")
        return "break"

    # ─────────────────────────────────────────
    #  Menu contextuel sortie
    # ─────────────────────────────────────────
    def _out_context_menu(self, event):
        current_cmd = self._input.get("1.0", "end").strip()
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        if current_cmd:
            menu.add_command(
                label=f"Ouvrir dans terminal  ({current_cmd[:30]}{'…' if len(current_cmd) > 30 else ''})",
                command=lambda: self._run_in_external_terminal(current_cmd))
        else:
            menu.add_command(label="Ouvrir un terminal",
                             command=lambda: self._run_in_external_terminal(""))
        menu.add_separator()
        menu.add_command(label="Copier la sélection",
                         command=lambda: self._copy_selection(None))
        menu.add_command(label="Copier tout", command=self._copy_all)
        menu.add_separator()
        menu.add_command(label="Effacer", command=self._clear)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_selection(self, event):
        try:
            text = self._out_box.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass
        return "break"

    def _copy_all(self):
        text = self._out_box.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _clear(self):
        self._out_box.configure(state="normal")
        self._out_box.delete("1.0", "end")
        self._out_box.configure(state="disabled")

    # ─────────────────────────────────────────
    #  Lecture TTS
    # ─────────────────────────────────────────
    def _tts_toggle(self):
        if self._tts_active:
            tts.stop()
            self._tts_reset()
        else:
            text = self._out_box.get("1.0", "end").strip()
            if not text:
                return
            self._tts_active = True
            self._tts_btn.configure(text="⏹", fg="#E07070")
            tts.speak(text, on_done=lambda: self.root.after(0, self._tts_reset))

    def _tts_reset(self):
        self._tts_active = False
        try:
            self._tts_btn.configure(text="🔊", fg=FG)
        except Exception:
            pass

    # ─────────────────────────────────────────
    #  Écriture dans la zone de sortie
    # ─────────────────────────────────────────
    def _write_out(self, text: str, tag: str = "out_tag"):
        self._out_box.configure(state="normal")
        self._out_box.insert("end", text + "\n", tag)
        self._out_box.see("end")
        self._out_box.configure(state="disabled")

    def _write_sys(self, text: str):
        self._write_out(text, "sys_tag")

    def _write_err(self, text: str):
        self._write_out(text, "err_tag")

    # ─────────────────────────────────────────
    #  Persistance
    # ─────────────────────────────────────────
    _KNOWN_TAGS = ("cmd_tag", "err_tag", "sys_tag", "stdin_tag", "out_tag")

    def _slot_path(self):
        return CONSOLES_DIR / f"console_{self.slot}.json"

    def _dump_content(self) -> list:
        """Sérialise le contenu du widget Text en segments (texte + tag)."""
        segments = []
        tag_stack: list[str] = []
        for etype, value, _ in self._out_box.dump("1.0", "end",
                                                    tag=True, text=True):
            if etype == "tagon" and value in self._KNOWN_TAGS:
                tag_stack.append(value)
            elif etype == "tagoff":
                try:
                    tag_stack.remove(value)
                except ValueError:
                    pass
            elif etype == "text" and value:
                tag = tag_stack[-1] if tag_stack else "out_tag"
                segments.append({"t": value, "g": tag})
        return segments

    def _persist(self):
        """Sauvegarde l'état complet de la console (contenu taggé + historique + cwd + label)."""
        try:
            data = {
                "slot":     self.slot,
                "cwd":      self._cwd,
                "history":  self._history,
                "segments": self._dump_content(),
                "label":    self._label,
            }
            with open(self._slot_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.log(f"Console persist erreur slot {self.slot} : {e}")

    def _restore(self):
        """Charge l'état persisté au démarrage."""
        p = self._slot_path()
        if not p.exists():
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        # CWD
        cwd = data.get("cwd", "")
        if cwd and os.path.isdir(cwd):
            self._cwd = cwd
            self._cwd_var.set(cwd)
        # Historique
        self._history = data.get("history", [])
        # Label personnalisé
        self._label = data.get("label", "")
        if self._label:
            try:
                self.dashboard._console_notebook.tab(
                    self.frame, text=f"  {self._label}  ")
            except Exception:
                pass
        # Contenu taggé
        segments = data.get("segments")
        if segments:
            self._out_box.configure(state="normal")
            self._out_box.delete("1.0", "end")
            for seg in segments:
                self._out_box.insert("end", seg["t"], seg.get("g", "out_tag"))
            self._out_box.see("end")
            self._out_box.configure(state="disabled")
        # Compatibilité ancienne version (output texte brut)
        elif data.get("output"):
            self._out_box.configure(state="normal")
            self._out_box.delete("1.0", "end")
            self._out_box.insert("1.0", data["output"] + "\n")
            self._out_box.see("end")
            self._out_box.configure(state="disabled")
