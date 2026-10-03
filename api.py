"""
api.py – Méthodes Python appelables depuis l'interface web
(window.pywebview.api.<méthode>(…) côté JavaScript).

pywebview exécute chaque appel dans son propre thread. Seules les méthodes
publiques sont exposées : tout le reste est préfixé par « _ ».
"""

import os
from pathlib import Path

import webview

import custom_tasks
from clipboard_listener import get_clipboard_text, set_clipboard_content, set_clipboard_text
from engine import logger, tts, strip_markdown
from storage import DATA_DIR, clipboard_history, settings
from winutil import (list_windows, open_url, reveal_in_explorer, set_opacity, set_topmost,
                     window_thumbnail)

ATTACH_TYPES = (
    "Texte & code (*.txt;*.md;*.py;*.js;*.ts;*.json;*.yaml;*.yml;*.csv;*.xml;*.html;*.css;"
    "*.c;*.cpp;*.h;*.cs;*.java;*.rs;*.go;*.ps1;*.bat;*.sql;*.log;*.ini;*.toml)",
    "Images (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)",
    "Tous les fichiers (*.*)",
)
IMAGE_TYPES = ("Images (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)",)
SAVE_IMAGE_TYPES = ("Image PNG (*.png)", "Image JPEG (*.jpg;*.jpeg)", "Image WebP (*.webp)",
                    "Bitmap (*.bmp)")


