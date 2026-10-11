"""
transcriber.py – Transcription des enregistrements en texte, en local, avec
Whisper (faster-whisper, sur le processeur).

Le modèle est téléchargé depuis Hugging Face à la première utilisation (cache
partagé de huggingface_hub), puis gardé en mémoire tant qu'il sert. Le son est
décodé ici (PyAV) plutôt que par faster-whisper, dont le décodeur ne suit pas
les versions récentes de PyAV.

Les transcriptions passent une par une, dans un thread dédié ; chacune peut être
annulée entre deux phrases.

Transcription en direct (Live) : le son arrive pendant l'enregistrement, il est
découpé aux silences (ou toutes les LIVE_MAX secondes) et chaque morceau est
transcrit dans son propre thread, avec la fin du texte déjà obtenu pour garder
le fil (vocabulaire, ponctuation). Le modèle est partagé avec les fichiers.
"""

import os
import queue
import ssl
import threading
import time
from dataclasses import dataclass, field

from engine import logger

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")   # cache sans liens (Windows)

try:
    import av
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    MISSING = ""
except ImportError as e:
    MISSING = f"librairie '{e.name}' absente (pip install faster-whisper)"

# Modèles proposés : clé faster-whisper → (libellé, taille du téléchargement)
MODELS = {
    "base":           ("Rapide",    "150 Mo"),
    "small":          ("Équilibré", "480 Mo"),
    "large-v3-turbo": ("Précis",    "1,6 Go"),
}
LANGUAGES = {"": "Détection automatique", "fr": "Français", "en": "Anglais", "es": "Espagnol",
             "de": "Allemand", "it": "Italien", "pt": "Portugais"}
RATE = 16000
PARAGRAPH_GAP = 1.5            # silence (s) entre deux phrases qui ouvre un paragraphe
PARAGRAPH_CHARS = 450          # au-delà, le paragraphe se termine à la fin de la phrase
PROGRESS_EVERY = 0.25
UNLOAD_AFTER = 300             # modèle libéré après 5 min sans transcription
LIVE_FRAME = 480               # 30 ms : unité de la détection des silences
LIVE_QUIET = 0.006             # niveau (RMS) sous lequel c'est du silence, au moins
LIVE_GAP = 0.6                 # silence (s) qui termine un morceau…
LIVE_MIN = 4.0                 # … s'il dure au moins ça (s) : un morceau court se transcrit mal
LIVE_MAX = 20.0                # au-delà, coupé même sans silence
LIVE_CONTEXT = 200             # fin du texte déjà transcrit, donnée au modèle


class Cancelled(Exception):
    pass


def short_error(error: Exception, limit: int = 160) -> str:
    """Première ligne de l'erreur, raccourcie (affichée à l'utilisateur)."""
    text = str(error).strip()
    text = text.splitlines()[0].strip() if text else error.__class__.__name__
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


@dataclass
class Job:
    path: str
    model: str
    language: str
    on_progress: object                         # (job) → None
    on_done: object                             # (job, text | None, error) → None
    state: str = "queued"                       # queued | download | load | run
    progress: float = 0.0
    cancelled: threading.Event = field(default_factory=threading.Event)


def _windows_certificates():
    """Téléchargements Hugging Face vérifiés avec les certificats de Windows :
    sur certains postes (antivirus ou proxy qui inspecte le HTTPS), ceux de
    Python sont refusés."""
    try:
        import truststore
        from huggingface_hub import set_client_factory
        from huggingface_hub.utils import _http as hf_http
    except ImportError:
        return
    # Le client du module HTTP qu'utilise huggingface_hub (httpx2 depuis la 2.x, httpx
    # avant) : avec un autre, ses erreurs (redirection relative…) lui échappent
    client = getattr(hf_http, "httpx2", None) or getattr(hf_http, "httpx", None)
    if client is None:
        return
    hook = getattr(hf_http, "hf_request_event_hook", None)
    hooks = {"request": [hook]} if hook else {}
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    set_client_factory(lambda: client.Client(event_hooks=hooks, follow_redirects=True, timeout=None,
                                             verify=context))


def decode(path: str):
    """Son du fichier en mono 16 kHz (float32), le format attendu par Whisper."""
    chunks = []
    with av.open(path) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def paragraphs(segments) -> str:
    """Phrases de Whisper regroupées en paragraphes : nouveau paragraphe après un
    silence, ou en fin de phrase quand le paragraphe devient long."""
    out, current, last_end = [], [], None
    for start, end, text in segments:
        text = text.strip()
        if not text:
            continue
        pause = last_end is not None and start - last_end >= PARAGRAPH_GAP
        too_long = sum(len(t) + 1 for t in current) >= PARAGRAPH_CHARS and current[-1].endswith((".", "!", "?", "…"))
        if current and (pause or too_long):
            out.append(" ".join(current))
            current = []
        current.append(text)
        last_end = end
    if current:
        out.append(" ".join(current))
    return "\n\n".join(out)


