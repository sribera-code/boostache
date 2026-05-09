"""
main.py – Point d'entrée de Boostache (multi-conversations)
Gère le system tray, la fenêtre dashboard, et orchestre le démarrage.
"""

import threading
import importlib
import os
import re
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import datetime

import pystray

try:
    import ollama as _ollama
    OLLAMA_OK = True
except ImportError:
    OLLAMA_OK = False

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True
except ImportError:
    DND_OK = False

from engine import create_tray_icon, logger, task_manager, hotkey_manager, tts
from storage import settings, conversations, clear_cache, CACHE_DIR, DATA_DIR
import tasks
import bindings

# ── Répertoire de persistance des consoles ────────────────────────────────────
_CONSOLES_DIR = DATA_DIR / "consoles"
_CONSOLES_DIR.mkdir(parents=True, exist_ok=True)

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = "#2B2B2B"
BG2       = "#1E1E1E"
BG3       = "#333333"
BG4       = "#3D3D3D"
BG_LOG    = "#252525"
BG_CHAT   = "#1A1A1A"
BORDER    = "#000000"
FG        = "#D8D8D8"
FG_DIM    = "#888888"
FG_LOG    = "#AAAAAA"
FG_HEAD   = "#BBBBBB"
ACCENT    = "#EEEEEE"
GREEN     = "#6AAF6A"


# ── Utilitaire TTS (module-level) ─────────────────────────────────────────────
def _strip_markdown(text: str) -> str:
    """Supprime les balises markdown avant la lecture TTS."""
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\*\*\*([^*]+)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'─+', '', text)
    return text.strip()


# ── Utilitaire DnD (module-level) ─────────────────────────────────────────────
def _parse_drop_data(data: str) -> list[str]:
    paths = []
    braced = re.findall(r'\{([^}]+)\}', data)
    paths.extend(braced)
    remainder = re.sub(r'\{[^}]+\}', '', data).strip()
    if remainder:
        for part in re.split(r'(?<![:\\])\s+', remainder):
            part = part.strip()
            if part:
                paths.append(part)
    return [p for p in paths if p]


