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
import assistant
import diagnostics
import locks
import reminders
from clipboard_listener import get_clipboard_text, set_clipboard_content, set_clipboard_text
from compare import UserError as CompareError
from notes import UserError as NoteError
import speech
from engine import LEVELS, logger, tts, strip_markdown
from ollama_admin import DOWNLOAD_PAGE, INSTALLER, USES as MODEL_USES, models_dir
from storage import DATA_DIR, clipboard_history, settings
from winutil import (exe_icon, list_windows, open_url, recycle, reveal_in_explorer, set_opacity,
                     set_topmost, window_thumbnail)

# Filtres des fenêtres de fichiers : pywebview n'accepte dans le libellé que lettres, chiffres
# et espaces (ni virgule, ni « & », ni apostrophe), sinon la fenêtre ne s'ouvre pas
ATTACH_TYPES = (
    "Texte et code (*.txt;*.md;*.py;*.js;*.ts;*.json;*.yaml;*.yml;*.csv;*.xml;*.html;*.css;"
    "*.c;*.cpp;*.h;*.cs;*.java;*.rs;*.go;*.ps1;*.bat;*.sql;*.log;*.ini;*.toml)",
    "Images (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)",
    "Tous les fichiers (*.*)",
)
IMAGE_TYPES = ("Images (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)",)
COMPARE_TYPES = (
    "Textes et documents (*.txt;*.md;*.csv;*.json;*.xml;*.html;*.yaml;*.yml;*.ini;*.toml;*.log;*.py;*.js;*.ts;"
    "*.css;*.c;*.cpp;*.h;*.cs;*.java;*.ps1;*.bat;*.sql;*.srt;*.pdf;*.docx;*.odt;*.rtf;*.pptx;*.xlsx)",
    "Tous les fichiers (*.*)",
)
SAVE_IMAGE_TYPES = ("Image PNG (*.png)", "Image JPEG (*.jpg;*.jpeg)", "Image WebP (*.webp)",
                    "Bitmap (*.bmp)")
NOTE_SAVE_TYPES = ("Texte (*.txt)", "Markdown (*.md)", "Tous les fichiers (*.*)")
NOTE_OPEN_TYPES = (
    "Textes (*.txt;*.md;*.markdown;*.log;*.csv;*.json;*.xml;*.html;*.ini;*.yaml;*.yml;*.toml;*.srt)",
    "Documents copiés dans une note (*.pdf;*.docx;*.odt;*.rtf;*.pptx;*.xlsx;*.eml;*.epub)",
    "Tous les fichiers (*.*)",
)