class Api:
    def __init__(self, app):
        self._app = app

    # ─────────────────────────────────────────
    #  Général
    # ─────────────────────────────────────────
    def ui_ready(self):
        return self._app.ui_ready()

    def copy_text(self, text):
        return set_clipboard_text(str(text or ""))

    def copy_rich(self, text, html):
        """Texte brut + HTML mis en forme (collé tel quel dans Word, Outlook…)."""
        return set_clipboard_content(text=str(text or ""), html=str(html or ""))

    def read_clipboard(self):
        return get_clipboard_text()

    def open_url(self, url):
        return open_url(url)

    def open_data_dir(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DATA_DIR))

    def reveal_path(self, path):
        if path and os.path.exists(path):
            reveal_in_explorer(path)

    def speak(self, text, source=None, markdown=False):
        text = strip_markdown(text) if markdown else (text or "")
        tts.speak(text, source=source)

    def tts_stop(self):
        tts.stop()

    def dictate(self):
        """Dictée vocale Windows (Win+H) dans le champ qui a le focus."""
        import keyboard
        keyboard.send("windows+h")

    def set_setting(self, key, value):
        return self._app.set_setting(key, value)

    def pick_folder(self, initial=None):
        result = self._app.window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=initial if initial and os.path.isdir(initial) else "")
        return result[0] if result else None

    def hide_window(self):
        self._app.hide()

    def focus_ui(self):
        self._app.focus_ui()

    # ─────────────────────────────────────────
    #  Fenêtres des autres applications
    # ─────────────────────────────────────────
    def windows_list(self):
        return list_windows(exclude={self._app._hwnd})

    def window_thumbnail(self, hwnd):
        return window_thumbnail(int(hwnd))

    def window_set_topmost(self, hwnd, on):
        """True si c'est fait, False si Windows refuse, None si la fenêtre est fermée."""
        return set_topmost(int(hwnd), bool(on))

    def window_set_opacity(self, hwnd, percent):
        """True si c'est fait, False si Windows refuse, None si la fenêtre est fermée."""
        return set_opacity(int(hwnd), int(percent))

    # ─────────────────────────────────────────
    #  Conversations
    # ─────────────────────────────────────────
    def chat_new(self, after_id=None):
        return self._app.chat.new_tab(after_id)

    def chat_close(self, tab_id):
        return self._app.chat.close_tab(tab_id)

    def chat_rename(self, tab_id, label):
        return self._app.chat.rename_tab(tab_id, label or "")

    def chat_set_model(self, tab_id, model):
        self._app.chat.set_model(tab_id, model)

    def chat_refresh_models(self):
        self._app.chat.refresh_models()

    def chat_send(self, tab_id, text, model):
        return self._app.chat.send(tab_id, text, model)

    def chat_edit(self, tab_id, index, text, model):
        return self._app.chat.edit_last(tab_id, int(index), text, model)

    def chat_fork(self, tab_id, index):
        return self._app.chat.fork(tab_id, int(index))

    def chat_stop(self, tab_id):
        self._app.chat.stop(tab_id)

    def chat_attach_dialog(self, tab_id):
        paths = self._app.window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=ATTACH_TYPES)
        if not paths:
            return {"errors": []}
        return self._app.chat.attach_paths(tab_id, list(paths))

    def chat_attach_paths(self, tab_id, paths):
        return self._app.chat.attach_paths(tab_id, list(paths or []))

    def chat_detach(self, tab_id, att_id):
        self._app.chat.detach(tab_id, att_id)

    def chat_paste(self, tab_id):
        return self._app.chat.paste(tab_id)

    def chat_screenshot(self, tab_id):
        self._app.chat.screenshot(tab_id)

    def chat_help_suggest(self, tab_id):
        self._app.chat.help_suggest(tab_id)

    def chat_copy_message(self, tab_id, index):
        return self._app.chat.copy_message(tab_id, int(index))

    def chat_image_url(self, tab_id, index, k):
        return self._app.chat.image_url(tab_id, int(index), int(k))

    def chat_attachment_url(self, tab_id, att_id):
        return self._app.chat.attachment_url(tab_id, att_id)

    def chat_copy_image(self, tab_id, index, k):
        return self._app.chat.copy_image(tab_id, int(index), int(k))

    def chat_image_to_captures(self, tab_id, index, k, name=""):
        image = self._app.chat.message_image(tab_id, int(index), int(k))
        if image is None:
            return False
        self._app.captures.add_image(image, label=os.path.splitext(name or "")[0])
        return True

    # ─────────────────────────────────────────
    #  Captures
    # ─────────────────────────────────────────
    def capture_snip(self):
        self._app.captures.snip()

    def capture_load(self, slot):
        return self._app.captures.load(int(slot))

    def capture_save(self, slot, image=None, orig=None):
        return self._app.captures.save(int(slot), image, orig)

    def capture_close(self, slot):
        return self._app.captures.close(int(slot))

    def capture_rename(self, slot, label):
        return self._app.captures.rename(int(slot), label or "")

    def capture_paste(self):
        return self._app.captures.paste()

    def capture_open_dialog(self):
        paths = self._app.window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=IMAGE_TYPES)
        if not paths:
            return {"count": 0, "errors": []}
        return self._app.captures.open_paths(list(paths))

    def capture_open_paths(self, paths):
        return self._app.captures.open_paths(list(paths or []))

    def capture_copy(self, image):
        return self._app.captures.copy(image)

    def capture_save_as(self, slot, image):
        directory = settings.get("capture_save_dir") or str(Path.home() / "Pictures")
        result = self._app.window.create_file_dialog(
            webview.FileDialog.SAVE, directory=directory,
            save_filename=self._app.captures.default_filename(int(slot)),
            file_types=SAVE_IMAGE_TYPES)
        if not result:
            return None
        return self._app.captures.export(result if isinstance(result, str) else result[0], image)

    def capture_temp_file(self, slot, image):
        return self._app.captures.temp_file(int(slot), image)

    # ─────────────────────────────────────────
    #  Consoles
    # ─────────────────────────────────────────
    def term_new(self, after_slot=None):
        return self._app.terminals.new(after_slot)

    def term_close(self, slot):
        return self._app.terminals.close(int(slot))

    def term_rename(self, slot, label):
        return self._app.terminals.rename(int(slot), label or "")

    def term_set_shell(self, slot, shell):
        return self._app.terminals.set_shell(int(slot), shell)

    def term_buffer(self, slot):
        return self._app.terminals.saved_buffer(int(slot))

    def term_start(self, slot, cols, rows):
        return self._app.terminals.start(int(slot), cols, rows)

    def term_stop(self, slot):
        self._app.terminals.stop(int(slot))

    def term_input(self, slot, data):
        self._app.terminals.write(int(slot), data)

    def term_submit(self, slot, line):
        self._app.terminals.submit(int(slot), line)

    def term_resize(self, slot, cols, rows):
        self._app.terminals.resize(int(slot), cols, rows)

    def term_title(self, slot, title):
        self._app.terminals.on_title(int(slot), title)

    def term_save(self, slot, buffer):
        self._app.terminals.save_buffer(int(slot), buffer)

    def term_pick_dir(self, slot):
        t = self._app.terminals._find(int(slot))
        path = self.pick_folder(t.cwd if t else None)
        return self._app.terminals.change_dir(int(slot), path) if path else False

    def term_cd(self, slot, path):
        return self._app.terminals.change_dir(int(slot), path)

    def term_open_external(self, slot):
        return self._app.terminals.open_external(int(slot))

    # ─────────────────────────────────────────
    #  Notes
    # ─────────────────────────────────────────
    def note_new(self, after_slot=None):
        return self._app.notes.new(after_slot)

    def note_close(self, slot):
        return self._app.notes.close(int(slot))

    def note_rename(self, slot, label):
        return self._app.notes.rename(int(slot), label or "")

    def note_save(self, slot, content):
        return self._app.notes.save(int(slot), content or "")

    # ─────────────────────────────────────────
    #  Sites intégrés (WhatsApp, Gmail) : pane = clé du site
    # ─────────────────────────────────────────
    def pane_show(self, pane, x, y, width, height, focus=False):
        self._app.panes[pane].show(x, y, width, height, focus)

    def pane_hide(self, pane):
        self._app.panes[pane].hide()

    def pane_reload(self, pane):
        self._app.panes[pane].reload()

    def pane_suggest(self, pane, model, hint=""):
        return self._app.panes[pane].suggest(str(model or ""), str(hint or ""))

    def pane_improve(self, pane, model, hint=""):
        return self._app.panes[pane].improve(str(model or ""), str(hint or ""))

    def pane_insert(self, pane, text):
        return self._app.panes[pane].insert(str(text or ""))

    # ─────────────────────────────────────────
    #  Presse-papiers
    # ─────────────────────────────────────────
    def clip_get(self, item_id):
        item = clipboard_history.get(int(item_id))
        return item["text"] if item else None

    def clip_copy(self, item_id):
        item = clipboard_history.get(int(item_id))
        return set_clipboard_text(item["text"]) if item else False

    def clip_delete(self, item_id):
        clipboard_history.delete(int(item_id))
        self._app.emit_clipboard()

    def clip_clear(self):
        clipboard_history.clear()
        self._app.emit_clipboard()

    # ─────────────────────────────────────────
    #  Historique
    # ─────────────────────────────────────────
    def logs_clear(self):
        logger.clear_history()

    # ─────────────────────────────────────────
    #  Tâches & raccourcis
    # ─────────────────────────────────────────
    def tasks_list(self):
        return custom_tasks.rows()

    def task_save(self, data, edit_idx=None, insert_after=None):
        try:
            custom_tasks.save(data or {}, edit_idx, insert_after)
            return {"ok": True, "tasks": custom_tasks.rows()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def task_delete(self, idx):
        try:
            custom_tasks.delete(int(idx))
            return {"ok": True, "tasks": custom_tasks.rows()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def task_run(self, idx):
        custom_tasks.run_now(int(idx))

    def hotkeys_list(self):
        return custom_tasks.hotkey_rows()

    def hotkey_run(self, idx):
        custom_tasks.run_hotkey(int(idx))