class Transcriber:
    def __init__(self):
        self._queue: queue.Queue[Job] = queue.Queue()
        self._jobs: dict[str, Job] = {}          # chemin → transcription en attente ou en cours
        self._lock = threading.Lock()
        self._model = None
        self._model_name = ""
        self._model_lock = threading.Lock()
        self._last_used = 0.0
        self._certs = False
        threading.Thread(target=self._loop, daemon=True, name="transcriber").start()

    @property
    def available(self) -> bool:
        return not MISSING

    def job(self, path: str) -> Job | None:
        with self._lock:
            return self._jobs.get(os.path.normcase(path))

    def submit(self, path: str, model: str, language: str, on_progress, on_done) -> Job | None:
        """Ajoute une transcription à la file (None si ce fichier y est déjà)."""
        key = os.path.normcase(path)
        with self._lock:
            if key in self._jobs:
                return None
            job = Job(path, model if model in MODELS else "small",
                      language if language in LANGUAGES else "", on_progress, on_done)
            self._jobs[key] = job
        self._queue.put(job)
        return job

    def cancel(self, path: str) -> bool:
        job = self.job(path)
        if job:
            job.cancelled.set()
        return job is not None

    # ── Thread de transcription ────────────────
    def _loop(self):
        while True:
            try:
                job = self._queue.get(timeout=30)
            except queue.Empty:
                self._maybe_unload()
                continue
            text, error = None, ""
            try:
                if job.cancelled.is_set():
                    raise Cancelled
                text = self._run(job)
            except Cancelled:
                error = "cancelled"
            except Exception as e:
                logger.log(f"Transcription : échec ({e})")
                error = short_error(e)
            finally:
                with self._lock:
                    self._jobs.pop(os.path.normcase(job.path), None)
                self._last_used = time.monotonic()
            try:
                job.on_done(job, text, error)
            except Exception as e:
                logger.log(f"Transcription : erreur après la fin ({e})")

    def _maybe_unload(self):
        with self._model_lock:
            if self._model is not None and time.monotonic() - self._last_used > UNLOAD_AFTER:
                self._model = None
                self._model_name = ""
                logger.log("Transcription : modèle libéré de la mémoire.")

    def _set(self, job: Job, state: str, progress: float = 0.0):
        job.state, job.progress = state, progress
        job.on_progress(job)

    def _load(self, job: Job):
        return self.model_for(job.model, lambda state: self._set(job, state), job.cancelled)

    def model_for(self, name: str, on_state=None, cancelled: threading.Event | None = None):
        """Modèle chargé, téléchargé au besoin (on_state("download" | "load")) ; un
        seul gardé en mémoire, partagé par les fichiers et la transcription en direct."""
        with self._model_lock:
            self._last_used = time.monotonic()
            if self._model is not None and self._model_name == name:
                return self._model
            self._model = None
            try:
                path = download_model(name, local_files_only=True)
            except Exception:
                if on_state:
                    on_state("download")
                if not self._certs:
                    _windows_certificates()
                    self._certs = True
                logger.log(f"Transcription : téléchargement du modèle « {name} » ({MODELS[name][1]})…")
                try:
                    path = download_model(name)
                except Exception as e:
                    logger.log(f"Transcription : téléchargement du modèle « {name} » impossible ({e})")
                    raise RuntimeError(f"téléchargement du modèle impossible ({short_error(e, 120)})") from e
            if cancelled is not None and cancelled.is_set():
                raise Cancelled
            if on_state:
                on_state("load")
            threads = max(1, (os.cpu_count() or 2) - 1)   # un cœur reste libre pour le reste
            self._model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=threads)
            self._model_name = name
            return self._model

    def live(self, model: str, language: str, on_update) -> "Live":
        """Transcription en direct (voir Live) ; on_update(live) à chaque changement."""
        return Live(self, model if model in MODELS else "small",
                    language if language in LANGUAGES else "", on_update)

    def _run(self, job: Job) -> str:
        model = self._load(job)
        self._set(job, "run")
        audio = decode(job.path)
        duration = len(audio) / RATE
        started = time.monotonic()
        segments, info = model.transcribe(audio, language=job.language or None, vad_filter=True)
        parts, last = [], 0.0
        for seg in segments:
            if job.cancelled.is_set():
                raise Cancelled
            parts.append((seg.start, seg.end, seg.text))
            now = time.monotonic()
            if now - last >= PROGRESS_EVERY and duration:
                self._set(job, "run", min(seg.end / duration, 0.99))
                last = now
        logger.log(f"Transcription : {os.path.basename(job.path)} ({info.language}, "
                   f"{duration:.0f} s d'audio en {time.monotonic() - started:.0f} s, modèle {job.model})")
        return paragraphs(parts)