class Api:
    def __init__(self, app):
        self._app = app

    # ─────────────────────────────────────────
    #  Général
    # ─────────────────────────────────────────
    def ui_ready(self):
        return self._app.ui_ready()

    def ui_started(self):
        self._app.ui_started()

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

    def speak(self, text, source=None, markdown=False, stream=False):
        """stream : texte encore en cours d'écriture, la suite arrive par tts_feed."""
        # Message arrivé relu (une notification) : les autres applications baissent, comme à son annonce
        duck = assistant.duck_level() if str(source or "").startswith(assistant.FEED_READ) else None
        tts.speak(text or "", source=source, stream=bool(stream), prepare=strip_markdown if markdown else None,
                  duck=duck)

    def tts_feed(self, source, text, final=False):
        return tts.feed(source, text or "", bool(final))

    def tts_stop(self):
        tts.stop()

    def tts_pause(self, paused=None):
        tts.pause(None if paused is None else bool(paused))

    def tts_skip(self, delta):
        tts.skip(int(delta))

    def tts_volume(self, volume):
        """Volume pendant le glissement du curseur (enregistré à la fin, par set_setting)."""
        tts.set_volume(volume)

    def tts_voices(self):
        return speech.list_voices()

    def tts_preview(self, engine, voice=""):
        """Fait entendre une voix sans la choisir."""
        if engine not in speech.ENGINES:
            return
        label = speech.voice_label(engine, voice or tts.voice_for(engine))
        tts.speak(f"Bonjour, voici la voix {label}. Elle lit vos réponses, vos notes et vos textes à voix haute.",
                  source="tts-preview", engine=engine, voice=voice or "")

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
        self._app.chat.check(force=True)

    def chat_check(self):
        """Onglet ouvert, envoi sans modèle : Ollama est testé s'il n'a pas répondu récemment."""
        self._app.chat.check()

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
    #  Enregistreur (son qui sort du PC)
    # ─────────────────────────────────────────
    def rec_devices(self):
        return self._app.recorder.devices()

    def rec_start(self, source="output", device_id="", mic_id="", live=False):
        return self._app.recorder.start(str(source or ""), str(device_id or ""), str(mic_id or ""), bool(live))

    def rec_live_close(self):
        self._app.recorder.close_live()

    def rec_pause(self, paused):
        self._app.recorder.pause(bool(paused))

    def rec_stop(self):
        return self._app.recorder.stop()

    def rec_files(self):
        return self._app.recorder.files()

    def rec_rename(self, name, title):
        return self._app.recorder.rename(name, str(title or ""))

    def rec_delete(self, name):
        return self._app.recorder.delete(name)

    def rec_reveal(self, name=None):
        self._app.recorder.reveal(name)

    def rec_copy_file(self, name):
        return self._app.recorder.copy_file(name)

    def rec_transcribe(self, name):
        return self._app.recorder.transcribe(name)

    def rec_transcribe_cancel(self, name):
        return self._app.recorder.cancel_transcription(name)

    def rec_transcript(self, name):
        return self._app.recorder.transcript(name)

    def rec_open_transcript(self, name):
        return self._app.recorder.open_transcript(name)

    def rec_minutes(self, name):
        return self._app.minutes.get(str(name or ""))

    def rec_minutes_start(self, name):
        return self._app.minutes.start(str(name or ""))

    def rec_minutes_stop(self):
        self._app.minutes.stop()

    def rec_pick_folder(self):
        path = self.pick_folder(str(self._app.recorder.folder()))
        return self._app.recorder.set_folder(path) if path else None

    # ─────────────────────────────────────────
    #  Assistant (questions sur Gmail et WhatsApp, annonce des nouveaux messages)
    # ─────────────────────────────────────────
    def assistant_ask(self, question):
        return self._app.assistant.ask(str(question or ""))

    def assistant_stop(self):
        self._app.assistant.stop()

    def assistant_delete(self, item_id):
        self._app.assistant.delete(str(item_id))

    def assistant_clear(self):
        self._app.assistant.clear()

    def assistant_clear_feed(self):
        self._app.assistant.clear_feed()

    def assistant_example(self):
        self._app.assistant.example()

    # ─────────────────────────────────────────
    #  Documents (index des dossiers choisis, questions et résumés)
    # ─────────────────────────────────────────
    def rag_state(self):
        return self._app.rag.state()

    def rag_children(self, path):
        return self._app.rag.children(str(path or ""))

    def rag_add_folder(self):
        path = self.pick_folder()
        return self._app.rag.add_folder(path) if path else None

    def rag_add_paths(self, paths):
        return self._app.rag.add_paths([str(p) for p in paths or []])

    def rag_remove_folder(self, path):
        return self._app.rag.remove_folder(str(path or ""))

    def rag_exclude(self, path, excluded=True):
        return self._app.rag.set_excluded(str(path or ""), bool(excluded))

    def rag_reindex(self, path=None):
        return self._app.rag.reindex(str(path) if path else None)

    def rag_rescan(self):
        self._app.rag.rescan()

    def rag_pause(self, paused):
        return self._app.rag.set_paused(bool(paused))

    def rag_set_embed_model(self, model):
        return self._app.rag.set_embed_model(str(model or ""))

    def rag_pull(self, name):
        """Télécharge un modèle d'embeddings (progression : événements ollama:pull)."""
        if not self._app.ollama:
            return {"ok": False, "error": "La librairie « ollama » est absente."}
        return self._app.ollama.pull(str(name or ""))

    def rag_ask(self, question, scope=None):
        return self._app.rag.ask(str(question or ""), str(scope) if scope else None)

    def rag_task(self, kind, path):
        return self._app.rag.task(str(kind or ""), str(path or ""))

    def rag_stop(self):
        self._app.rag.stop()

    def rag_delete(self, item_id):
        self._app.rag.delete(str(item_id))

    def rag_clear(self):
        self._app.rag.clear()

    def rag_search(self, query, scope=None):
        return self._app.rag.search(str(query or ""), str(scope) if scope else None)

    def rag_file_info(self, path):
        return self._app.rag.file_info(str(path or ""))

    def rag_file_chunks(self, path):
        return self._app.rag.file_chunks(str(path or ""))

    def rag_open(self, path):
        return self._app.rag.open_file(str(path or ""))

    def rag_reveal(self, path):
        return self._app.rag.reveal(str(path or ""))

    # ─────────────────────────────────────────
    #  Ollama (serveur, modèles, téléchargements)
    # ─────────────────────────────────────────
    def ollama_state(self):
        return self._app.ollama.state()

    def ollama_running(self):
        return self._app.ollama.running()

    def ollama_start(self):
        return self._app.ollama.start()

    def ollama_install_page(self):
        return open_url(DOWNLOAD_PAGE)

    def ollama_banner(self):
        return self._app.ollama.banner() if self._app.ollama else {"kind": ""}

    def ollama_installer(self):
        """Télécharge l'installateur d'Ollama (navigateur) : le relancer répare une installation incomplète."""
        return open_url(INSTALLER)

    def ollama_open_models_dir(self):
        folder = models_dir()
        if folder.is_dir():
            os.startfile(str(folder))
            return True
        return False

    def ollama_pull(self, name):
        return self._app.ollama.pull(str(name or ""))

    def ollama_cancel(self, name):
        self._app.ollama.cancel(str(name or ""))

    def ollama_dismiss(self, name):
        self._app.ollama.dismiss(str(name or ""))

    def ollama_delete(self, name):
        return self._app.ollama.delete(str(name or ""))

    def ollama_unload(self, name):
        return self._app.ollama.unload(str(name or ""))

    def ollama_check_updates(self):
        return self._app.ollama.check_updates()

    def ollama_library(self, force=False):
        return self._app.ollama.library(bool(force))

    def ollama_variants(self, name):
        return self._app.ollama.variants(str(name or ""))

    def ollama_sizes(self, names):
        return self._app.ollama.sizes(list(names or []))

    def ollama_use(self, use, model):
        """Choisit le modèle d'une fonction de Boostache (Conversations, aide…)."""
        key = MODEL_USES.get(use)
        if not key or not model:
            return None
        self._app.set_setting(key, str(model))
        return self._app.ollama.uses()

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

    def note_save(self, slot, content, to_file=False):
        return self._app.notes.save(int(slot), content or "", bool(to_file))

    def _notes(self, fn):
        try:
            return fn(self._app.notes)
        except NoteError as e:
            return {"error": str(e)}

    def note_save_as(self, slot, content):
        """Fenêtre Enregistrer, puis la note est liée au fichier choisi (None : annulé)."""
        notes = self._app.notes
        notes.save(int(slot), content or "")
        folder, name = notes.save_suggestion(int(slot))
        result = self._app.window.create_file_dialog(webview.FileDialog.SAVE, directory=folder,
                                                     save_filename=name, file_types=NOTE_SAVE_TYPES)
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        return self._notes(lambda n: n.save_as(int(slot), path))

    def note_open_dialog(self, reuse=None, after=None):
        folder = settings.get("notes_dir") or ""
        paths = self._app.window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=NOTE_OPEN_TYPES,
            directory=folder if os.path.isdir(folder) else "")
        return self.note_open_paths(list(paths), reuse, after) if paths else None

    def note_open_paths(self, paths, reuse=None, after=None):
        return self._app.notes.open_paths([str(p) for p in paths or []],
                                          None if reuse is None else int(reuse),
                                          None if after is None else int(after))

    def note_check(self, dirty=None):
        return self._app.notes.check(dirty or [])

    def note_file_reload(self, slot):
        return self._notes(lambda n: n.reload(int(slot)))

    def note_file_write(self, slot):
        return self._notes(lambda n: n.write(int(slot)))

    def note_file_auto(self, slot, auto):
        return self._app.notes.set_auto(int(slot), bool(auto))

    def note_unlink(self, slot):
        return self._app.notes.unlink(int(slot))

    # ─────────────────────────────────────────
    #  Sites intégrés (WhatsApp, Gmail) : pane = clé du site
    # ─────────────────────────────────────────
    def pane_show(self, pane, x, y, width, height, focus=False, holes=None):
        self._app.panes[pane].show(x, y, width, height, focus, holes)

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

    # Résumé des e-mails (Gmail) : scope = current | selection | unread | all ;
    # ids : e-mails choisis dans la liste à côté du site (sinon la liste affichée dans Gmail)
    def pane_digest(self, pane, model, hint="", scope="all", ids=None):
        ids = None if ids is None else [str(i) for i in ids][:100]
        return self._app.panes[pane].digest(str(model or ""), str(hint or ""), str(scope or "all"), ids)

    def pane_digest_stop(self, pane):
        self._app.panes[pane].digest_stop()

    # Question sur les e-mails (Gmail) : réponse par l'événement gmail:qa
    def pane_ask(self, pane, model, question):
        return self._app.panes[pane].ask(str(model or ""), str(question or ""))

    def pane_ask_stop(self, pane):
        self._app.panes[pane].ask_stop()

    def pane_ask_reset(self, pane):
        self._app.panes[pane].ask_reset()

    def pane_open_thread(self, pane, thread_id, base=""):
        return self._app.panes[pane].open_thread(str(thread_id or ""), str(base or ""))

    # Liste des e-mails à côté du site (Gmail) : résumés en arrière-plan
    def pane_list_summarize(self, pane, model, hint="", ids=None):
        return self._app.panes[pane].summarize_list(str(model or ""), str(hint or ""), [str(i) for i in ids or []][:100])

    def pane_list_stop(self, pane):
        self._app.panes[pane].summarize_stop()

    def pane_list_action(self, pane, thread_id, action):
        return self._app.panes[pane].list_action(str(thread_id or ""), str(action or ""))

    # ─────────────────────────────────────────
    #  Fichiers
    # ─────────────────────────────────────────
    def files_inspect(self, path):
        return self._app.files.inspect(str(path or ""))

    def files_details(self, path):
        return self._app.files.details(str(path or ""))

    def files_text(self, path):
        return self._app.files.text(str(path or ""))

    def files_scan(self, path):
        return self._app.files.scan(str(path or ""))

    def files_stop_scan(self, path):
        self._app.files.stop_scan(str(path or ""))

    def files_hashes(self, path):
        return self._app.files.hashes(str(path or ""))

    def files_remember(self, paths):
        self._app.files.remember([str(p) for p in (paths or [])])

    def files_forget(self, path):
        self._app.files.forget(str(path or ""))

    def files_refresh(self, path):
        self._app.files.refresh(str(path or ""))

    def files_thread(self, path):
        return self._app.files.thread(str(path or ""))

    def files_summarize(self, path):
        return self._app.files.summarize(str(path or ""))

    def files_ask(self, path, question):
        return self._app.files.ask(str(path or ""), question)

    def files_stop(self):
        self._app.files.stop()

    def files_delete(self, path, entry_id):
        self._app.files.delete(str(path or ""), str(entry_id or ""))

    def files_model(self):
        return self._app.files.model()

    def files_open(self, path):
        """Ouvre l'élément avec son application (un dossier dans l'Explorateur)."""
        path = str(path or "")
        if not os.path.exists(path):
            return False
        try:
            os.startfile(path)
            return True
        except OSError as e:
            logger.log(f"Fichiers : ouverture de {path} impossible ({e}).", level="warning")
            return False

    def locks_scan(self, paths, elevated=False):
        """Processus qui utilisent ces fichiers ou dossiers ; elevated : en
        administrateur (fenêtre UAC). {cancelled} si l'UAC est refusée."""
        paths = [str(p) for p in (paths or []) if p]
        try:
            result = locks.scan(paths, elevated=bool(elevated))
        except PermissionError:
            return {"cancelled": True}
        except Exception as e:
            logger.log(f"Fichiers bloqués : analyse impossible ({e}).", level="warning")
            return {"error": str(e)}
        for p in result["processes"]:
            p["icon"] = exe_icon(p["exe"]) if p["exe"] else None
        return result

    def locks_kill(self, targets, elevated=False):
        """targets : [{pid, start, name}] (start : vérifie que le pid n'a pas été repris)."""
        targets = [{"pid": int(t["pid"]), "start": str(t.get("start") or 0), "name": str(t.get("name") or "")}
                   for t in (targets or [])]
        try:
            result = locks.terminate(targets, elevated=bool(elevated))
        except PermissionError:
            return {"cancelled": True}
        except Exception as e:
            logger.log(f"Fichiers bloqués : fin de processus impossible ({e}).", level="warning")
            return {"error": str(e)}
        names = {t["pid"]: t["name"] for t in targets}
        for pid in result["killed"]:
            logger.log(f"Fichiers bloqués : {names.get(pid) or 'processus'} (PID {pid}) terminé.")
        return result

    def locks_close(self, pid):
        """Demande à l'application de fermer ses fenêtres : nombre de fenêtres prévenues."""
        return locks.close_windows(int(pid))

    def locks_recycle(self, path):
        path = str(path or "")
        if not os.path.exists(path):
            return {"ok": True}
        if recycle(path, warn_permanent=True, owner=self._app._hwnd) and not os.path.exists(path):
            logger.log(f"Fichiers bloqués : {path} mis à la corbeille.")
            return {"ok": True}
        return {"ok": False}

    def locks_pick_file(self):
        paths = self._app.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return list(paths) if paths else []

    # ─────────────────────────────────────────
    #  Calculatrice
    # ─────────────────────────────────────────
    def calc_state(self):
        return self._app.calc.state()

    def calc_warm_up(self):
        self._app.calc.warm_up()

    def calc_run(self, code):
        return self._app.calc.run(code)

    def calc_interrupt(self):
        return self._app.calc.interrupt()

    def calc_reset(self):
        return self._app.calc.reset()

    def calc_replay(self, session):
        return self._app.calc.replay(str(session or ""))

    def calc_delete(self, n):
        return self._app.calc.delete(int(n))

    def calc_clear(self):
        return self._app.calc.clear()

    def calc_edit(self, n, code):
        """Modifie un calcul de l'historique : lui et ceux qui le suivent sont recalculés."""
        return self._app.calc.recompute(int(n), code)

    def calc_ask(self, question):
        return self._app.calc.ask(question)

    def calc_stop(self):
        self._app.calc.stop()

    def calc_delete_turn(self, turn_id):
        self._app.calc.delete_turn(str(turn_id or ""))

    def calc_clear_conversation(self):
        self._app.calc.clear_conversation()

    def calc_model(self):
        return self._app.calc.model()

    # ─────────────────────────────────────────
    #  Comparer (deux textes : différences, fusion, IA)
    #  side : "left" | "right" ; les erreurs reviennent en {error}
    # ─────────────────────────────────────────
    def _compare(self, fn):
        try:
            return fn(self._app.compare)
        except CompareError as e:
            return {"error": str(e)}

    def compare_state(self, check=False):
        return self._compare(lambda c: c.state(bool(check)))

    def compare_load(self, side, path):
        return self._compare(lambda c: c.load(str(side or ""), str(path or "")))

    def compare_pick(self, side):
        sides = self._app.compare.sides
        near = next((s["path"] for s in (sides.get(side), *sides.values()) if s and s.get("path")), "")
        folder = os.path.dirname(near)
        paths = self._app.window.create_file_dialog(
            webview.FileDialog.OPEN, directory=folder if os.path.isdir(folder) else "", file_types=COMPARE_TYPES)
        return self.compare_load(side, paths[0]) if paths else None

    def compare_text(self, side, text, name=""):
        return self._compare(lambda c: c.set_text(str(side or ""), str(text or ""), str(name or "")))

    def compare_paste(self, side):
        text = get_clipboard_text()
        if not text.strip():
            return {"error": "Le presse-papiers ne contient pas de texte."}
        return self.compare_text(side, text, "Texte collé")

    def compare_clear(self, side):
        return self._compare(lambda c: c.clear(str(side or "")))

    def compare_swap(self):
        return self._compare(lambda c: c.swap())

    def compare_reload(self):
        return self._compare(lambda c: c.reload())

    def compare_options(self, options):
        return self._compare(lambda c: c.set_options(options if isinstance(options, dict) else {}))

    def compare_hint(self, hint):
        self._app.compare.set_hint(str(hint or ""))

    def compare_choose(self, index, choice, text=""):
        return self._compare(lambda c: c.choose(int(index), str(choice) if choice else None, str(text or "")))

    def compare_choose_all(self, choice, only_open=False):
        return self._compare(lambda c: c.choose_all(str(choice) if choice else None, bool(only_open)))

    def compare_result(self, default="left"):
        return self._compare(lambda c: c.result(str(default or "left")))

    def compare_save(self, target, text, utf8=False):
        """target : "left" | "right" (fichier remplacé, l'ancien à la corbeille) ou "as" (fenêtre Enregistrer)."""
        path = ""
        if target == "as":
            folder, name = self._app.compare.save_suggestion()
            result = self._app.window.create_file_dialog(webview.FileDialog.SAVE, directory=folder,
                                                         save_filename=name)
            if not result:
                return None
            path = result if isinstance(result, str) else result[0]
        return self._compare(lambda c: c.save(str(target or ""), str(text or ""), path, bool(utf8),
                                              owner=self._app._hwnd))

    def compare_open(self, side):
        """Ouvre le fichier d'un côté avec son application."""
        s = self._app.compare.sides.get(str(side or "")) or {}
        if s.get("path") and os.path.isfile(s["path"]):
            os.startfile(s["path"])
            return True
        return False

    def compare_explain(self):
        return self._app.compare.explain()

    def compare_ask(self, question):
        return self._app.compare.ask(question)

    def compare_stop(self):
        self._app.compare.stop()

    def compare_delete(self, entry_id):
        self._app.compare.delete(str(entry_id or ""))

    def compare_resolve(self, indices=None):
        """L'IA choisit la fusion des différences données (aucune : celles sans choix)."""
        return self._app.compare.resolve([int(i) for i in indices] if indices is not None else None)

    def compare_resolve_stop(self):
        self._app.compare.resolve_stop()

    def compare_model(self):
        return self._app.compare.model()

    # ─────────────────────────────────────────
    #  Recherche dans toutes les sections (Ctrl+K)
    # ─────────────────────────────────────────
    def search_all(self, query):
        return self._app.search.search(str(query or ""))

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
    #  Journal
    # ─────────────────────────────────────────
    def logs_clear(self):
        logger.clear_history()

    def logs_client(self, level, message, details=""):
        """Erreur ou avertissement de l'interface (errors.js) : rejoint le journal."""
        logger.log(str(message or "")[:2000] or "(sans message)", level=level if level in LEVELS else "error",
                   details=str(details or "")[:12000] or None, source="interface")

    def logs_report(self, client=None):
        """Rapport de diagnostic (texte) ; client : navigateur et taille de la fenêtre."""
        return self._app.diagnostic_report(client if isinstance(client, dict) else {})

    def logs_sessions(self):
        return diagnostics.sessions()

    def logs_open(self, name=None):
        return diagnostics.open_log(str(name) if name else None)

    def logs_open_folder(self):
        return diagnostics.open_folder()

    # ─────────────────────────────────────────
    #  Rappels
    # ─────────────────────────────────────────
    def reminders_list(self):
        return self._app.reminders.snapshot()

    def reminder_suggestions(self, text=""):
        return reminders.suggestions(str(text or ""))

    def reminder_add(self, text, at, details="", source=None):
        return self._app.reminders.add(str(text or ""), at, str(details or ""), source)

    def reminder_delete(self, reminder_id):
        return self._app.reminders.delete(str(reminder_id or ""))

    def reminder_snooze(self, reminder_id, at):
        return self._app.reminders.snooze(str(reminder_id or ""), at)

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
