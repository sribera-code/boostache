"""
notes_tab.py – Onglet Notes (éditeur de texte simple avec dictée + TTS).
"""

import json
import threading
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from engine import logger, tts
from storage import DATA_DIR, settings
from theme import (
    BG2, BG3, BG4, BG_CHAT, BORDER, FG, FG_DIM, ACCENT, ACCENT_HOVER, RED,
)


NOTES_DIR = DATA_DIR / "notes"
NOTES_DIR.mkdir(parents=True, exist_ok=True)


class NoteTab:
    """Onglet note : éditeur de texte plein, TTS et dictée Windows+H."""

    _counter = 0

    def __init__(self, inner_notebook: ttk.Notebook, dashboard,
                 slot: int | None = None):
        NoteTab._counter += 1
        self.num  = NoteTab._counter
        self.slot = slot if slot is not None else self.num
        self.dashboard = dashboard
        self._label: str = ""
        self._tts_active = False
        self._suspend_autosave = False
        self._save_scheduled  = False

        self.frame = ttk.Frame(inner_notebook)
        inner_notebook.add(self.frame, text="  ·  ")
        is_new = not self._slot_path().exists()
        self._build()
        self._restore()
        if is_new:
            self._persist()

    @property
    def root(self):
        return self.dashboard.root

    # ─────────────────────────────────────────
    #  Construction
    # ─────────────────────────────────────────
    def _build(self):
        # ── Éditeur ───────────────────────────
        ed_frame = tk.Frame(self.frame, bg=BG_CHAT)
        ed_frame.pack(fill="both", expand=True)

        self._editor = tk.Text(
            ed_frame, bg=BG_CHAT, fg=FG, font=("Segoe UI", 10),
            relief="flat", borderwidth=0, wrap="word",
            padx=12, pady=10, insertbackground=FG,
            selectbackground=BG4, selectforeground=ACCENT,
            highlightthickness=0, undo=True)

        vsb = ttk.Scrollbar(ed_frame, orient="vertical",
                            command=self._editor.yview)
        self._editor.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._editor.pack(fill="both", expand=True)

        self._editor.bind("<Button-3>", self._editor_context_menu)
        self._editor.bind("<KeyRelease>", self._on_modified)
        self._editor.bind("<<Paste>>",   self._on_modified)
        self._editor.bind("<<Cut>>",     self._on_modified)

        tk.Frame(self.frame, bg=BORDER, height=1).pack(fill="x")

        # ── Barre du bas (dictée + TTS) ───────
        bottom = ctk.CTkFrame(self.frame, fg_color=BG2, corner_radius=0,
                              height=48)
        bottom.pack(fill="x")
        bottom.pack_propagate(False)

        icon_font = ctk.CTkFont(family="Segoe UI Emoji", size=15)

        def _icon_btn(text, cmd):
            return ctk.CTkButton(
                bottom, text=text, command=cmd,
                width=36, height=36, corner_radius=8,
                fg_color="transparent", hover_color=BG3,
                text_color=FG_DIM, font=icon_font)

        _icon_btn("🎤", self._dictate).pack(side="left", padx=(10, 2), pady=6)

        self._tts_btn = _icon_btn("🔊", self._tts_toggle)
        self._tts_btn.pack(side="left", padx=2, pady=6)
        self._tts_btn.bind("<Button-3>", self._tts_btn_menu)

    # ─────────────────────────────────────────
    #  Menu contextuel éditeur
    # ─────────────────────────────────────────
    def _editor_context_menu(self, event):
        has_sel = self._has_selection()
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        menu.add_command(label="Copier la sélection",
                         command=lambda: self._copy_selection(None),
                         state=("normal" if has_sel else "disabled"))
        menu.add_command(label="Copier tout", command=self._copy_all)
        menu.add_command(label="Coller", command=self._paste)
        menu.add_separator()
        menu.add_command(label="Lire la sélection",
                         command=self._tts_speak_selection,
                         state=("normal" if has_sel else "disabled"))
        menu.add_command(label="Tout lire",
                         command=lambda: self._tts_speak_mode("all"))
        menu.add_separator()
        menu.add_command(label="Tout effacer", command=self._clear)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_selection(self, event):
        try:
            text = self._editor.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass
        return "break"

    def _copy_all(self):
        text = self._editor.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _paste(self):
        try:
            txt = self.root.clipboard_get()
        except tk.TclError:
            return
        try:
            self._editor.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass
        self._editor.insert("insert", txt)
        self._schedule_save()

    def _clear(self):
        self._editor.delete("1.0", "end")
        self._schedule_save()

    def _has_selection(self) -> bool:
        try:
            self._editor.index(tk.SEL_FIRST)
            return True
        except tk.TclError:
            return False

    def _get_selection_text(self) -> str:
        try:
            return self._editor.get(tk.SEL_FIRST, tk.SEL_LAST).strip()
        except tk.TclError:
            return ""

    # ─────────────────────────────────────────
    #  TTS
    # ─────────────────────────────────────────
    def _tts_toggle(self):
        if self._tts_active:
            tts.stop()
            self._tts_reset()
        else:
            mode = settings.get("tts_mode_note", "all")
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
        self._tts_btn.configure(text="⏹", text_color=RED)
        tts.speak(text, on_done=lambda: self.root.after(0, self._tts_reset))

    def _tts_reset(self):
        self._tts_active = False
        try:
            self._tts_btn.configure(text="🔊", text_color=FG_DIM)
        except Exception:
            pass

    def _get_tts_text(self, mode: str = "all") -> str:
        if mode == "sel":
            return self._get_selection_text()
        return self._editor.get("1.0", "end").strip()

    def _tts_btn_menu(self, event):
        current = settings.get("tts_mode_note", "all")
        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG3, fg=FG, activebackground=BG4,
                       activeforeground=ACCENT, relief="flat", bd=0,
                       font=("Segoe UI", 9))
        menu.add_command(
            label=("✓  " if current == "all" else "    ") + "Tout lire",
            command=lambda: settings.set("tts_mode_note", "all"))
        menu.add_command(
            label=("✓  " if current == "sel" else "    ") + "Lire la sélection",
            command=lambda: settings.set("tts_mode_note", "sel"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ─────────────────────────────────────────
    #  Dictée (Windows+H)
    # ─────────────────────────────────────────
    def _dictate(self):
        try:
            self._editor.focus_set()
        except Exception:
            pass

        def send_hotkey():
            try:
                import keyboard
                keyboard.send("windows+h")
            except Exception:
                pass
        threading.Thread(target=send_hotkey, daemon=True).start()

    # ─────────────────────────────────────────
    #  Auto-save + titre auto
    # ─────────────────────────────────────────
    def _on_modified(self, event):
        if self._suspend_autosave:
            return
        self._schedule_save()

    def _schedule_save(self):
        if self._save_scheduled or self._suspend_autosave:
            return
        self._save_scheduled = True
        if self.root:
            self.root.after(500, self._auto_save)

    def _auto_save(self):
        self._save_scheduled = False
        self._persist()
        if not self._label:
            self._update_auto_title()

    def _derive_title(self) -> str:
        text = self._editor.get("1.0", "end").strip()
        if not text:
            return ""
        first_line = text.split("\n")[0].strip()
        t = first_line[:18]
        return t + ("…" if len(first_line) > 18 else "")

    def _update_auto_title(self):
        title = self._derive_title()
        try:
            nb = self.dashboard._note_notebook
            display = f"  {title}  " if title else "  ·  "
            nb.tab(self.frame, text=display)
        except Exception:
            pass

    # ─────────────────────────────────────────
    #  Persistance
    # ─────────────────────────────────────────
    def _slot_path(self):
        return NOTES_DIR / f"note_{self.slot}.json"

    def _persist(self):
        try:
            data = {
                "slot":    self.slot,
                "label":   self._label,
                "content": self._editor.get("1.0", "end-1c"),
            }
            with open(self._slot_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.log(f"Note persist erreur slot {self.slot} : {e}")

    def _restore(self):
        p = self._slot_path()
        if not p.exists():
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self._label = data.get("label", "")
        content    = data.get("content", "")
        if content:
            self._suspend_autosave = True
            self._editor.insert("1.0", content)
            self._suspend_autosave = False
        if self._label:
            try:
                self.dashboard._note_notebook.tab(
                    self.frame, text=f"  {self._label}  ")
            except Exception:
                pass
        elif content:
            self._update_auto_title()
