"""
transcriber.py – Transcription des enregistrements en texte, en local, avec
Whisper (faster-whisper, sur le processeur).

Le modèle est téléchargé depuis Hugging Face à la première utilisation (cache
partagé de huggingface_hub), puis gardé en mémoire tant qu'il sert. Le son est
décodé ici (PyAV) plutôt que par faster-whisper, dont le décodeur ne suit pas
les versions récentes de PyAV.

Les transcriptions passent une par une, dans un thread dédié ; chacune peut être
annulée entre deux phrases.
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


class Cancelled(Exception):
    pass


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
        import httpx
        import truststore
        from huggingface_hub import set_client_factory
    except ImportError:
        return
    try:
        from huggingface_hub.utils._http import hf_request_event_hook
        hooks = {"request": [hf_request_event_hook]}
    except ImportError:
        hooks = {}
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    set_client_factory(lambda: httpx.Client(event_hooks=hooks, follow_redirects=True, timeout=None,
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
                error = str(e) or e.__class__.__name__
            finally:
                with self._lock:
                    self._jobs.pop(os.path.normcase(job.path), None)
                self._last_used = time.monotonic()
            try:
                job.on_done(job, text, error)
            except Exception as e:
                logger.log(f"Transcription : erreur après la fin ({e})")

    def _maybe_unload(self):
        if self._model is not None and time.monotonic() - self._last_used > UNLOAD_AFTER:
            self._model = None
            self._model_name = ""
            logger.log("Transcription : modèle libéré de la mémoire.")

    def _set(self, job: Job, state: str, progress: float = 0.0):
        job.state, job.progress = state, progress
        job.on_progress(job)

    def _load(self, job: Job):
        if self._model is not None and self._model_name == job.model:
            return self._model
        self._model = None
        try:
            path = download_model(job.model, local_files_only=True)
        except Exception:
            self._set(job, "download")
            if not self._certs:
                _windows_certificates()
                self._certs = True
            logger.log(f"Transcription : téléchargement du modèle « {job.model} » ({MODELS[job.model][1]})…")
            path = download_model(job.model)
        if job.cancelled.is_set():
            raise Cancelled
        self._set(job, "load")
        threads = max(1, (os.cpu_count() or 2) - 1)   # un cœur reste libre pour le reste
        self._model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=threads)
        self._model_name = job.model
        return self._model

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