class Live:
    """Transcription au fil de l'eau : feed() reçoit le son (mono 16 kHz, float32)
    pendant l'enregistrement ; il est découpé aux silences, chaque morceau est
    transcrit dans un thread. finish() : plus de son, le reste est transcrit
    puis state passe à « done ». Lus par l'appelant : state (download, load,
    run, done, error, cancelled), text, pending (secondes de son pas encore
    transcrites), error ; done est levé à la toute fin."""

    def __init__(self, owner: Transcriber, model: str, language: str, on_update):
        self._owner = owner
        self.model = model
        self.language = language
        self._on_update = on_update
        self.state = "load"
        self.text = ""
        self.error = ""
        self.pending = 0.0
        self.done = threading.Event()
        self._parts: list[tuple[float, float, str]] = []
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._closed = False
        # Découpage (thread de l'enregistrement)
        self._blocks: list = []
        self._size = 0                    # échantillons du morceau en cours
        self._offset = 0                  # échantillons déjà découpés (position du morceau)
        self._scan = np.zeros(0, np.float32)
        self._quiet = 0                   # échantillons de silence à la fin du morceau
        self._voiced = False              # le morceau contient du son
        self._floor = 1.0                 # bruit de fond estimé (RMS)
        threading.Thread(target=self._run, daemon=True, name="transcriber-live").start()

    # ── Son (thread de l'enregistrement) ───────
    def feed(self, samples):
        if self._closed or not len(samples):
            return
        self._blocks.append(samples)
        self._size += len(samples)
        self._scan = np.concatenate((self._scan, samples))
        n = len(self._scan) // LIVE_FRAME
        if n:
            frames = self._scan[:n * LIVE_FRAME].reshape(n, LIVE_FRAME)
            self._scan = self._scan[n * LIVE_FRAME:]
            floor = self._floor
            for rms in np.sqrt((frames * frames).mean(axis=1)):
                floor = min(float(rms), floor * 1.0015 + 1e-6)      # suit le bruit de fond, remonte lentement
                if rms > max(LIVE_QUIET, floor * 3):
                    self._voiced, self._quiet = True, 0
                else:
                    self._quiet += LIVE_FRAME
            self._floor = floor
        seconds = self._size / RATE
        if seconds >= LIVE_MAX or (self._voiced and seconds >= LIVE_MIN and self._quiet >= LIVE_GAP * RATE):
            self._cut()

    def _cut(self):
        audio = np.concatenate(self._blocks) if self._blocks else None
        start, voiced = self._offset / RATE, self._voiced
        self._offset += self._size
        self._blocks, self._size, self._quiet, self._voiced = [], 0, 0, False
        if audio is not None and voiced:                  # rien que du silence : rien à transcrire
            self.pending += len(audio) / RATE
            self._queue.put((start, audio))
            self._notify()

    def finish(self):
        """Fin de l'enregistrement : le reste du son est transcrit."""
        if self._closed:
            return
        if self._size >= RATE * 0.3:
            self._cut()
        self._closed = True
        self._queue.put(None)

    def cancel(self):
        self._closed = True
        self._cancel.set()
        self._queue.put(None)

    # ── Transcription ──────────────────────────
    def _notify(self):
        try:
            self._on_update(self)
        except Exception as e:
            logger.log(f"Transcription en direct : erreur d'affichage ({e})")

    def _set_state(self, state: str):
        self.state = state
        self._notify()

    def _run(self):
        try:
            model = self._owner.model_for(self.model, self._set_state, self._cancel)
            self._set_state("run")
            while True:
                item = self._queue.get()
                if item is None or self._cancel.is_set():
                    break
                start, audio = item
                segments, info = model.transcribe(
                    audio, language=self.language or None, vad_filter=True,
                    initial_prompt=self.text[-LIVE_CONTEXT:] or None, condition_on_previous_text=False)
                for seg in segments:
                    if self._cancel.is_set():
                        break
                    self._parts.append((start + seg.start, start + seg.end, seg.text))
                # Langue détectée sur le premier morceau parlé : gardée ensuite
                if not self.language and self._parts and info.language_probability >= 0.7:
                    self.language = info.language
                self.pending = max(0.0, self.pending - len(audio) / RATE)
                self.text = paragraphs(self._parts)
                self._owner._last_used = time.monotonic()
                self._notify()
            self.state = "cancelled" if self._cancel.is_set() else "done"
        except Cancelled:
            self.state = "cancelled"
        except Exception as e:
            logger.log(f"Transcription en direct : échec ({e})")
            self.state, self.error = "error", short_error(e)
        finally:
            self.pending = 0.0
            self._owner._last_used = time.monotonic()
            self.done.set()
            self._notify()
