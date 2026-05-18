"""
dashboard.py – Fenêtre principale de Boostache
Orchestre les onglets Conversation / Console / Historique / Tâches /
Raccourcis / Paramètres.
"""

import datetime
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

import customtkinter as ctk

from engine import logger, task_manager, hotkey_manager
from storage import settings, conversations, DATA_DIR
from theme import (
    BG, BG2, BG3, BG4, BG_LOG, BG_CHAT, BORDER,
    FG, FG_DIM, FG_LOG, FG_HEAD, ACCENT, ACCENT_HOVER, GREEN,
)
from ui_utils import OLLAMA_OK, _ollama, CTkRoot
from conversation_tab import ConversationTab
from console_tab import ConsoleTab, CONSOLES_DIR


class DashboardWindow:
    def __init__(self):
        self.root = None  # type: ignore
        self._log_queue: list[str] = []
        self._lock = threading.Lock()
        logger.subscribe(self._enqueue_log)

        self._conv_tabs: list[ConversationTab] = []
        self._conv_notebook: ttk.Notebook | None = None
        self._restoring_chat: bool = False

        self._console_tabs: list[ConsoleTab] = []
        self._console_notebook: ttk.Notebook | None = None
        self._plus_frame_console: ttk.Frame | None = None
        self._restoring_consoles: bool = False

    def build(self):
        self.root = CTkRoot()

        self.root.title("Boostache")
        self.root.geometry("900x620")
        self.root.minsize(720, 480)
        self.root.resizable(True, True)
        self.root.configure(fg_color=BG2)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Unmap>", self._on_unmap)
        try:
            self.root.iconbitmap("boostache.ico")
        except Exception:
            pass
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
        style.configure("TNotebook", background=BG2, borderwidth=0, relief="flat",
                        tabmargins=[8, 6, 8, 0])
        style.configure("TNotebook.Tab", background=BG2, foreground=FG_DIM,
                        padding=[18, 9], font=("Segoe UI", 9), borderwidth=0,
                        focuscolor=BG2)
        style.map("TNotebook.Tab",
                  background=[("selected", BG), ("active", BG3)],
                  foreground=[("selected", ACCENT), ("active", FG)],
                  padding=[("selected", [18, 9]), ("active", [18, 9])],
                  font=[("selected", ("Segoe UI", 9, "bold"))])

        # Notebook interne (onglets de conversation)
        style.configure("Inner.TNotebook", background=BG, borderwidth=0, relief="flat",
                        tabmargins=[4, 4, 4, 0])
        style.configure("Inner.TNotebook.Tab", background=BG, foreground=FG_DIM,
                        padding=[12, 5], font=("Segoe UI", 8), borderwidth=0,
                        focuscolor=BG)
        style.map("Inner.TNotebook.Tab",
                  background=[("selected", BG_CHAT), ("active", BG3)],
                  foreground=[("selected", FG), ("active", FG)],
                  padding=[("selected", [12, 5]), ("active", [12, 5])],
                  font=[("selected", ("Segoe UI", 8, "bold"))])

        style.configure("TFrame", background=BG)
        style.configure("Treeview", background=BG3, foreground=FG,
                        fieldbackground=BG3, rowheight=30,
                        font=("Segoe UI", 9), borderwidth=0, relief="flat")
        style.configure("Treeview.Heading", background=BG2, foreground=FG_HEAD,
                        font=("Segoe UI", 9, "bold"), relief="flat", borderwidth=0,
                        padding=[8, 6])
        style.map("Treeview.Heading",
                  background=[("active", BG3)])
        style.map("Treeview",
                  background=[("selected", BG4)],
                  foreground=[("selected", ACCENT)])
        style.configure("Vertical.TScrollbar", background=BG3, troughcolor=BG2,
                        borderwidth=0, arrowcolor=FG_DIM, relief="flat",
                        darkcolor=BG2, lightcolor=BG4, gripcount=0, arrowsize=12)
        style.map("Vertical.TScrollbar",
                  background=[("active", BG4)],
                  arrowcolor=[("active", FG)])
        style.configure("Chat.TCombobox", fieldbackground=BG3, background=BG3,
                        foreground=FG, selectbackground=BG4, selectforeground=ACCENT,
                        borderwidth=0, arrowcolor=FG_DIM)
        style.map("Chat.TCombobox", fieldbackground=[("readonly", BG3)],
                  foreground=[("readonly", FG)])

    # ─────────────────────────────────────────
    #  Notebook principal
    # ─────────────────────────────────────────
    def _build_notebook(self):
        container = ctk.CTkFrame(self.root, fg_color=BG2, corner_radius=0)
        container.pack(fill="both", expand=True, padx=0, pady=0)
        nb = ttk.Notebook(container)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))

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
            bg=BG_LOG, fg=FG_LOG, font=("Consolas", 10),
            insertbackground=FG, relief="flat", borderwidth=0,
            selectbackground=BG4, selectforeground=ACCENT,
            padx=12, pady=10, highlightthickness=0)
        self._log_box.pack(fill="both", expand=True, padx=2, pady=2)
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

        # ── Paramètres ────────────────────────
        settings_frame = ttk.Frame(nb)
        nb.add(settings_frame, text="  Paramètres  ")
        self._build_settings_tab(settings_frame)

    # ─────────────────────────────────────────
    #  Tab Paramètres
    # ─────────────────────────────────────────
    def _build_settings_tab(self, parent):
        outer = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=0)
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(outer, text="Pré-prompt système",
                     text_color=FG_HEAD, anchor="w",
                     font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
                     ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(outer,
                     text="Ce texte est envoyé au LLM comme message système avant chaque conversation.",
                     text_color=FG_DIM, anchor="w", justify="left",
                     wraplength=600,
                     font=ctk.CTkFont(family="Segoe UI", size=11)
                     ).pack(fill="x", pady=(0, 10))

        prompt_card = ctk.CTkFrame(outer, fg_color=BG3, corner_radius=8)
        prompt_card.pack(fill="both", expand=True)

        self._system_prompt_box = tk.Text(
            prompt_card, height=12,
            bg=BG3, fg=FG, font=("Segoe UI", 10),
            relief="flat", borderwidth=0, wrap="word",
            padx=12, pady=10, insertbackground=FG,
            selectbackground=BG4, selectforeground=ACCENT,
            highlightthickness=0)
        self._system_prompt_box.pack(fill="both", expand=True, padx=2, pady=2)

        saved = settings.get("system_prompt", "")
        if saved:
            self._system_prompt_box.insert("1.0", saved)

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", pady=(14, 0))

        self._settings_status = ctk.CTkLabel(btn_row, text="",
                                              text_color=GREEN,
                                              font=ctk.CTkFont(family="Segoe UI", size=11))
        self._settings_status.pack(side="left")

        ctk.CTkButton(btn_row, text="Sauvegarder",
                       command=self._save_system_prompt,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER,
                       text_color="#1A1A1A",
                       corner_radius=8, width=120, height=32,
                       font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
                       ).pack(side="right")

    def _save_system_prompt(self):
        prompt = self._system_prompt_box.get("1.0", "end").strip()
        settings.set("system_prompt", prompt)
        self._settings_status.configure(text="✓ Sauvegardé")
        self.root.after(2000, lambda: self._settings_status.configure(text=""))

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
        self._conv_notebook.bind("<Double-Button-1>", self._on_conv_tab_double_click)

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
                         command=lambda t=target: self._inline_rename_tab(
                             self._conv_notebook, str(t.frame), False))
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
    #  Renommage inline d'onglet (double-clic)
    # ─────────────────────────────────────────
    _rename_popup: tk.Toplevel | None = None  # popup de renommage actif

    def _inline_rename_tab(self, nb: ttk.Notebook, tab_id: str, is_console: bool,
                            screen_x: int | None = None, screen_y: int | None = None):
        # Fermer un éventuel popup déjà ouvert
        if self._rename_popup is not None:
            try:
                self._rename_popup.destroy()
            except Exception:
                pass
            self._rename_popup = None
        try:
            bx, by, bw, bh = nb.bbox(tab_id)
        except Exception:
            return
        entry_w = max(bw, 180)
        entry_h = 28
        # Position absolue à l'écran
        if screen_x is not None and screen_y is not None:
            sx = screen_x
            sy = screen_y - entry_h // 2
        else:
            sx = nb.winfo_rootx() + bx
            sy = nb.winfo_rooty() + by
        current = nb.tab(tab_id, "text").strip()
        var = tk.StringVar(value=current)
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=ACCENT)
        popup.geometry(f"{entry_w}x{entry_h}+{sx}+{sy}")
        self._rename_popup = popup
        entry = tk.Entry(popup, textvariable=var, bg=BG3, fg=FG,
                         insertbackground=FG, relief="flat",
                         font=("Segoe UI", 9), bd=0,
                         selectbackground=BG4, selectforeground=ACCENT,
                         highlightthickness=0)
        entry.pack(fill="both", expand=True, padx=2, pady=2)
        entry.select_range(0, "end")
        entry.focus_set()
        _done = [False]

        def _apply(event=None):
            if _done[0]:
                return
            _done[0] = True
            self._rename_popup = None
            label = var.get().strip()
            try:
                popup.destroy()
            except Exception:
                pass
            display = f"  {label}  " if label else "  ·  "
            if is_console:
                for t in self._console_tabs:
                    if str(t.frame) == tab_id:
                        t._label = label
                        try:
                            nb.tab(tab_id, text=display)
                        except Exception:
                            pass
                        t._persist()
                        break
            else:
                for t in self._conv_tabs:
                    if str(t.frame) == tab_id:
                        t._custom_label = label
                        try:
                            nb.tab(tab_id, text=display)
                        except Exception:
                            pass
                        self._chat_save_meta()
                        break

        def _cancel(event=None):
            if _done[0]:
                return
            _done[0] = True
            self._rename_popup = None
            try:
                popup.destroy()
            except Exception:
                pass

        entry.bind("<Return>", _apply)
        entry.bind("<Escape>", _cancel)
        entry.bind("<FocusOut>", _apply)

    def _on_conv_tab_double_click(self, event):
        nb = self._conv_notebook
        try:
            idx = nb.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        tab_id = nb.tabs()[idx]
        if tab_id == str(self._plus_frame):
            return
        self._inline_rename_tab(nb, tab_id, is_console=False,
                                 screen_x=event.x_root, screen_y=event.y_root)

    def _on_console_tab_double_click(self, event):
        nb = self._console_notebook
        try:
            idx = nb.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        tab_id = nb.tabs()[idx]
        if tab_id == str(self._plus_frame_console):
            return
        self._inline_rename_tab(nb, tab_id, is_console=True,
                                 screen_x=event.x_root, screen_y=event.y_root)

    # ─────────────────────────────────────────
    #  Persistance méta conversations (onglets ouverts)
    # ─────────────────────────────────────────
    _CHAT_META = DATA_DIR / "conversations" / "chat_meta.json"

    def _chat_save_meta(self):
        """Persiste l'état des onglets conversation ouverts (filtre les onglets vides)."""
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
        try:
            with open(self._CHAT_META, "r", encoding="utf-8") as f:
                return json.load(f).get("tabs", [])
        except Exception:
            return []

    def _close_tab(self, tab):
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
    def _active_tab(self):
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
    _CONSOLES_META = CONSOLES_DIR / "meta.json"

    def _console_save_meta(self):
        """Persiste l'ordre des consoles actives (filtre les onglets vides et non nommés)."""
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
        try:
            with open(self._CONSOLES_META, "r", encoding="utf-8") as f:
                order = json.load(f).get("order", [])
            # Garder uniquement les slots dont le fichier existe encore
            return [s for s in order
                    if (CONSOLES_DIR / f"console_{s}.json").exists()]
        except Exception:
            # Fallback : scan du répertoire trié
            return sorted(
                int(p.stem.split("_")[1])
                for p in CONSOLES_DIR.glob("console_*.json")
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
        self._console_notebook.bind("<Double-Button-1>",
                                     self._on_console_tab_double_click)

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
                         command=lambda t=target_tab: self._inline_rename_tab(
                             self._console_notebook, str(t.frame), True))
        menu.add_separator()
        menu.add_command(label="Fermer cette console",
                         command=lambda: self._close_console_tab(target_tab))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _close_console_tab(self, tab: ConsoleTab):
        if tab._proc:
            try:
                tab._proc.terminate()
            except Exception:
                pass

        if len(self._console_tabs) <= 1:
            # Dernier onglet : réinitialiser sans fermer (même état qu'une nouvelle console)
            tab._clear()
            tab._history.clear()
            tab._label = ""
            try:
                self._console_notebook.tab(tab.frame, text="  ·  ")
            except Exception:
                pass
            tab._write_sys(tab._cwd)
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
    #  Status bar
    # ─────────────────────────────────────────
    def _build_status_bar(self):
        sep = tk.Frame(self.root, bg=BORDER, height=1)
        sep.pack(fill="x")
        bar = ctk.CTkFrame(self.root, fg_color=BG2, corner_radius=0, height=28)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="● actif",
                     text_color=GREEN,
                     font=ctk.CTkFont(family="Segoe UI", size=11)
                     ).pack(side="left", padx=16)
        self._clock_lbl = ctk.CTkLabel(bar, text="",
                                        text_color=FG_DIM,
                                        font=ctk.CTkFont(family="Consolas", size=11))
        self._clock_lbl.pack(side="right", padx=16)
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

        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Modifier la tâche" if editing else "Nouvelle tâche planifiée")
        dlg.configure(fg_color=BG2)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.after(50, dlg.grab_set)

        body = ctk.CTkFrame(dlg, fg_color=BG2, corner_radius=0)
        body.pack(fill="both", expand=True, padx=18, pady=16)

        small_font = ctk.CTkFont(family="Segoe UI", size=10)
        regular_font = ctk.CTkFont(family="Segoe UI", size=11)

        # ── Titre ──
        ctk.CTkLabel(body, text="Titre", text_color=FG_DIM, anchor="w",
                     font=small_font
                     ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        lbl_var = tk.StringVar(value=existing.get("label", "Ma tâche"))
        ctk.CTkEntry(body, textvariable=lbl_var, width=320, height=32,
                     fg_color=BG3, border_color=BG4, border_width=1,
                     text_color=FG, corner_radius=8,
                     font=regular_font
                     ).grid(row=1, column=0, columnspan=4, sticky="we", pady=(0, 12))

        # ── Type de périodicité ──
        ctk.CTkLabel(body, text="Périodicité", text_color=FG_DIM, anchor="w",
                     font=small_font
                     ).grid(row=2, column=0, sticky="w", pady=(0, 2))
        sched_var = tk.StringVar(value=existing.get("sched_type", "interval"))

        rb_frame = ctk.CTkFrame(body, fg_color="transparent")
        rb_frame.grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ctk.CTkRadioButton(rb_frame, text="Intervalle", variable=sched_var,
                            value="interval", text_color=FG,
                            fg_color=ACCENT, hover_color=ACCENT_HOVER,
                            border_color=BG4, font=regular_font,
                            command=lambda: _toggle()).pack(side="left")
        ctk.CTkRadioButton(rb_frame, text="Heure fixe", variable=sched_var,
                            value="fixed", text_color=FG,
                            fg_color=ACCENT, hover_color=ACCENT_HOVER,
                            border_color=BG4, font=regular_font,
                            command=lambda: _toggle()).pack(side="left", padx=(20, 0))

        # ── Sous-panneau Intervalle ──
        iv_frame = ctk.CTkFrame(body, fg_color="transparent")
        iv_frame.grid(row=4, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ctk.CTkLabel(iv_frame, text="Toutes les", text_color=FG,
                     font=regular_font).pack(side="left")
        iv_val = tk.StringVar(value=str(existing.get("interval_value", "30")))
        ctk.CTkEntry(iv_frame, textvariable=iv_val, width=70, height=30,
                     fg_color=BG3, border_color=BG4, border_width=1,
                     text_color=FG, corner_radius=8,
                     font=regular_font).pack(side="left", padx=8)
        iv_unit = tk.StringVar(value=existing.get("interval_unit", "minutes"))
        ctk.CTkOptionMenu(iv_frame, variable=iv_unit, width=110, height=30,
                          values=["secondes", "minutes", "heures"],
                          fg_color=BG3, button_color=BG4, button_hover_color=ACCENT,
                          text_color=FG, dropdown_fg_color=BG3,
                          dropdown_text_color=FG, dropdown_hover_color=BG4,
                          corner_radius=8, font=regular_font).pack(side="left")

        # ── Sous-panneau Heure fixe ──
        fx_frame = ctk.CTkFrame(body, fg_color="transparent")
        fx_frame.grid(row=4, column=0, columnspan=4, sticky="w", pady=(0, 8))
        fx_frame.grid_remove()
        ctk.CTkLabel(fx_frame, text="Heure (HH:MM)", text_color=FG,
                     font=regular_font).pack(side="left")
        fx_time = tk.StringVar(value=existing.get("at_time", "09:00"))
        ctk.CTkEntry(fx_frame, textvariable=fx_time, width=80, height=30,
                     fg_color=BG3, border_color=BG4, border_width=1,
                     text_color=FG, corner_radius=8,
                     font=regular_font).pack(side="left", padx=10)

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
        ctk.CTkLabel(body, text="Action (Python)", text_color=FG_DIM, anchor="w",
                     font=small_font
                     ).grid(row=5, column=0, sticky="nw", pady=(4, 2))
        code_card = ctk.CTkFrame(body, fg_color=BG3, corner_radius=8)
        code_card.grid(row=6, column=0, columnspan=4, sticky="we", pady=(0, 12))
        code_box = tk.Text(code_card, height=6, width=44,
                           bg=BG3, fg=FG,
                           insertbackground=FG, relief="flat",
                           font=("Consolas", 10), wrap="none",
                           padx=10, pady=8, highlightthickness=0,
                           selectbackground=BG4, selectforeground=ACCENT)
        code_box.pack(fill="both", expand=True, padx=2, pady=2)
        default_code = existing.get("action_code",
                                    'logger.log("Ma tâche s\'exécute ✓")')
        code_box.insert("1.0", default_code)

        # ── Boutons ──
        btn_frame = ctk.CTkFrame(body, fg_color="transparent")
        btn_frame.grid(row=7, column=0, columnspan=4, sticky="e", pady=(4, 0))

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

        ctk.CTkButton(btn_frame, text="Annuler", command=dlg.destroy,
                       fg_color=BG3, hover_color=BG4, text_color=FG_DIM,
                       corner_radius=8, width=100, height=32,
                       font=regular_font).pack(side="left", padx=(0, 8))
        btn_label = "Enregistrer" if editing else "Créer"
        ctk.CTkButton(btn_frame, text=btn_label, command=_ok,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER,
                       text_color="#1A1A1A",
                       corner_radius=8, width=120, height=32,
                       font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
                       ).pack(side="left")

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
        self._task_tree.tag_configure("custom", foreground=ACCENT)
        self.root.after(5000, self._refresh_tasks)

    def _refresh_hotkeys(self):
        self._hk_tree.delete(*self._hk_tree.get_children())
        for entry in hotkey_manager.registered:
            self._hk_tree.insert("", "end", values=(entry["combo"], entry["label"]))

    def _tick_clock(self):
        if self.root:
            self._clock_lbl.configure(
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
