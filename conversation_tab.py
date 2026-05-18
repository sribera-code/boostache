"""
conversation_tab.py – Onglet de conversation (chat LLM via Ollama)
Encapsule l'état et les widgets d'une seule conversation.
"""

import os
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from engine import logger, tts
from storage import settings, conversations, CACHE_DIR
from theme import (
    BG2, BG3, BG4, BG_CHAT, FG, FG_DIM, ACCENT, GREEN,
)
from ui_utils import (
    OLLAMA_OK, _ollama,
    DND_OK, DND_FILES,
    strip_markdown, parse_drop_data,
)


class ConversationTab:
    """Encapsule l'état et les widgets d'une seule conversation."""

    _counter = 0

    def __init__(self, inner_notebook: ttk.Notebook, dashboard):
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
        # Zone messages (packé après input_frame pour garantir que la zone de
        # saisie ait toujours sa hauteur naturelle et ne soit pas coupée)
        msg_frame = tk.Frame(self.frame, bg=BG_CHAT)

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
        self._chat_box.tag_configure("md_table_head", foreground="#FFFFFF",
                                      font=("Consolas", 9),
                                      lmargin1=12, lmargin2=12)
        self._chat_box.tag_configure("md_table_row", foreground="#A8CCEA",
                                      font=("Consolas", 9),
                                      lmargin1=12, lmargin2=12)
        self._chat_box.tag_configure("md_table_border", foreground="#555555",
                                      font=("Consolas", 9),
                                      lmargin1=12, lmargin2=12)
        self._chat_box.tag_configure("md_table_bold", foreground="#D0E8F8",
                                      font=("Consolas", 9))

        self._chat_box.bind("<Control-c>", self._chat_copy_selection)
        self._chat_box.bind("<Control-C>", self._chat_copy_selection)
        self._chat_box.bind("<MouseWheel>", self._on_chat_scroll)
        self._chat_box.bind("<Button-3>", self._chat_context_menu)

        # Barre fichiers attachés (initialement cachée)
        self._file_bar = tk.Frame(self.frame, bg=BG2)

        # Zone de saisie — packée en side="bottom" avant msg_frame
        input_frame = tk.Frame(self.frame, bg=BG2, pady=6)
        input_frame.pack(side="bottom", fill="x")

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
        self._tts_btn.bind("<Button-3>", self._tts_btn_menu)

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
            bg=BG2, fg=GREEN, activebackground=BG2, activeforeground=GREEN,
            relief="flat", cursor="hand2", padx=4, pady=0, bd=0,
            font=("Segoe UI", 24))
        self._chat_send_btn.pack(side="right", padx=(4, 8))

        msg_frame.pack(fill="both", expand=True)
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
        model_menu.add_separator()
        model_menu.add_command(label="Rafraîchir les modèles",
                               command=self.dashboard._refresh_models)

        menu.add_cascade(label=f"Modèle : {current or '—'}", menu=model_menu)
        menu.add_separator()
        menu.add_command(label="Copier la sélection",
                         command=lambda: self._chat_copy_selection(None))
        menu.add_command(label="Copier tout", command=self._chat_copy_all)
        menu.add_separator()
        has_sel = self._has_selection()
        menu.add_command(label="Lire la sélection",
                         command=self._tts_speak_selection,
                         state=("normal" if has_sel else "disabled"))
        menu.add_command(label="Lire la dernière réponse",
                         command=lambda: self._tts_speak_mode("last"))
        menu.add_command(label="Tout lire",
                         command=lambda: self._tts_speak_mode("all"))
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

            system_prompt = settings.get("system_prompt", "").strip()
            if system_prompt:
                history = [{"role": "system", "content": system_prompt}] + history
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
            # Tableau markdown
            elif line.startswith("|") and line.count("|") >= 2:
                table_lines = []
                while i < len(lines) and lines[i].startswith("|") and lines[i].count("|") >= 2:
                    table_lines.append(lines[i])
                    i += 1
                self._render_md_table(table_lines)
                continue
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

    def _insert_table_inline(self, text: str, base_tag: str):
        """Inline renderer pour les cellules de tableau — uniquement Consolas."""
        pattern = r'(\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)'
        parts = re.split(pattern, text)
        for part in parts:
            if part.startswith("***") and part.endswith("***") and len(part) > 6:
                self._chat_box.insert("end", part[3:-3], "md_table_bold")
            elif part.startswith("**") and part.endswith("**") and len(part) > 4:
                self._chat_box.insert("end", part[2:-2], "md_table_bold")
            elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                self._chat_box.insert("end", part[1:-1], base_tag)
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                self._chat_box.insert("end", part[1:-1], base_tag)
            else:
                self._chat_box.insert("end", part, base_tag)

    def _render_md_table(self, lines: list):
        def strip_inline(t):
            t = re.sub(r'\*\*\*([^*]+)\*\*\*', r'\1', t)
            t = re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
            t = re.sub(r'\*([^*]+)\*', r'\1', t)
            t = re.sub(r'`([^`]+)`', r'\1', t)
            return t

        def parse_row(line):
            cells = line.strip().strip("|").split("|")
            return [c.strip() for c in cells]

        def is_sep(line):
            return all(re.match(r'^:?-+:?$', c.strip()) for c in parse_row(line) if c.strip())

        rows = []  # list of ('header'|'sep'|'row', cells)
        for idx, line in enumerate(lines):
            if is_sep(line):
                rows.append(('sep', []))
            elif idx == 0:
                rows.append(('header', parse_row(line)))
            else:
                rows.append(('row', parse_row(line)))

        data_rows = [(t, c) for t, c in rows if t != 'sep']
        if not data_rows:
            return
        num_cols = max(len(c) for _, c in data_rows)
        col_widths = [1] * num_cols
        for _, cells in data_rows:
            for j, cell in enumerate(cells[:num_cols]):
                col_widths[j] = max(col_widths[j], len(strip_inline(cell)))

        def ins_border(text):
            self._chat_box.insert("end", text, "md_table_border")

        ins_border("┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐\n")

        for row_type, cells in rows:
            if row_type == 'sep':
                ins_border("├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤\n")
                continue
            is_header = row_type == 'header'
            cell_tag = "md_table_head" if is_header else "md_table_row"
            ins_border("│")
            for j in range(num_cols):
                cell = cells[j] if j < len(cells) else ""
                plain_len = len(strip_inline(cell))
                pad = col_widths[j] - plain_len
                ins_border(" ")
                self._insert_table_inline(cell, cell_tag)
                ins_border(" " * (pad + 1) + "│")
            ins_border("\n")

        ins_border("└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘\n")

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
        """Bouton 🔊 : utilise le mode actif (last / all)."""
        if self._tts_active:
            tts.stop()
            self._tts_reset()
        else:
            mode = settings.get("tts_mode_chat", "last")
            self._tts_speak_mode(mode)

    def _tts_speak_mode(self, mode: str):
        text = self._get_tts_text(mode)
        if not text:
            return
        self._tts_start(text)

    def _tts_speak_selection(self):
        text = self._get_selection_text()
        if not text:
            return
        self._tts_start(text)

    def _tts_start(self, text: str):
        if self._tts_active:
            tts.stop()
        self._tts_active = True
        self._tts_btn.configure(text="⏹", fg="#E07070")
        tts.speak(text, on_done=lambda: self.root.after(0, self._tts_reset))

    def _tts_reset(self):
        self._tts_active = False
        try:
            self._tts_btn.configure(text="🔊", fg=FG)
        except Exception:
            pass

    def _get_tts_text(self, mode: str = "last") -> str:
        if mode == "all":
            parts = []
            for msg in self._chat_history:
                role = msg.get("role")
                if role in ("user", "assistant"):
                    parts.append(strip_markdown(msg.get("content", "")))
            return "\n\n".join(p for p in parts if p)
        # mode "last" par défaut
        for msg in reversed(self._chat_history):
            if msg.get("role") == "assistant":
                return strip_markdown(msg.get("content", ""))
        return ""

    def _has_selection(self) -> bool:
        try:
            self._chat_box.index(tk.SEL_FIRST)
            return True
        except tk.TclError:
            return False

    def _get_selection_text(self) -> str:
        try:
            return self._chat_box.get(tk.SEL_FIRST, tk.SEL_LAST).strip()
        except tk.TclError:
            return ""

    def _tts_btn_menu(self, event):
        """Right-click sur le bouton 🔊 : choisir le mode par défaut."""
        current = settings.get("tts_mode_chat", "last")
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        menu.add_command(
            label=("✓  " if current == "last" else "    ") + "Lire la dernière réponse",
            command=lambda: settings.set("tts_mode_chat", "last"))
        menu.add_command(
            label=("✓  " if current == "all" else "    ") + "Tout lire",
            command=lambda: settings.set("tts_mode_chat", "all"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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
        self._file_bar.pack(side="bottom", fill="x", after=self._chat_input.master)

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
            window_hidden = False
            try:
                import time
                from PIL import ImageGrab, Image
                import keyboard
                try:
                    self.root.after(0, lambda: self.root.clipboard_clear())
                except Exception:
                    pass
                if self.root:
                    try:
                        self.root.after(0, lambda: self.root.withdraw())
                        window_hidden = True
                        time.sleep(0.25)
                    except Exception:
                        pass
                keyboard.send("windows+shift+s")
                deadline = time.time() + 30
                img = None
                while time.time() < deadline:
                    time.sleep(0.4)
                    try:
                        grabbed = ImageGrab.grabclipboard()
                        if isinstance(grabbed, list):
                            for f in grabbed:
                                try:
                                    img = Image.open(f)
                                    img.load()
                                    break
                                except Exception:
                                    continue
                        elif grabbed is not None:
                            img = grabbed
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
            finally:
                if window_hidden and self.root:
                    try:
                        self.root.after(0, lambda: (self.root.deiconify(), self.root.lift()))
                    except Exception:
                        pass
        threading.Thread(target=capture, daemon=True).start()

    def _chat_paste(self, event):
        try:
            from PIL import ImageGrab, Image
            import time as _t
            grabbed = ImageGrab.grabclipboard()
            img = None
            if isinstance(grabbed, list):
                for f in grabbed:
                    try:
                        img = Image.open(f)
                        img.load()
                        break
                    except Exception:
                        continue
            elif grabbed is not None:
                img = grabbed
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
        for path in parse_drop_data(event.data):
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