# ─────────────────────────────────────────────
#  ConversationTab – une conversation = un onglet
# ─────────────────────────────────────────────
class ConversationTab:
    """Encapsule l'état et les widgets d'une seule conversation."""

    _counter = 0

    def __init__(self, inner_notebook: ttk.Notebook, dashboard: "DashboardWindow"):
        ConversationTab._counter += 1
        self.num = ConversationTab._counter
        self.dashboard = dashboard
        self.conv_id: str | None = None
        self._custom_label: str = ""   # Label personnalisé (prioritaire sur le titre auto)

        self._chat_history: list[dict] = []
        self._attached_files: list[dict] = []
        self._chat_streaming = False
        self._user_scrolled = False
        self._model_var = tk.StringVar()
        self._models_list: list[str] = []
        self._tts_active = False

        self.frame = ttk.Frame(inner_notebook)
        inner_notebook.add(self.frame, text="  ·  ")   # Pas de titre par défaut

        self._build()

    @property
    def root(self):
        return self.dashboard.root

    # ─────────────────────────────────────────
    #  Construction des widgets
    # ─────────────────────────────────────────
    def _build(self):
        # Zone messages
        msg_frame = tk.Frame(self.frame, bg=BG_CHAT)
        msg_frame.pack(fill="both", expand=True)

        self._chat_box = tk.Text(
            msg_frame, state="disabled",
            bg=BG_CHAT, fg=FG, font=("Segoe UI", 9),
            relief="flat", borderwidth=0, wrap="word",
            padx=10, pady=8,
            selectbackground="#4A7EBB", selectforeground="#FFFFFF")

        chat_vsb = ttk.Scrollbar(msg_frame, orient="vertical",
                                  command=self._chat_box.yview)
        self._chat_box.configure(yscrollcommand=chat_vsb.set)
        chat_vsb.pack(side="right", fill="y")
        self._chat_box.pack(fill="both", expand=True)

        # ── Tags de base ──
        # Vert clair pour l'utilisateur
        self._chat_box.tag_configure("user_tag",
                                      foreground="#8EC88E",
                                      font=("Segoe UI", 9), spacing1=4, spacing3=4,
                                      lmargin1=12, lmargin2=12, rmargin=12)
        # Bleu clair pour le LLM (plus de surlignage fond)
        self._chat_box.tag_configure("bot_tag",
                                      foreground="#A8CCEA",
                                      font=("Segoe UI", 9), spacing1=4, spacing3=4,
                                      lmargin1=12, lmargin2=12, rmargin=12)
        self._chat_box.tag_configure("sys_tag",
                                      foreground=FG_DIM,
                                      font=("Segoe UI", 8, "italic"),
                                      spacing1=4, spacing3=4, lmargin1=12)
        self._chat_box.tag_configure("label_tag",
                                      foreground=FG_DIM,
                                      font=("Segoe UI", 7, "bold"),
                                      spacing1=8, spacing3=0, lmargin1=12)

        # ── Tags markdown — uniquement foreground, pas de background ──
        self._chat_box.tag_configure("md_h1", foreground="#FFFFFF",
                                      font=("Segoe UI", 14, "bold"),
                                      spacing1=10, spacing3=4, lmargin1=12)
        self._chat_box.tag_configure("md_h2", foreground="#EEEEEE",
                                      font=("Segoe UI", 12, "bold"),
                                      spacing1=8, spacing3=3, lmargin1=12)
        self._chat_box.tag_configure("md_h3", foreground="#E0E0E0",
                                      font=("Segoe UI", 10, "bold"),
                                      spacing1=6, spacing3=2, lmargin1=12)
        self._chat_box.tag_configure("md_bold", foreground="#D0E8F8",
                                      font=("Segoe UI", 9, "bold"))
        self._chat_box.tag_configure("md_italic", foreground="#A8CCEA",
                                      font=("Segoe UI", 9, "italic"))
        self._chat_box.tag_configure("md_bold_italic", foreground="#C8E4F8",
                                      font=("Segoe UI", 9, "bold italic"))
        # Code inline : bleu clair, sans fond
        self._chat_box.tag_configure("md_code", foreground="#7EC8E8",
                                      font=("Consolas", 9))
        # Bloc de code : bleu légèrement plus sombre, sans fond
        self._chat_box.tag_configure("md_code_block", foreground="#90C8E0",
                                      font=("Consolas", 9),
                                      spacing1=2, spacing3=2,
                                      lmargin1=20, lmargin2=20)
        self._chat_box.tag_configure("md_bullet", foreground="#A8CCEA",
                                      font=("Segoe UI", 9),
                                      lmargin1=20, lmargin2=32)
        self._chat_box.tag_configure("md_hr", foreground="#444444",
                                      font=("Segoe UI", 7),
                                      spacing1=4, spacing3=4, lmargin1=12)

        self._chat_box.bind("<Control-c>", self._chat_copy_selection)
        self._chat_box.bind("<Control-C>", self._chat_copy_selection)
        self._chat_box.bind("<MouseWheel>", self._on_chat_scroll)
        self._chat_box.bind("<Button-3>", self._chat_context_menu)

        # Barre fichiers attachés (initialement cachée)
        self._file_bar = tk.Frame(self.frame, bg=BG2)

        # Zone de saisie
        input_frame = tk.Frame(self.frame, bg=BG2, pady=6)
        input_frame.pack(fill="x")

        tk.Button(input_frame, text="🔗", command=self._chat_attach_file,
                  bg=BG2, fg=FG, activebackground=BG2,
                  relief="flat", cursor="hand2", padx=4, pady=2, bd=0,
                  font=("Segoe UI", 13)).pack(side="left", padx=(8, 4))

        tk.Button(input_frame, text="🎤", command=self._chat_dictate,
                  bg=BG2, fg=FG, activebackground=BG2,
                  relief="flat", cursor="hand2", padx=4, pady=2, bd=0,
                  font=("Segoe UI", 13)).pack(side="left", padx=(0, 4))

        tk.Button(input_frame, text="📷", command=self._chat_screenshot,
                  bg=BG2, fg=FG, activebackground=BG2,
                  relief="flat", cursor="hand2", padx=4, pady=2, bd=0,
                  font=("Segoe UI", 13)).pack(side="left", padx=(0, 4))

        self._tts_btn = tk.Button(input_frame, text="🔊", command=self._tts_toggle,
                                   bg=BG2, fg=FG, activebackground=BG2,
                                   relief="flat", cursor="hand2", padx=4, pady=2, bd=0,
                                   font=("Segoe UI", 13))
        self._tts_btn.pack(side="left", padx=(0, 4))

        self._chat_input = tk.Text(input_frame, height=3,
                                    bg=BG3, fg=FG, font=("Segoe UI", 9),
                                    relief="flat", borderwidth=0,
                                    insertbackground=FG, selectbackground=BG4,
                                    wrap="word", padx=8, pady=6)
        self._chat_input.pack(side="left", fill="x", expand=True, padx=4)
        self._chat_input.bind("<Return>", self._chat_on_enter)
        self._chat_input.bind("<Shift-Return>", lambda e: None)
        self._chat_input.bind("<Control-v>", self._chat_paste)
        self._chat_input.bind("<Control-V>", self._chat_paste)

        self._chat_send_btn = tk.Button(
            input_frame, text="➤", command=self._chat_send,
            bg=BG2, fg=FG, activebackground=BG2,
            relief="flat", cursor="hand2", padx=4, pady=0, bd=0,
            font=("Segoe UI", 24))
        self._chat_send_btn.pack(side="right", padx=(4, 8))

        self.root.after(150, self._setup_dnd)

    # ─────────────────────────────────────────
    #  Modèles
    # ─────────────────────────────────────────
    def set_models(self, models: list[str], preferred: str = ""):
        self._models_list = models
        if not models:
            return
        current = self._model_var.get()
        if preferred and preferred in models:
            self._model_var.set(preferred)
        elif current and current in models:
            pass  # Conserver le modèle déjà sélectionné
        else:
            last = settings.get("last_model")
            if last and last in models:
                self._model_var.set(last)
            else:
                self._model_var.set(models[0])

    # ─────────────────────────────────────────
    #  Messages
    # ─────────────────────────────────────────
    def _chat_at_bottom(self) -> bool:
        return not self._user_scrolled

    def _on_chat_scroll(self, event):
        try:
            if event.delta > 0:
                self._user_scrolled = True
            else:
                if self._chat_box.yview()[1] >= 0.99:
                    self._user_scrolled = False
        except Exception:
            pass

    def _chat_append(self, text: str, tag: str, label: str = ""):
        self._chat_box.configure(state="normal")
        if label:
            self._chat_box.insert("end", label + "\n", "label_tag")
        self._chat_box.insert("end", text + "\n", tag)
        if self._chat_at_bottom():
            self._chat_box.see("end")
        self._chat_box.configure(state="disabled")

    def _chat_box_start_bot(self, label: str):
        """Insère le label du modèle sans ligne vide initiale."""
        self._chat_box.configure(state="normal")
        self._chat_box.insert("end", label + "\n", "label_tag")
        self._chat_box.see("end")
        self._chat_box.configure(state="disabled")

    def _chat_append_stream_chunk(self, chunk: str):
        self._chat_box.configure(state="normal")
        self._chat_box.insert("end", chunk, "bot_tag")
        if self._chat_at_bottom():
            self._chat_box.see("end")
        self._chat_box.configure(state="disabled")

    def _chat_context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))

        # Sous-menu modèle (propre à cet onglet)
        model_menu = tk.Menu(menu, tearoff=0,
                             bg=BG3, fg=FG, activebackground=BG4,
                             activeforeground=ACCENT, relief="flat", bd=0,
                             font=("Segoe UI", 9))
        current = self._model_var.get()
        for m in self._models_list:
            lbl = ("✓  " if m == current else "    ") + m
            model_menu.add_command(label=lbl,
                                   command=lambda m=m: self._model_var.set(m))
        if not self._models_list:
            model_menu.add_command(label="(aucun modèle)", state="disabled")

        menu.add_cascade(label=f"Modèle : {current or '—'}", menu=model_menu)
        menu.add_command(label="Rafraîchir les modèles",
                         command=self.dashboard._refresh_models)
        menu.add_separator()
        menu.add_command(label="Copier la sélection",
                         command=lambda: self._chat_copy_selection(None))
        menu.add_command(label="Copier tout", command=self._chat_copy_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ─────────────────────────────────────────
    #  Envoi / Streaming
    # ─────────────────────────────────────────
    def _chat_on_enter(self, event):
        if not event.state & 0x1:
            self._chat_send()
            return "break"

    def _chat_send(self):
        if self._chat_streaming:
            return
        text = self._chat_input.get("1.0", "end").strip()
        if not text and not self._attached_files:
            return
        model = self._model_var.get()
        if not model or model.startswith("Erreur") or model.startswith("("):
            self._chat_append("⚠ Sélectionne un modèle valide.", "sys_tag")
            return

        self._chat_input.delete("1.0", "end")
        self._chat_streaming = True
        self._chat_send_btn.config(state="disabled", text="…")
        settings.set("last_model", model)

        user_display = text or ""
        message: dict = {"role": "user", "content": text or ""}

        if self._attached_files:
            text_parts, images, labels = [], [], []
            for f in self._attached_files:
                if f["type"] == "text":
                    text_parts.append(f"[Fichier : {f['name']}]\n```\n{f['content']}\n```")
                    labels.append(f"🔗 {f['name']}")
                elif f["type"] == "image":
                    images.append(f["content"])
                    labels.append(f"🖼 {f['name']}")
            if text_parts:
                joined = "\n\n".join(text_parts)
                message["content"] = f"{joined}\n\n{text}" if text else joined
            if images:
                message["images"] = images
            user_display = "  ".join(labels) + (f"\n{text}" if text else "")

        self._chat_append(user_display, "user_tag", "Vous")
        self._chat_history.append(message)
        for f in self._attached_files:
            if f.get("temp_path"):
                try:
                    os.unlink(f["temp_path"])
                except Exception:
                    pass
        self._chat_detach_file()

        threading.Thread(
            target=self._chat_stream,
            args=(model, list(self._chat_history)),
            daemon=True
        ).start()

    def _chat_stream(self, model: str, history: list[dict]):
        full_response = ""
        bot_start_mark = None
        try:
            if self.root:
                self.root.after(0, lambda: self._chat_box_start_bot(model))
                import time; time.sleep(0.05)
                try:
                    bot_start_mark = self._chat_box.index("end-1c")
                except Exception:
                    pass

            stream = _ollama.chat(model=model, messages=history, stream=True)
            for chunk in stream:
                token = chunk["message"]["content"]
                full_response += token
                if self.root:
                    t = token
                    self.root.after(0, lambda t=t: self._chat_append_stream_chunk(t))

        except Exception as e:
            if self.root:
                err = str(e)
                self.root.after(0, lambda: self._chat_append(f"⚠ Erreur : {err}", "sys_tag"))
        finally:
            if full_response:
                self._chat_history.append({"role": "assistant", "content": full_response})
                # Sauvegarder avec le conv_id de cet onglet
                self.conv_id = conversations.save(self._chat_history, model, self.conv_id)
                # Mettre à jour la meta (conv_id peut être nouveau)
                if self.root:
                    self.root.after(0, self.dashboard._chat_save_meta)
                # Mettre à jour le titre de l'onglet
                self._schedule_tab_rename()
                # Rendre le markdown après streaming
                if self.root and bot_start_mark:
                    fr = full_response
                    bm = bot_start_mark
                    self.root.after(0, lambda: self._render_markdown_at(fr, bm))
            self._chat_streaming = False
            self._user_scrolled = False
            if self.root:
                self.root.after(0, lambda: self._chat_send_btn.config(
                    state="normal", text="➤"))

    # ─────────────────────────────────────────
    #  Titre de l'onglet
    # ─────────────────────────────────────────
    def _get_title(self) -> str:
        if self._custom_label:
            return self._custom_label
        for m in self._chat_history:
            if m.get("role") == "user":
                content = m.get("content", "")
                first_line = content.split("\n")[0]
                if first_line.startswith("[Fichier"):
                    continue
                t = first_line[:18]
                return t + ("…" if len(first_line) > 18 else "")
        return ""   # Pas de titre par défaut

    def _schedule_tab_rename(self):
        if self._custom_label:
            return   # Ne pas écraser un label personnalisé
        title = self._get_title()
        if self.root:
            self.root.after(0, lambda: self._do_tab_rename(title))

    def _do_tab_rename(self, title: str):
        try:
            nb = self.dashboard._conv_notebook
            display = f"  {title}  " if title else "  ·  "
            nb.tab(self.frame, text=display)
        except Exception:
            pass

    # ─────────────────────────────────────────
    #  Rendu Markdown
    # ─────────────────────────────────────────
    def _render_markdown_at(self, text: str, start_mark: str):
        """Remplace le texte brut par du texte formaté markdown."""
        self._chat_box.configure(state="normal")
        try:
            self._chat_box.delete(start_mark, "end")
        except Exception:
            self._chat_box.configure(state="disabled")
            return
        # Saut de ligne après le label du modèle
        self._chat_box.insert("end", "\n", "label_tag")

        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Blocs de code ```
            if line.startswith("```"):
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                if code_lines:
                    self._chat_box.insert("end", "\n".join(code_lines) + "\n",
                                          "md_code_block")
                i += 1
                continue

            # Règle horizontale
            if line.strip() in ("---", "***", "___") and len(line.strip()) >= 3:
                self._chat_box.insert("end", "─" * 48 + "\n", "md_hr")
                i += 1
                continue

            # Headers
            if line.startswith("### "):
                self._insert_md_inline(line[4:], "md_h3")
                self._chat_box.insert("end", "\n")
            elif line.startswith("## "):
                self._insert_md_inline(line[3:], "md_h2")
                self._chat_box.insert("end", "\n")
            elif line.startswith("# "):
                self._insert_md_inline(line[2:], "md_h1")
                self._chat_box.insert("end", "\n")
            # Listes (- * +)
            elif re.match(r"^[-*+] ", line):
                self._chat_box.insert("end", "• ", "md_bullet")
                self._insert_md_inline(line[2:], "md_bullet")
                self._chat_box.insert("end", "\n", "md_bullet")
            # Listes numérotées
            elif re.match(r'^\d+\. ', line):
                self._insert_md_inline(line, "md_bullet")
                self._chat_box.insert("end", "\n", "md_bullet")
            # Ligne normale
            else:
                self._insert_md_inline(line, "bot_tag")
                self._chat_box.insert("end", "\n", "bot_tag")

            i += 1

        self._chat_box.see("end")
        self._chat_box.configure(state="disabled")

    def _insert_md_inline(self, text: str, base_tag: str):
        """Insère du texte avec formatage inline markdown."""
        pattern = r'(\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)'
        parts = re.split(pattern, text)
        for part in parts:
            if part.startswith("***") and part.endswith("***") and len(part) > 6:
                self._chat_box.insert("end", part[3:-3], "md_bold_italic")
            elif part.startswith("**") and part.endswith("**") and len(part) > 4:
                self._chat_box.insert("end", part[2:-2], "md_bold")
            elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                self._chat_box.insert("end", part[1:-1], "md_italic")
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                self._chat_box.insert("end", part[1:-1], "md_code")
            else:
                self._chat_box.insert("end", part, base_tag)

    # ─────────────────────────────────────────
    #  Copie
    # ─────────────────────────────────────────
    def _chat_copy_all(self):
        text = self._chat_box.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _chat_copy_selection(self, event):
        try:
            selected = self._chat_box.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
        except tk.TclError:
            pass
        return "break"

    # ─────────────────────────────────────────
    #  Lecture TTS
    # ─────────────────────────────────────────
    def _tts_toggle(self):
        if self._tts_active:
            tts.stop()
            self._tts_reset()
        else:
            text = self._get_tts_text()
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

    def _get_tts_text(self) -> str:
        for msg in reversed(self._chat_history):
            if msg.get("role") == "assistant":
                return _strip_markdown(msg.get("content", ""))
        return ""

    # ─────────────────────────────────────────
    #  Fichiers attachés
    # ─────────────────────────────────────────
    def _rebuild_file_bar(self):
        for w in self._file_bar.winfo_children():
            w.destroy()
        if not self._attached_files:
            self._file_bar.pack_forget()
            return
        for i, f in enumerate(self._attached_files):
            chip = tk.Frame(self._file_bar, bg=BG3, padx=4, pady=2)
            chip.pack(side="left", padx=(6, 0), pady=3)
            icon = "🖼" if f["type"] == "image" else "🔗"
            lbl = tk.Label(chip, text=f"{icon} {f['name']}", bg=BG3, fg=FG,
                           font=("Segoe UI", 8), cursor="hand2")
            lbl.pack(side="left")
            # Right-click → ouvrir le répertoire ou détacher
            path = f.get("path", "")
            lbl.bind("<Button-3>", lambda e, p=path, idx=i: self._file_chip_menu(e, p, idx))
            chip.bind("<Button-3>", lambda e, p=path, idx=i: self._file_chip_menu(e, p, idx))
            tk.Button(chip, text="✕",
                      command=lambda idx=i: self._detach_one(idx),
                      bg=BG3, fg=FG_DIM, activebackground=BG4,
                      relief="flat", cursor="hand2", bd=0,
                      font=("Segoe UI", 8)).pack(side="left", padx=(4, 0))
        self._file_bar.pack(fill="x", before=self._chat_input.master)

    def _open_file_location(self, path: str):
        try:
            import subprocess
            subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
        except Exception:
            pass

    def _file_chip_menu(self, event, path: str, idx: int):
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        if path:
            menu.add_command(label="Ouvrir le répertoire",
                             command=lambda: self._open_file_location(path))
            menu.add_separator()
        menu.add_command(label="Retirer", command=lambda: self._detach_one(idx))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _detach_one(self, idx: int):
        if 0 <= idx < len(self._attached_files):
            self._attached_files.pop(idx)
        self._rebuild_file_bar()

    def _chat_detach_file(self):
        self._attached_files.clear()
        self._rebuild_file_bar()

    def _chat_attach_file(self):
        path = filedialog.askopenfilename(
            title="Ajouter un fichier",
            filetypes=[
                ("Texte & code", "*.txt *.py *.js *.ts *.json *.yaml *.yml *.md *.csv *.xml *.html *.css *.c *.cpp *.h *.java *.rs *.go"),
                ("Images",       "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                ("Tous",         "*.*"),
            ]
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str, temp: bool = False):
        ext  = os.path.splitext(path)[1].lower()
        name = os.path.basename(path)
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            try:
                with open(path, "rb") as f:
                    data = f.read()
                self._attached_files.append({
                    "name": name, "content": data, "type": "image",
                    "path": path, "temp_path": path if temp else None})
                self._rebuild_file_bar()
            except Exception as e:
                self._chat_append(f"Impossible de lire l'image : {e}", "sys_tag")
        else:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self._attached_files.append({
                    "name": name, "content": content, "type": "text",
                    "path": path, "temp_path": None})
                self._rebuild_file_bar()
            except Exception as e:
                self._chat_append(f"Impossible de lire le fichier : {e}", "sys_tag")

    # ─────────────────────────────────────────
    #  Dicter / Screenshot / Paste / DnD
    # ─────────────────────────────────────────
    def _chat_dictate(self):
        def send_hotkey():
            try:
                import keyboard
                keyboard.send("windows+h")
            except Exception:
                pass
        threading.Thread(target=send_hotkey, daemon=True).start()

    def _chat_screenshot(self):
        def capture():
            try:
                import time
                from PIL import ImageGrab
                import keyboard
                try:
                    self.root.after(0, lambda: self.root.clipboard_clear())
                except Exception:
                    pass
                keyboard.send("windows+shift+s")
                deadline = time.time() + 30
                img = None
                while time.time() < deadline:
                    time.sleep(0.4)
                    try:
                        img = ImageGrab.grabclipboard()
                        if img is not None:
                            break
                    except Exception:
                        pass
                if img is None:
                    return
                tmp_path = str(CACHE_DIR / f"boostache_screenshot_{int(time.time())}.png")
                img.save(tmp_path, "PNG")
                if self.root:
                    p = tmp_path
                    self.root.after(0, lambda: self._load_file(p, temp=True))
            except Exception as e:
                if self.root:
                    err = str(e)
                    self.root.after(0, lambda: self._chat_append(
                        f"⚠ Capture échouée : {err}", "sys_tag"))
        threading.Thread(target=capture, daemon=True).start()

    def _chat_paste(self, event):
        try:
            from PIL import ImageGrab
            import time as _t
            img = ImageGrab.grabclipboard()
            if img is not None:
                tmp_path = str(CACHE_DIR / f"boostache_paste_{int(_t.time())}.png")
                img.save(tmp_path, "PNG")
                self._load_file(tmp_path)
                return "break"
        except Exception:
            pass
        return None

    def _setup_dnd(self):
        if not DND_OK or not hasattr(self, "_chat_input"):
            return
        try:
            self._chat_input.drop_target_register(DND_FILES)
            self._chat_input.dnd_bind("<<Drop>>", self._on_file_drop)
        except Exception as e:
            logger.log(f"Drag & drop non disponible : {e}")

    def _on_file_drop(self, event):
        for path in _parse_drop_data(event.data):
            self._load_file(path)

    # ─────────────────────────────────────────
    #  Chargement depuis storage
    # ─────────────────────────────────────────
    def load_conversation(self, data: dict):
        """Charge une conversation depuis son dict de stockage."""
        self._chat_history = data.get("messages", [])
        model = data.get("model", "")
        self.conv_id = data.get("id")

        self._chat_box.configure(state="normal")
        self._chat_box.delete("1.0", "end")
        self._chat_box.configure(state="disabled")

        for msg in self._chat_history:
            role = msg.get("role", "")
            text = msg.get("content", "")
            if role == "user":
                lines = text.split("\n")
                display = lines[0] if lines[0].startswith("[Fichier") else text[:200]
                self._chat_append(display, "user_tag", "Vous")
            elif role == "assistant":
                mark = self._chat_box.index("end-1c")
                self._chat_append(text, "bot_tag", model)
                self._render_markdown_at(text, mark)

        if model:
            self._model_var.set(model)

        # Mettre à jour le titre de l'onglet
        self._schedule_tab_rename()


# ─────────────────────────────────────────────
#  ConsoleTab – une console = un onglet
# ─────────────────────────────────────────────
class ConsoleTab:
    """Onglet terminal léger : champ CWD, zone de sortie, champ d'entrée."""

    _counter = 0

    def __init__(self, inner_notebook: ttk.Notebook, dashboard: "DashboardWindow",
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
        paths = _parse_drop_data(event.data)
        if not paths:
            return
        p = paths[0]
        # Si c'est un fichier, on prend son répertoire parent
        if os.path.isfile(p):
            p = os.path.dirname(p)
        self._cwd_var.set(p)
        self._apply_cwd()

    def _on_input_drop(self, event):
        paths = _parse_drop_data(event.data)
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
    #  Exécution
    # ─────────────────────────────────────────
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
        import subprocess, re
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
        return _CONSOLES_DIR / f"console_{self.slot}.json"

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
        import json
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
        import json
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


# ─────────────────────────────────────────────
#  Fenêtre principale (Dashboard)
# ─────────────────────────────────────────────
class DashboardWindow:
    def __init__(self):
        self.root = None  # type: ignore
        self._log_queue: list[str] = []
        self._lock = threading.Lock()
        logger.subscribe(self._enqueue_log)

        self._conv_tabs: list[ConversationTab] = []
        self._conv_notebook: ttk.Notebook | None = None
        self._restoring_chat: bool = False

        self._console_tabs: list["ConsoleTab"] = []
        self._console_notebook: ttk.Notebook | None = None
        self._plus_frame_console: ttk.Frame | None = None
        self._restoring_consoles: bool = False

    def build(self):
        if DND_OK:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()

        self.root.title("Boostache")
        self.root.geometry("720x580")
        self.root.resizable(True, True)
        self.root.configure(bg=BG2)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.iconbitmap("boostache.ico")
        self._set_dark_titlebar()
        self._style()
        self._build_notebook()
        self._build_status_bar()
        self._poll_logs()
        self._task_load_persisted()   # charge les tâches custom sauvegardées
        self._refresh_tasks()
        self.hide()

    def _on_unmap(self, event):
        if event.widget == self.root:
            self.root.after(0, self.hide)

    def _set_dark_titlebar(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
            self.root.withdraw()
            self.root.after(10, lambda: None)
        except Exception:
            pass

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        # Notebook principal (onglets Conversation / Historique / …)
        style.configure("TNotebook", background=BG2, borderwidth=1, relief="flat")
        style.configure("TNotebook.Tab", background=BG3, foreground=FG_DIM,
                        padding=[16, 7], font=("Segoe UI", 9), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", BG4), ("active", BG3)],
                  foreground=[("selected", ACCENT), ("active", FG)])

        # Notebook interne (onglets de conversation)
        style.configure("Inner.TNotebook", background=BG2, borderwidth=0, relief="flat")
        style.configure("Inner.TNotebook.Tab", background=BG2, foreground=FG_DIM,
                        padding=[10, 4], font=("Segoe UI", 8), borderwidth=0)
        style.map("Inner.TNotebook.Tab",
                  background=[("selected", BG_CHAT), ("active", BG3)],
                  foreground=[("selected", FG), ("active", FG)])

        style.configure("TFrame", background=BG)
        style.configure("Treeview", background=BG3, foreground=FG,
                        fieldbackground=BG3, rowheight=28,
                        font=("Segoe UI", 9), borderwidth=1, relief="flat")
        style.configure("Treeview.Heading", background=BG2, foreground=FG_HEAD,
                        font=("Segoe UI", 9, "bold"), relief="flat", borderwidth=0)
        style.map("Treeview",
                  background=[("selected", BG4)],
                  foreground=[("selected", ACCENT)])
        style.configure("Vertical.TScrollbar", background=BG3, troughcolor=BG2,
                        borderwidth=1, arrowcolor=FG_DIM, relief="flat",
                        darkcolor=BG2, lightcolor=BG4)
        style.configure("Chat.TCombobox", fieldbackground=BG3, background=BG3,
                        foreground=FG, selectbackground=BG4, selectforeground=ACCENT,
                        borderwidth=0)
        style.map("Chat.TCombobox", fieldbackground=[("readonly", BG3)],
                  foreground=[("readonly", FG)])

    # ─────────────────────────────────────────
    #  Notebook principal
    # ─────────────────────────────────────────
    def _build_notebook(self):
        border = tk.Frame(self.root, bg="#0a0a0a", padx=1, pady=1)
        border.pack(fill="both", expand=True)
        nb = ttk.Notebook(border)
        nb.pack(fill="both", expand=True)

        # ── Conversation ──────────────────────
        chat_frame = ttk.Frame(nb)
        nb.add(chat_frame, text="  Conversation  ")
        self._build_chat_tab(chat_frame)

        # ── Console ───────────────────────────
        console_frame = ttk.Frame(nb)
        nb.add(console_frame, text="  Console  ")
        self._build_console_tab(console_frame)

        # ── Historique ────────────────────────
        log_frame = ttk.Frame(nb)
        nb.add(log_frame, text="  Historique  ")
        self._log_box = scrolledtext.ScrolledText(
            log_frame, state="disabled",
            bg=BG_LOG, fg=FG_LOG, font=("Consolas", 9),
            insertbackground=FG, relief="flat", borderwidth=0,
            selectbackground=BG4, selectforeground=ACCENT, padx=8, pady=6)
        self._log_box.pack(fill="both", expand=True)
        self._log_box.bind("<Button-3>", self._log_context_menu)
        self._log_box.bind("<Control-c>", lambda e: self._log_copy_selection())
        self._log_box.bind("<Control-C>", lambda e: self._log_copy_selection())

        # ── Tâches ────────────────────────────
        task_frame = ttk.Frame(nb)
        nb.add(task_frame, text="  Tâches  ")
        cols = ("Tâche", "Périodicité", "Prochain déclenchement")
        self._task_tree = ttk.Treeview(task_frame, columns=cols,
                                       show="headings", selectmode="browse")
        for c in cols:
            self._task_tree.heading(c, text=c)
        self._task_tree.column("Tâche",                    width=260)
        self._task_tree.column("Périodicité",              width=160)
        self._task_tree.column("Prochain déclenchement",   width=160)
        vsb = ttk.Scrollbar(task_frame, orient="vertical",
                            command=self._task_tree.yview)
        self._task_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._task_tree.pack(fill="both", expand=True)
        self._task_tree.bind("<Button-3>", self._task_context_menu)

        # ── Raccourcis ────────────────────────
        hk_frame = ttk.Frame(nb)
        nb.add(hk_frame, text="  Raccourcis  ")
        hk_cols = ("Raccourci", "Action")
        self._hk_tree = ttk.Treeview(hk_frame, columns=hk_cols,
                                      show="headings", selectmode="browse")
        for c in hk_cols:
            self._hk_tree.heading(c, text=c)
        self._hk_tree.column("Raccourci", width=190)
        self._hk_tree.column("Action", width=370)
        vsb2 = ttk.Scrollbar(hk_frame, orient="vertical",
                              command=self._hk_tree.yview)
        self._hk_tree.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side="right", fill="y")
        self._hk_tree.pack(fill="both", expand=True)
        self._hk_tree.bind("<Button-3>", self._hk_context_menu)
        self._refresh_hotkeys()

    # ─────────────────────────────────────────
    #  Tab Conversation (inner notebook multi-onglets)
    # ─────────────────────────────────────────
    def _build_chat_tab(self, parent):
        if not OLLAMA_OK:
            tk.Label(parent,
                     text="⚠  La librairie 'ollama' n'est pas installée.\npip install ollama",
                     bg=BG, fg=FG_DIM, font=("Segoe UI", 10),
                     justify="center").pack(expand=True)
            return

        # ── Notebook interne ──────────────────
        self._conv_notebook = ttk.Notebook(parent, style="Inner.TNotebook")
        self._conv_notebook.pack(fill="both", expand=True)

        # Onglet "+" (pseudo-tab qui crée une nouvelle conversation)
        self._plus_frame = ttk.Frame(self._conv_notebook)
        self._conv_notebook.add(self._plus_frame, text="  ＋  ")

        # Événement : si l'utilisateur clique sur l'onglet "+"
        self._conv_notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Right-click sur la zone des onglets
        self._conv_notebook.bind("<Button-3>", self._on_tab_right_click)

        # Restaurer les onglets depuis la meta
        self._restoring_chat = True
        meta_tabs = self._chat_load_meta()
        if meta_tabs:
            for entry in meta_tabs:
                tab = self._new_conversation_tab()
                conv_id = entry.get("conv_id")
                label   = entry.get("label", "")
                if conv_id:
                    data = conversations.load(conv_id)
                    if data and data.get("messages"):
                        tab.load_conversation(data)
                if label:
                    tab._custom_label = label
                    try:
                        self._conv_notebook.tab(tab.frame, text=f"  {label}  ")
                    except Exception:
                        pass
            # Sélectionner le premier onglet (pas le +)
            if self._conv_tabs:
                self._conv_notebook.select(self._conv_tabs[0].frame)
        else:
            self._new_conversation_tab()
            self.root.after(700, self._load_latest_conversation)
        self._restoring_chat = False

        self.root.after(500, self._refresh_models)

    # ─────────────────────────────────────────
    #  Gestion des onglets de conversation
    # ─────────────────────────────────────────
    def _new_conversation_tab(self, insert_after: int | None = None):
        """Crée un nouvel onglet conversation.
        insert_after : index (dans le notebook) après lequel insérer.
                       None → insérer juste avant l'onglet '+'.
        """
        existing_models: list[str] = []
        if self._conv_tabs:
            existing_models = self._conv_tabs[0]._models_list

        # ConversationTab.__init__ fait notebook.add() → ajouté à la fin
        tab = ConversationTab(self._conv_notebook, self)

        try:
            all_tabs = list(self._conv_notebook.tabs())
            plus_id  = str(self._plus_frame)
            tab_id   = str(tab.frame)
            plus_idx = all_tabs.index(plus_id) if plus_id in all_tabs else len(all_tabs)

            # Position cible : après insert_after, ou juste avant "+"
            target_idx = (insert_after + 1) if insert_after is not None else plus_idx

            # Clamp pour ne jamais dépasser la position de "+" (après insertion)
            target_idx = min(target_idx, plus_idx)

            tab_text = self._conv_notebook.tab(tab.frame, "text")
            self._conv_notebook.forget(tab.frame)
            self._conv_notebook.insert(target_idx, tab.frame, text=tab_text)
        except Exception:
            pass

        if existing_models:
            tab.set_models(existing_models, preferred=settings.get("last_model", ""))

        self._conv_tabs.append(tab)
        self._conv_notebook.select(tab.frame)
        self._chat_save_meta()
        return tab

    def _on_tab_changed(self, event):
        """Intercepte la sélection de l'onglet '+'."""
        if not hasattr(self, '_plus_frame'):
            return
        try:
            selected = self._conv_notebook.select()
            if selected == str(self._plus_frame):
                # Créer un vrai onglet à la place
                new_tab = self._new_conversation_tab()
        except Exception:
            pass

    def _on_tab_right_click(self, event):
        """Right-click sur la barre des onglets du notebook interne."""
        nb = self._conv_notebook
        try:
            idx = nb.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        tab_id = nb.tabs()[idx]

        # Onglet "+"
        if tab_id == str(self._plus_frame):
            menu = tk.Menu(self.root, tearoff=0,
                           bg=BG3, fg=FG, activebackground=BG4,
                           activeforeground=ACCENT, relief="flat", bd=0,
                           font=("Segoe UI", 9))
            menu.add_command(label="Nouvelle conversation",
                             command=self._new_conversation_tab)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return

        # Onglet conversation
        target: "ConversationTab | None" = None
        for t in self._conv_tabs:
            if str(t.frame) == tab_id:
                target = t
                break
        if target is None:
            return

        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        # Insérer juste après l'onglet cliqué (idx + 1)
        menu.add_command(label="Nouvelle conversation",
                         command=lambda: self._new_conversation_tab(insert_after=idx))
        menu.add_command(label="Renommer cet onglet…",
                         command=lambda: self._rename_conv_tab(target))
        menu.add_separator()
        menu.add_command(label="Fermer cette conversation",
                         command=lambda: self._close_tab(target))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _close_current_tab(self):
        self._close_tab(self._active_tab)

    # ─────────────────────────────────────────
    #  Renommage d'onglet conversation
    # ─────────────────────────────────────────
    def _rename_conv_tab(self, tab: "ConversationTab"):
        dlg = tk.Toplevel(self.root)
        dlg.title("Renommer l'onglet")
        dlg.configure(bg=BG2)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        dlg.lift()

        tk.Label(dlg, text="Nom de l'onglet :", bg=BG2, fg=FG,
                 font=("Segoe UI", 9)).pack(padx=16, pady=(14, 4), anchor="w")

        var = tk.StringVar(value=tab._custom_label or tab._get_title())
        entry = tk.Entry(dlg, textvariable=var, bg=BG3, fg=FG,
                         insertbackground=FG, relief="flat", font=("Segoe UI", 9),
                         bd=4, width=34)
        entry.pack(padx=16, pady=4)
        entry.select_range(0, "end")
        entry.focus_set()

        def _apply():
            label = var.get().strip()
            tab._custom_label = label
            display = f"  {label}  " if label else "  ·  "
            try:
                self._conv_notebook.tab(tab.frame, text=display)
            except Exception:
                pass
            self._chat_save_meta()
            dlg.destroy()

        entry.bind("<Return>", lambda e: _apply())
        entry.bind("<Escape>", lambda e: dlg.destroy())

        btn_frame = tk.Frame(dlg, bg=BG2)
        btn_frame.pack(pady=(8, 14))
        tk.Button(btn_frame, text="OK", command=_apply,
                  bg=BG4, fg=ACCENT, activebackground=BG3,
                  relief="flat", cursor="hand2",
                  padx=16, pady=3, bd=0,
                  font=("Segoe UI", 9, "bold")).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Annuler", command=dlg.destroy,
                  bg=BG3, fg=FG_DIM, activebackground=BG4,
                  relief="flat", cursor="hand2",
                  padx=16, pady=3, bd=0,
                  font=("Segoe UI", 9)).pack(side="left", padx=6)

        dlg.update_idletasks()
        rx = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{rx}+{ry}")

    # ─────────────────────────────────────────
    #  Persistance méta conversations (onglets ouverts)
    # ─────────────────────────────────────────
    _CHAT_META = DATA_DIR / "conversations" / "chat_meta.json"

    def _chat_save_meta(self):
        """Persiste l'état des onglets conversation ouverts (filtre les onglets vides)."""
        import json
        if self._restoring_chat:
            return
        try:
            tabs_data = [
                {"conv_id": t.conv_id, "label": t._custom_label}
                for t in self._conv_tabs
                if t.conv_id or t._custom_label   # ignorer les onglets vides et non nommés
            ]
            with open(self._CHAT_META, "w", encoding="utf-8") as f:
                json.dump({"tabs": tabs_data}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.log(f"Chat meta erreur : {e}")

    def _chat_load_meta(self) -> list[dict]:
        """Retourne la liste des onglets conversation à restaurer."""
        import json
        try:
            with open(self._CHAT_META, "r", encoding="utf-8") as f:
                return json.load(f).get("tabs", [])
        except Exception:
            return []

    def _close_tab(self, tab: "ConversationTab | None"):
        """Ferme un onglet donné. Si c'est le dernier, réinitialise sans fermer."""
        if tab is None:
            return
        if len(self._conv_tabs) <= 1:
            # Dernier onglet : réinitialisation sans fermeture
            tab._chat_history.clear()
            tab._chat_box.configure(state="normal")
            tab._chat_box.delete("1.0", "end")
            tab._chat_box.configure(state="disabled")
            tab._chat_detach_file()
            tab.conv_id = None
            tab._custom_label = ""
            try:
                self._conv_notebook.tab(tab.frame, text="  ·  ")
            except Exception:
                pass
            tab._chat_append("Conversation effacée.", "sys_tag")
            self._chat_save_meta()
            return
        # Plusieurs onglets : sélectionner un voisin AVANT de forget,
        # pour empêcher le notebook d'auto-sélectionner l'onglet "+".
        nb          = self._conv_notebook
        all_tab_ids = list(nb.tabs())
        plus_id     = str(self._plus_frame)
        tab_pos     = all_tab_ids.index(str(tab.frame))

        fallback = None
        for candidate in [tab_pos - 1, tab_pos + 1]:
            if 0 <= candidate < len(all_tab_ids) and all_tab_ids[candidate] != plus_id:
                fallback = candidate
                break
        if fallback is not None:
            nb.select(fallback)

        self._conv_tabs.remove(tab)
        nb.forget(tab.frame)
        tab.frame.destroy()
        self._chat_save_meta()

    @property
    def _active_tab(self) -> "ConversationTab | None":
        if not self._conv_tabs or self._conv_notebook is None:
            return None
        current = self._conv_notebook.select()
        for t in self._conv_tabs:
            if str(t.frame) == current:
                return t
        return self._conv_tabs[-1] if self._conv_tabs else None

    # ─────────────────────────────────────────
    #  Modèles (propagés à tous les onglets)
    # ─────────────────────────────────────────
    def _refresh_models(self):
        def fetch():
            try:
                result = _ollama.list()
                models = [m.model for m in result.models]
                if not models:
                    models = ["(aucun modèle trouvé)"]
            except Exception as e:
                models = [f"Erreur : {e}"]
            if self.root:
                self.root.after(0, lambda: self._set_models_all(models))
        threading.Thread(target=fetch, daemon=True).start()

    def _set_models_all(self, models: list[str]):
        for tab in self._conv_tabs:
            tab.set_models(models)

    # ─────────────────────────────────────────
    #  Chargement de la dernière conversation
    # ─────────────────────────────────────────
    def _load_latest_conversation(self):
        data = conversations.load_latest()
        if not data or not data.get("messages"):
            return
        tab = self._active_tab
        if tab:
            tab.load_conversation(data)

    # ─────────────────────────────────────────
    #  Tab Console (inner notebook multi-onglets)
    # ─────────────────────────────────────────
    _CONSOLES_META = _CONSOLES_DIR / "meta.json"

    def _console_save_meta(self):
        """Persiste l'ordre des consoles actives (filtre les onglets vides et non nommés)."""
        import json
        try:
            order = []
            for t in self._console_tabs:
                if not t._label and not t._history:
                    # Vide et sans nom : supprimer le fichier slot s'il existe
                    try:
                        t._slot_path().unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                t._persist()   # s'assurer que le slot est à jour
                order.append(t.slot)
            with open(self._CONSOLES_META, "w", encoding="utf-8") as f:
                json.dump({"order": order}, f)
        except Exception as e:
            logger.log(f"Console meta erreur : {e}")

    def _console_load_meta(self) -> list[int]:
        """Retourne la liste ordonnée des slots à restaurer."""
        import json
        try:
            with open(self._CONSOLES_META, "r", encoding="utf-8") as f:
                order = json.load(f).get("order", [])
            # Garder uniquement les slots dont le fichier existe encore
            return [s for s in order
                    if (_CONSOLES_DIR / f"console_{s}.json").exists()]
        except Exception:
            # Fallback : scan du répertoire trié
            return sorted(
                int(p.stem.split("_")[1])
                for p in _CONSOLES_DIR.glob("console_*.json")
                if p.stem.split("_")[1].isdigit()
            )

    def _build_console_tab(self, parent):
        self._console_notebook = ttk.Notebook(parent, style="Inner.TNotebook")
        self._console_notebook.pack(fill="both", expand=True)

        self._plus_frame_console = ttk.Frame(self._console_notebook)
        self._console_notebook.add(self._plus_frame_console, text="  ＋  ")

        self._console_notebook.bind("<<NotebookTabChanged>>",
                                     self._on_console_tab_changed)
        self._console_notebook.bind("<Button-3>",
                                     self._on_console_tab_right_click)

        # Restaurer les slots en ordre, sans déclencher _on_console_tab_changed
        self._restoring_consoles = True
        slots = self._console_load_meta()
        if slots:
            for slot in slots:
                self._new_console_tab(slot=slot)
        else:
            self._new_console_tab()
        self._restoring_consoles = False

        # Forcer "+" à la fin (les onglets restaurés ont été ajoutés après "+"
        # via notebook.add() donc il faut remettre "+" à la toute fin)
        try:
            nb = self._console_notebook
            nb.forget(self._plus_frame_console)
            nb.add(self._plus_frame_console, text="  ＋  ")
        except Exception:
            pass

        # Sélectionner le premier onglet (pas le +)
        if self._console_tabs:
            self._console_notebook.select(self._console_tabs[0].frame)

    def _new_console_tab(self, insert_after: int | None = None,
                          slot: int | None = None):
        nb = self._console_notebook
        tab = ConsoleTab(nb, self, slot=slot)

        # Toujours repositionner avant "+" (même pendant la restauration,
        # car notebook.add() place le tab après "+" qui a été ajouté en premier)
        try:
            all_tabs = list(nb.tabs())
            plus_id  = str(self._plus_frame_console)
            plus_idx = all_tabs.index(plus_id) if plus_id in all_tabs \
                       else len(all_tabs)
            target   = (insert_after + 1) if insert_after is not None \
                       else plus_idx
            target   = min(target, plus_idx)
            tab_text = nb.tab(tab.frame, "text")
            nb.forget(tab.frame)
            nb.insert(target, tab.frame, text=tab_text)
        except Exception:
            pass

        self._console_tabs.append(tab)
        if not self._restoring_consoles:
            nb.select(tab.frame)
            self._console_save_meta()
        return tab

    def _on_console_tab_changed(self, event):
        if self._restoring_consoles or not self._plus_frame_console:
            return
        try:
            if self._console_notebook.select() == str(self._plus_frame_console):
                self._new_console_tab()
        except Exception:
            pass

    def _on_console_tab_right_click(self, event):
        nb = self._console_notebook
        try:
            idx = nb.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        tab_id = nb.tabs()[idx]

        if tab_id == str(self._plus_frame_console):
            menu = tk.Menu(self.root, tearoff=0,
                           bg=BG3, fg=FG, activebackground=BG4,
                           activeforeground=ACCENT, relief="flat", bd=0,
                           font=("Segoe UI", 9))
            menu.add_command(label="Nouvelle console",
                             command=self._new_console_tab)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return

        target_tab: "ConsoleTab | None" = None
        for t in self._console_tabs:
            if str(t.frame) == tab_id:
                target_tab = t
                break
        if target_tab is None:
            return

        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        menu.add_command(label="Nouvelle console",
                         command=lambda: self._new_console_tab(insert_after=idx))
        menu.add_command(label="Renommer cet onglet…",
                         command=lambda: self._rename_console_tab(target_tab))
        menu.add_separator()
        menu.add_command(label="Fermer cette console",
                         command=lambda: self._close_console_tab(target_tab))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _close_console_tab(self, tab: "ConsoleTab"):
        if tab._proc:
            try:
                tab._proc.terminate()
            except Exception:
                pass

        if len(self._console_tabs) <= 1:
            # Dernier onglet : vider sans fermer
            tab._clear()
            tab._write_sys("Console effacée.")
            tab._history.clear()
            tab._persist()
            self._console_save_meta()
            return

        nb          = self._console_notebook
        all_tab_ids = list(nb.tabs())
        plus_id     = str(self._plus_frame_console)
        tab_pos     = all_tab_ids.index(str(tab.frame))

        fallback = None
        for candidate in [tab_pos - 1, tab_pos + 1]:
            if 0 <= candidate < len(all_tab_ids) \
               and all_tab_ids[candidate] != plus_id:
                fallback = candidate
                break
        if fallback is not None:
            nb.select(fallback)

        self._console_tabs.remove(tab)
        nb.forget(tab.frame)
        # Supprimer le fichier de persistance
        try:
            tab._slot_path().unlink(missing_ok=True)
        except Exception:
            pass
        self._console_save_meta()
        tab.frame.destroy()

    # ─────────────────────────────────────────
    #  Renommage d'onglet console
    # ─────────────────────────────────────────
    def _rename_console_tab(self, tab: "ConsoleTab"):
        dlg = tk.Toplevel(self.root)
        dlg.title("Renommer l'onglet")
        dlg.configure(bg=BG2)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        dlg.lift()

        tk.Label(dlg, text="Nom de l'onglet :", bg=BG2, fg=FG,
                 font=("Segoe UI", 9)).pack(padx=16, pady=(14, 4), anchor="w")

        var = tk.StringVar(value=tab._label)
        entry = tk.Entry(dlg, textvariable=var, bg=BG3, fg=FG,
                         insertbackground=FG, relief="flat", font=("Segoe UI", 9),
                         bd=4, width=34)
        entry.pack(padx=16, pady=4)
        entry.select_range(0, "end")
        entry.focus_set()

        def _apply():
            label = var.get().strip()
            tab._label = label
            display = f"  {label}  " if label else "  ·  "
            try:
                self._console_notebook.tab(tab.frame, text=display)
            except Exception:
                pass
            tab._persist()   # persiste le label dans le JSON du slot
            dlg.destroy()

        entry.bind("<Return>", lambda e: _apply())
        entry.bind("<Escape>", lambda e: dlg.destroy())

        btn_frame = tk.Frame(dlg, bg=BG2)
        btn_frame.pack(pady=(8, 14))
        tk.Button(btn_frame, text="OK", command=_apply,
                  bg=BG4, fg=ACCENT, activebackground=BG3,
                  relief="flat", cursor="hand2",
                  padx=16, pady=3, bd=0,
                  font=("Segoe UI", 9, "bold")).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Annuler", command=dlg.destroy,
                  bg=BG3, fg=FG_DIM, activebackground=BG4,
                  relief="flat", cursor="hand2",
                  padx=16, pady=3, bd=0,
                  font=("Segoe UI", 9)).pack(side="left", padx=6)

        dlg.update_idletasks()
        rx = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{rx}+{ry}")

    # ─────────────────────────────────────────
    #  Status bar
    # ─────────────────────────────────────────
    def _build_status_bar(self):
        sep = tk.Frame(self.root, bg=BORDER, height=1)
        sep.pack(fill="x")
        bar = tk.Frame(self.root, bg=BG2, pady=5)
        bar.pack(fill="x")
        tk.Label(bar, text="● actif", font=("Segoe UI", 8),
                 bg=BG2, fg=GREEN).pack(side="left", padx=14)
        self._clock_lbl = tk.Label(bar, text="", font=("Consolas", 8),
                                    bg=BG2, fg=FG_DIM)
        self._clock_lbl.pack(side="right", padx=14)
        self._tick_clock()

    # ─────────────────────────────────────────
    #  Logs
    # ─────────────────────────────────────────
    def _log_context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        menu.add_command(label="Copier la sélection", command=self._log_copy_selection)
        menu.add_command(label="Copier tout", command=self._log_copy)
        menu.add_separator()
        menu.add_command(label="Effacer", command=self._clear_logs)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _log_copy_selection(self):
        try:
            text = self._log_box.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _log_copy(self):
        text = self._log_box.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _enqueue_log(self, line: str):
        with self._lock:
            self._log_queue.append(line)

    def _poll_logs(self):
        with self._lock:
            lines, self._log_queue = self._log_queue, []
        if lines and self.root:
            self._log_box.configure(state="normal")
            for line in lines:
                self._log_box.insert("end", line + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        if self.root:
            self.root.after(300, self._poll_logs)

    def _clear_logs(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ─────────────────────────────────────────
    #  Tâches & Hotkeys
    # ─────────────────────────────────────────
    def _task_context_menu(self, event):
        row = self._task_tree.identify_row(event.y)
        idx = self._task_tree.index(row) if row else None
        if row:
            self._task_tree.selection_set(row)

        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        # "Nouvelle tâche" s'insère juste après la ligne cliquée (ou en fin si zone vide)
        menu.add_command(label="Nouvelle tâche…",
                         command=lambda: self._task_dialog(insert_after=idx))
        if row and idx is not None:
            entry = task_manager.registered[idx] if idx < len(task_manager.registered) else None
            menu.add_separator()
            menu.add_command(label="Exécuter maintenant",
                             command=lambda i=idx: self._task_run_now(i))
            if entry and entry.get("custom"):
                menu.add_command(label="Modifier…",
                                 command=lambda i=idx: self._task_dialog(edit_idx=i))
                menu.add_command(label="Supprimer",
                                 command=lambda i=idx: self._task_delete(i))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _task_run_now(self, idx: int):
        try:
            entry = task_manager.registered[idx]
            threading.Thread(target=entry["job"].job_func, daemon=True).start()
            logger.log(f"Tâche exécutée manuellement : {entry['label']}")
        except Exception as e:
            logger.log(f"Erreur exécution manuelle : {e}")

    def _task_delete(self, idx: int):
        try:
            entry = task_manager.registered[idx]
            import schedule as _sched
            _sched.cancel_job(entry["job"])
            task_manager.registered.pop(idx)
            self._task_persist()
            self._refresh_tasks()
            logger.log(f"Tâche supprimée : {entry['label']}")
        except Exception as e:
            logger.log(f"Erreur suppression tâche : {e}")

    # ── Persistance des tâches custom ─────────
    def _task_persist(self):
        custom = []
        for e in task_manager.registered:
            if e.get("custom"):
                custom.append({
                    "label":          e["label"],
                    "sched_type":     e["sched_type"],
                    "interval_value": e.get("interval_value"),
                    "interval_unit":  e.get("interval_unit"),
                    "at_time":        e.get("at_time"),
                    "action_code":    e.get("action_code", ""),
                })
        settings.set("custom_tasks", custom)

    def _task_load_persisted(self):
        saved = settings.get("custom_tasks", [])
        for t in saved:
            self._task_register_custom(
                label        = t.get("label", "Tâche"),
                sched_type   = t.get("sched_type", "interval"),
                interval_val = t.get("interval_value", 30),
                interval_unit= t.get("interval_unit", "minutes"),
                at_time      = t.get("at_time", "09:00"),
                action_code  = t.get("action_code", ""),
                persist      = False,   # déjà persisté
            )

    def _task_register_custom(self, label, sched_type, interval_val,
                               interval_unit, at_time, action_code,
                               persist=True, insert_at=None):
        import schedule as _sched

        action_code = action_code.strip()

        def make_fn(code):
            def fn():
                try:
                    exec(code, {"logger": logger,   # noqa: S102
                                "__builtins__": __builtins__})
                except Exception as exc:
                    logger.log(f"⚠ Erreur tâche '{label}' : {exc}")
            return fn

        fn = make_fn(action_code)

        if sched_type == "interval":
            v = int(interval_val) if interval_val else 1
            if interval_unit == "secondes":
                job = _sched.every(v).seconds.do(fn)
                period_str = f"Toutes les {v} s"
            elif interval_unit == "heures":
                job = _sched.every(v).hours.do(fn)
                period_str = f"Toutes les {v} h"
            else:
                job = _sched.every(v).minutes.do(fn)
                period_str = f"Toutes les {v} min"
        else:
            job = _sched.every().day.at(at_time).do(fn)
            period_str = f"Chaque jour à {at_time}"

        entry = {
            "label":          label,
            "job":            job,
            "custom":         True,
            "sched_type":     sched_type,
            "interval_value": interval_val,
            "interval_unit":  interval_unit,
            "at_time":        at_time,
            "action_code":    action_code,
            "period_str":     period_str,
        }

        if insert_at is not None:
            task_manager.registered.insert(insert_at, entry)
        else:
            task_manager.registered.append(entry)

        logger.log(f"Tâche custom ajoutée : {label} ({period_str})")

        if persist:
            self._task_persist()
        self._refresh_tasks()

    # ── Dialog création / édition de tâche ───────────────
    def _task_dialog(self, insert_after: int | None = None,
                     edit_idx: int | None = None):
        """Dialog unifiée : création (edit_idx=None) ou édition (edit_idx=<index>)."""
        editing = edit_idx is not None
        existing = task_manager.registered[edit_idx] if editing else {}

        dlg = tk.Toplevel(self.root)
        dlg.title("Modifier la tâche" if editing else "Nouvelle tâche planifiée")
        dlg.configure(bg=BG2)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.attributes("-topmost", True)

        pad = {"padx": 14, "pady": 5}

        # ── Titre ──
        tk.Label(dlg, text="Titre", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", **pad)
        lbl_var = tk.StringVar(value=existing.get("label", "Ma tâche"))
        tk.Entry(dlg, textvariable=lbl_var, bg=BG3, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 9), width=36,
                 bd=4).grid(row=0, column=1, columnspan=3, sticky="we", **pad)

        # ── Type de périodicité ──
        tk.Label(dlg, text="Périodicité", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 8)).grid(row=1, column=0, sticky="w", **pad)
        sched_var = tk.StringVar(value=existing.get("sched_type", "interval"))

        rb_frame = tk.Frame(dlg, bg=BG2)
        rb_frame.grid(row=1, column=1, columnspan=3, sticky="w", padx=14, pady=2)
        tk.Radiobutton(rb_frame, text="Intervalle", variable=sched_var,
                       value="interval", bg=BG2, fg=FG, selectcolor=BG3,
                       activebackground=BG2, font=("Segoe UI", 9),
                       command=lambda: _toggle()).pack(side="left")
        tk.Radiobutton(rb_frame, text="Heure fixe", variable=sched_var,
                       value="fixed", bg=BG2, fg=FG, selectcolor=BG3,
                       activebackground=BG2, font=("Segoe UI", 9),
                       command=lambda: _toggle()).pack(side="left", padx=(16, 0))

        # ── Sous-panneau Intervalle ──
        iv_frame = tk.Frame(dlg, bg=BG2)
        iv_frame.grid(row=2, column=1, columnspan=3, sticky="w", padx=14, pady=2)
        tk.Label(iv_frame, text="Toutes les", bg=BG2, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        iv_val = tk.StringVar(value=str(existing.get("interval_value", "30")))
        tk.Spinbox(iv_frame, from_=1, to=9999, textvariable=iv_val, width=6,
                   bg=BG3, fg=FG, buttonbackground=BG4, relief="flat",
                   insertbackground=FG, font=("Segoe UI", 9)).pack(side="left", padx=6)
        iv_unit = tk.StringVar(value=existing.get("interval_unit", "minutes"))
        ttk.Combobox(iv_frame, textvariable=iv_unit, width=10,
                     values=["secondes", "minutes", "heures"],
                     state="readonly", style="Chat.TCombobox").pack(side="left")

        # ── Sous-panneau Heure fixe ──
        fx_frame = tk.Frame(dlg, bg=BG2)
        fx_frame.grid(row=2, column=1, columnspan=3, sticky="w", padx=14, pady=2)
        fx_frame.grid_remove()
        tk.Label(fx_frame, text="Heure (HH:MM)", bg=BG2, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        fx_time = tk.StringVar(value=existing.get("at_time", "09:00"))
        tk.Entry(fx_frame, textvariable=fx_time, width=8, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", font=("Segoe UI", 9),
                 bd=4).pack(side="left", padx=8)

        def _toggle():
            if sched_var.get() == "interval":
                iv_frame.grid()
                fx_frame.grid_remove()
            else:
                iv_frame.grid_remove()
                fx_frame.grid()

        # Afficher le bon panneau si on est en mode édition
        _toggle()

        # ── Action (code Python) ──
        tk.Label(dlg, text="Action (Python)", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 8)).grid(row=3, column=0, sticky="nw",
                                             padx=14, pady=(8, 2))
        code_box = tk.Text(dlg, height=6, width=44, bg=BG3, fg=FG,
                           insertbackground=FG, relief="flat",
                           font=("Consolas", 9), bd=4, wrap="none")
        code_box.grid(row=3, column=1, columnspan=3, padx=14, pady=(8, 4))
        default_code = existing.get("action_code",
                                    'logger.log("Ma tâche s\'exécute ✓")')
        code_box.insert("1.0", default_code)

        # ── Boutons ──
        btn_frame = tk.Frame(dlg, bg=BG2)
        btn_frame.grid(row=4, column=0, columnspan=4, pady=(4, 12))

        def _ok():
            label      = lbl_var.get().strip() or "Tâche"
            sched_type = sched_var.get()
            iv         = iv_val.get()
            unit       = iv_unit.get()
            at         = fx_time.get().strip()
            code       = code_box.get("1.0", "end").strip()
            dlg.destroy()

            if editing:
                # Annuler l'ancien job et remplacer l'entrée au même index
                import schedule as _sched
                try:
                    _sched.cancel_job(task_manager.registered[edit_idx]["job"])
                except Exception:
                    pass
                task_manager.registered.pop(edit_idx)
                self._task_register_custom(label, sched_type, iv, unit, at, code,
                                           insert_at=edit_idx)
                logger.log(f"Tâche modifiée : {label}")
            else:
                # Insérer juste après insert_after, ou à la fin
                pos = (insert_after + 1) if insert_after is not None else None
                self._task_register_custom(label, sched_type, iv, unit, at, code,
                                           insert_at=pos)

        btn_label = "Enregistrer" if editing else "Créer"
        tk.Button(btn_frame, text=btn_label, command=_ok,
                  bg=BG4, fg=ACCENT, activebackground=BG3,
                  relief="flat", cursor="hand2", padx=18, pady=4, bd=0,
                  font=("Segoe UI", 9, "bold")).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Annuler", command=dlg.destroy,
                  bg=BG3, fg=FG_DIM, activebackground=BG4,
                  relief="flat", cursor="hand2", padx=18, pady=4, bd=0,
                  font=("Segoe UI", 9)).pack(side="left", padx=6)

        dlg.update_idletasks()
        rx = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{rx}+{ry}")

    def _hk_context_menu(self, event):
        row = self._hk_tree.identify_row(event.y)
        if not row:
            return
        self._hk_tree.selection_set(row)
        idx = self._hk_tree.index(row)
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        menu.add_command(label="Exécuter maintenant",
                         command=lambda: self._hk_run_now(idx))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _hk_run_now(self, idx: int):
        try:
            entry = hotkey_manager.registered[idx]
            cb = entry.get("callback")
            if cb:
                threading.Thread(target=cb, daemon=True).start()
            logger.log(f"Raccourci déclenché manuellement : {entry['combo']}")
        except Exception as e:
            logger.log(f"Erreur déclenchement manuel : {e}")

    def _refresh_tasks(self):
        if not self.root:
            return
        self._task_tree.delete(*self._task_tree.get_children())
        for entry in task_manager.registered:
            job = entry["job"]
            nxt = job.next_run.strftime("%d/%m %H:%M:%S") if job.next_run else "—"
            # period_str : fourni pour les tâches custom, dérivé du job sinon
            period_str = entry.get("period_str")
            if not period_str:
                try:
                    period_str = str(job.interval) + " " + str(job.unit)
                    if job.at_time:
                        period_str = f"Chaque jour à {job.at_time}"
                except Exception:
                    period_str = "—"
            tag = "custom" if entry.get("custom") else ""
            self._task_tree.insert("", "end",
                                   values=(entry["label"], period_str, nxt),
                                   tags=(tag,))
        self._task_tree.tag_configure("custom", foreground="#A8CCEA")
        self.root.after(5000, self._refresh_tasks)

    def _refresh_hotkeys(self):
        self._hk_tree.delete(*self._hk_tree.get_children())
        for entry in hotkey_manager.registered:
            self._hk_tree.insert("", "end", values=(entry["combo"], entry["label"]))

    def _tick_clock(self):
        if self.root:
            self._clock_lbl.config(
                text=datetime.datetime.now().strftime("%d/%m/%Y  %H:%M:%S"))
            self.root.after(1000, self._tick_clock)

    # ─────────────────────────────────────────
    #  Cycle de vie
    # ─────────────────────────────────────────
    def show(self):
        if self.root:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._refresh_hotkeys()

    def hide(self):
        if self.root:
            self.root.withdraw()

    def run(self):
        self.build()
        self.root.mainloop()

    def destroy(self):
        if self.root:
            self.root.destroy()


# ─────────────────────────────────────────────
#  Application principale
# ─────────────────────────────────────────────
class TrayApp:
    def __init__(self):
        self.dashboard = DashboardWindow()
        self._tray = None  # type: ignore

    def _build_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir", self._on_open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Recharger", self._on_reload),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", self._on_quit),
        )
        self._tray = pystray.Icon("Boostache", create_tray_icon(), "Boostache", menu)

    def _run_tray(self):
        self._build_tray()
        self._tray.run()

    def _on_open(self, icon=None, item=None):
        if self.dashboard.root:
            self.dashboard.root.after(0, self.dashboard.show)

    def _on_reload(self, icon=None, item=None):
        logger.log("Rechargement en cours…")
        hotkey_manager.remove_all()
        import schedule
        schedule.clear()
        task_manager.registered.clear()
        hotkey_manager.registered.clear()
        try:
            importlib.reload(tasks)
            importlib.reload(bindings)
            tasks.register()
            bindings.register(open_dashboard_fn=self._on_open)
            # Recharger les tâches custom persistées
            self.dashboard._task_load_persisted()
            logger.log("Rechargement terminé ✓")
        except Exception as e:
            logger.log(f"Erreur lors du rechargement : {e}")
        if self.dashboard.root:
            self.dashboard.root.after(0, self.dashboard._refresh_hotkeys)

    def _on_quit(self, icon=None, item=None):
        logger.log("Arrêt de Boostache…")
        task_manager.stop()
        hotkey_manager.remove_all()
        if self._tray:
            self._tray.stop()
        if self.dashboard.root:
            self.dashboard.root.after(0, self.dashboard.destroy)

    def run(self):
        tasks.register()
        bindings.register(open_dashboard_fn=self._on_open)
        threading.Thread(target=self._run_tray, daemon=True).start()
        task_manager.start()
        self.dashboard.run()


# ─────────────────────────────────────────────
#  Point d'entrée
# ─────────────────────────────────────────────
if __name__ == "__main__":
    clear_cache()
    TrayApp().run()
