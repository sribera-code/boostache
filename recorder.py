"""
recorder.py – Enregistreur : le son qui sort du PC (capture « loopback » d'une
sortie audio, wasapi.py), avec le micro en option.

L'enregistrement est écrit au fil de l'eau en WAV (16 bits, 48 kHz, stéréo),
en-tête mis à jour toutes les quelques secondes : un arrêt brutal laisse un
fichier lisible. À l'arrêt, il est converti en MP3 ou M4A par l'encodeur
intégré à Windows (Windows.Media.Transcoding, via PowerShell comme ocr.py) ;
le WAV est gardé si la conversion échoue. Un enregistrement peut aussi être
transcrit en texte (transcriber.py) : « nom.txt » à côté du fichier audio.

Les fichiers sont rangés dans un dossier choisi par l'utilisateur (par défaut
Musique\\Boostache) : la liste affichée est celle des fichiers audio de ce
dossier, renommés ou ajoutés depuis l'explorateur compris.

La page les lit par un hôte virtuel WebView2 (app.py) posé sur une jonction
DATA_DIR\\recordings → dossier choisi : WebView2 n'applique un changement
d'hôte virtuel qu'au chargement suivant de la page, alors que la jonction peut
être réorientée à tout moment.
"""

import os
import re
import stat
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from clipboard_listener import set_clipboard_files
from engine import logger
from storage import DATA_DIR, settings
from transcriber import LANGUAGES, MISSING as STT_MISSING, MODELS, Transcriber
from winutil import recycle, reveal_in_explorer

try:
    import numpy as np
    import comtypes
    import wasapi
    MISSING = ""
except ImportError as e:
    MISSING = f"librairie '{e.name}' absente (pip install {e.name})"

FORMATS = ("mp3", "m4a", "wav")
AUDIO_EXTS = {".mp3", ".m4a", ".wav"}
POLL = 0.01                     # secondes entre deux lectures des flux
LEVEL_EVERY = 0.08              # niveaux envoyés à l'interface
HEADER_EVERY = 2.0              # mise à jour de l'en-tête WAV
RETRY_EVERY = 1.0               # sortie audio perdue : nouvel essai
MIC_PRIME = 0.05                # micro : avance gardée (s) pour absorber les à-coups
MIC_MAX = 0.3                   # au-delà, l'excédent est jeté (horloges différentes)
TRANSCODE_TIMEOUT = 1800
LINK = DATA_DIR / "recordings"  # jonction vers le dossier des enregistrements
INVALID_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

_TRANSCODE = r"""
$ErrorActionPreference = 'Stop'
$SrcPath = $env:BOOSTACHE_SRC
$DstPath = $env:BOOSTACHE_DST
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Transcoding.MediaTranscoder, Windows.Media.Transcoding, ContentType = WindowsRuntime]
$null = [Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime]
$ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTask = ($ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskProgress = ($ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncActionWithProgress`1' })[0]
function Await($op, [Type]$type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $null = $task.Wait(-1)
  $task.Result
}
$src = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($SrcPath)) ([Windows.Storage.StorageFile])
$folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync([IO.Path]::GetDirectoryName($DstPath))) ([Windows.Storage.StorageFolder])
$dst = Await ($folder.CreateFileAsync([IO.Path]::GetFileName($DstPath), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
$quality = [Windows.Media.MediaProperties.AudioEncodingQuality]::High
if ($DstPath.ToLower().EndsWith('.mp3')) { $encoding = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp3($quality) }
else { $encoding = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateM4a($quality) }
$transcoder = New-Object Windows.Media.Transcoding.MediaTranscoder
$prepared = Await ($transcoder.PrepareFileTranscodeAsync($src, $dst, $encoding)) ([Windows.Media.Transcoding.PrepareTranscodeResult])
if (-not $prepared.CanTranscode) { [Console]::Error.WriteLine($prepared.FailureReason); exit 4 }
$task = $asTaskProgress.MakeGenericMethod([double]).Invoke($null, @($prepared.TranscodeAsync()))
$null = $task.Wait(-1)
"""


def default_folder() -> Path:
    try:
        from platformdirs import user_music_dir
        music = Path(user_music_dir())
    except Exception:
        music = Path.home() / "Music"
    return music / "Boostache"


def transcode(src: Path, dst: Path) -> str:
    """Convertit un WAV en MP3 ou M4A (extension de dst). Chaîne vide si c'est fait,
    sinon la cause de l'échec."""
    env = {**os.environ, "BOOSTACHE_SRC": str(src.resolve()), "BOOSTACHE_DST": str(dst.resolve())}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TRANSCODE],
            capture_output=True, encoding="utf-8", errors="replace", timeout=TRANSCODE_TIMEOUT,
            env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        return str(e)
    if result.returncode or not dst.exists() or not dst.stat().st_size:
        lines = (result.stderr or "").strip().splitlines()
        return lines[0] if lines else f"code {result.returncode}"
    return ""


class WavWriter:
    """WAV PCM 16 bits écrit au fil de l'eau."""

    def __init__(self, path: Path):
        self.path = path
        self._file = open(path, "xb")
        self._bytes = 0
        self._file.write(self._header())

    def _header(self) -> bytes:
        rate, channels, width = wasapi.RATE, wasapi.CHANNELS, 2
        le = lambda n, size: int(n).to_bytes(size, "little")
        return (b"RIFF" + le(36 + self._bytes, 4) + b"WAVEfmt " + le(16, 4) + le(1, 2) + le(channels, 2)
                + le(rate, 4) + le(rate * channels * width, 4) + le(channels * width, 2) + le(width * 8, 2)
                + b"data" + le(self._bytes, 4))

    def write(self, data: bytes):
        self._file.write(data)
        self._bytes += len(data)

    def update_header(self):
        """Tailles à jour : le fichier est lisible même si l'application s'arrête net."""
        end = self._file.tell()
        self._file.seek(0)
        self._file.write(self._header())
        self._file.seek(end)
        self._file.flush()

    def close(self):
        self.update_header()
        self._file.close()


class Session(threading.Thread):
    """Un enregistrement : lit la sortie (et le micro), mixe, écrit le WAV."""

    def __init__(self, service, path: Path, device_id: str, mic: bool):
        super().__init__(daemon=True, name="recorder")
        self._service = service
        self.path = path
        self.device_id = device_id
        self.device_name = ""
        self.mic = mic
        self.mic_name = ""
        self.frames = 0
        self.paused = False
        self.error = ""
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._out = self._silence = self._mic = None
        self._mic_buf = None
        self._mic_primed = False
        self._writer: WavWriter | None = None

    @property
    def seconds(self) -> float:
        return self.frames / wasapi.RATE

    def stop(self):
        self._stop.set()

    # ── Flux ───────────────────────────────────
    def _open_output(self):
        device, _id, self.device_name = wasapi.get_device(wasapi.E_RENDER, self.device_id)
        self._silence = wasapi.SilenceStream(device)
        self._out = wasapi.CaptureStream(device, loopback=True)

    def _close_output(self):
        for stream in (self._out, self._silence):
            if stream:
                stream.close()
        self._out = self._silence = None

    def _open_mic(self):
        try:
            device, _id, self.mic_name = wasapi.get_device(wasapi.E_CAPTURE)
            self._mic = wasapi.CaptureStream(device, loopback=False)
            self._mic_buf = np.zeros(0, np.float32)
            self._mic_primed = False
        except comtypes.COMError as e:
            self._mic = None
            self.mic = False
            self._service.toast(f"Micro indisponible ({e.text or e.hresult}) : enregistrement sans le micro.")

    def _drop_mic(self, reason: str):
        if self._mic:
            self._mic.close()
        self._mic = None
        self.mic = False
        self._service.toast(f"Micro {reason} : l'enregistrement continue sans lui.")
        self._service.emit_state()

    # ── Boucle ─────────────────────────────────
    def run(self):
        with wasapi.com_thread():
            try:
                self._writer = WavWriter(self.path)
                self._open_output()
                if self.mic:
                    self._open_mic()
            except Exception as e:
                self.error = getattr(e, "text", None) or str(e)
                self._close_output()
                if self._writer:
                    self._writer.close()
                    self.path.unlink(missing_ok=True)
                self.ready.set()
                return
            self.ready.set()
            try:
                self._loop()
            except Exception as e:
                logger.log(f"Enregistreur : arrêt sur erreur ({e})")
                self.error = str(e)
            finally:
                self._close_output()
                if self._mic:
                    self._mic.close()
                self._writer.close()
        self._service.session_ended(self)

    def _loop(self):
        last_level = last_header = time.monotonic()
        peaks = [0.0, 0.0]
        lost = False
        while not self._stop.wait(POLL):
            now = time.monotonic()
            if lost:
                # Sortie débranchée ou changée : on reprend dès qu'une sortie répond
                if now - lost < RETRY_EVERY:
                    continue
                try:
                    self._open_output()
                    lost = False
                    logger.log(f"Enregistreur : capture reprise sur « {self.device_name} ».")
                    self._service.emit_state()
                except comtypes.COMError:
                    self._close_output()
                    lost = now
                    continue
            try:
                self._silence.feed()
                data = self._out.read()
            except comtypes.COMError as e:
                if not wasapi.is_invalidated(e):
                    raise
                logger.log(f"Enregistreur : sortie « {self.device_name} » perdue, nouvel essai…")
                self._close_output()
                lost = now
                continue
            mic = self._read_mic()
            if data:
                out = np.frombuffer(data, np.float32)
                mixed = out
                if mic is not None:
                    m = self._take_mic(len(out))
                    peaks[1] = max(peaks[1], float(np.abs(m).max()) if len(m) else 0.0)
                    mixed = out + m
                if not self.paused:
                    peaks[0] = max(peaks[0], float(np.abs(out).max()))
                    pcm = (np.clip(mixed, -1.0, 1.0) * 32767).astype("<i2")
                    self._writer.write(pcm.tobytes())
                    self.frames += len(out) // wasapi.CHANNELS
            if now - last_header >= HEADER_EVERY:
                self._writer.update_header()
                last_header = now
            if now - last_level >= LEVEL_EVERY:
                if self.paused:
                    peaks = [0.0, 0.0]
                self._service.emit_level(self.seconds, peaks[0], peaks[1] if self._mic else None)
                peaks = [0.0, 0.0]
                last_level = now

    def _read_mic(self):
        if not self._mic:
            return None
        try:
            data = self._mic.read()
        except comtypes.COMError as e:
            if not wasapi.is_invalidated(e):
                raise
            self._drop_mic("débranché")
            return None
        if data:
            self._mic_buf = np.concatenate((self._mic_buf, np.frombuffer(data, np.float32)))
        return self._mic_buf

    def _take_mic(self, count: int):
        """Échantillons du micro à ajouter à ceux de la sortie. Les deux
        périphériques n'ont pas tout à fait la même horloge : une petite avance
        est gardée, l'excédent jeté, et un manque comblé par du silence."""
        buf = self._mic_buf
        prime = int(MIC_PRIME * wasapi.RATE) * wasapi.CHANNELS
        if not self._mic_primed:
            if len(buf) < prime + count:
                return np.zeros(count, np.float32)
            self._mic_primed = True
        if len(buf) < count:
            self._mic_primed = False
            return np.zeros(count, np.float32)
        taken, buf = buf[:count], buf[count:]
        limit = int(MIC_MAX * wasapi.RATE) * wasapi.CHANNELS
        if len(buf) > limit:
            buf = buf[-prime:]
        self._mic_buf = buf
        return taken


class RecorderService:
    def __init__(self, bridge, is_visible):
        self._bridge = bridge
        self._is_visible = is_visible
        self._lock = threading.Lock()
        self._session: Session | None = None
        self._converting: set[str] = set()      # noms des WAV en cours de conversion
        self._shutting_down = False
        self._served: Path | None = None
        self.playback_host = ""                 # hôte virtuel WebView2 du dossier (app.py)
        self.transcriber = Transcriber()

    @property
    def available(self) -> bool:
        return not MISSING

    def folder(self) -> Path:
        path = Path(settings.get("audio_dir") or default_folder())
        path.mkdir(parents=True, exist_ok=True)
        return path

    def toast(self, text: str, kind: str = "info"):
        self._bridge.emit("toast", {"text": text, "kind": kind})

    # ── État envoyé à l'interface ──────────────
    def snapshot(self) -> dict:
        try:
            folder = str(self.folder())
        except OSError:
            folder = str(settings.get("audio_dir") or default_folder())
        return {
            "available": self.available,
            "reason":    MISSING,
            "folder":    folder,
            "format":    settings.get("audio_format"),
            "device":    settings.get("audio_device"),
            "mic":       bool(settings.get("audio_mic")),
            "session":   self._session_state(),
            "files":     self.files(),
            "transcribe": {
                "available": self.transcriber.available,
                "reason":    STT_MISSING,
                "model":     settings.get("transcribe_model"),
                "language":  settings.get("transcribe_language"),
                "auto":      bool(settings.get("audio_transcribe")),
                "models":    [{"id": k, "label": label, "size": size} for k, (label, size) in MODELS.items()],
                "languages": [{"id": k, "label": label} for k, label in LANGUAGES.items()],
            },
        }

    def _session_state(self) -> dict | None:
        s = self._session
        if not s or not s.ready.is_set() or s.error:
            return None
        return {"name": s.path.name, "seconds": s.seconds, "paused": s.paused,
                "device": s.device_name, "mic": s.mic_name if s.mic else ""}

    def emit_state(self):
        self._bridge.emit("rec:state", {"session": self._session_state()})

    def emit_files(self):
        self._bridge.emit("rec:files", {"files": self.files()})

    def emit_level(self, seconds: float, out: float, mic: float | None):
        if self._is_visible():
            self._bridge.emit("rec:level", {"seconds": seconds, "out": out, "mic": mic})

    # ── Périphériques ──────────────────────────
    def devices(self) -> dict:
        if MISSING:
            return {"outputs": [], "mic": ""}
        with wasapi.com_thread():
            try:
                outputs = wasapi.list_devices(wasapi.E_RENDER)
            except Exception as e:
                logger.log(f"Enregistreur : liste des sorties audio impossible ({e})")
                outputs = []
            try:
                mic = next((d["name"] for d in wasapi.list_devices(wasapi.E_CAPTURE) if d["default"]), "")
            except Exception:
                mic = ""
        return {"outputs": outputs, "mic": mic}

    # ── Enregistrement ─────────────────────────
    def start(self, device_id: str = "", mic: bool = False) -> dict:
        if MISSING:
            return {"error": MISSING}
        with self._lock:
            if self._session:
                return {"error": "Un enregistrement est déjà en cours."}
            try:
                path = self._new_path()
            except OSError as e:
                return {"error": f"Dossier inaccessible ({e.strerror or e})"}
            session = Session(self, path, device_id or "", bool(mic))
            self._session = session
        session.start()
        if not session.ready.wait(10) or session.error:
            with self._lock:
                self._session = None
            error = session.error or "le périphérique audio ne répond pas"
            logger.log(f"Enregistreur : démarrage impossible ({error})")
            return {"error": f"Enregistrement impossible : {error}"}
        settings.set("audio_device", device_id or "")
        settings.set("audio_mic", bool(mic))
        mic_info = f" + micro « {session.mic_name} »" if session.mic else ""
        logger.log(f"Enregistreur : début ({session.device_name}{mic_info}) → {path.name}")
        self.emit_state()
        return {"ok": True, "session": self._session_state()}

    def pause(self, paused: bool):
        s = self._session
        if s:
            s.paused = bool(paused)
            self.emit_state()

    def stop(self) -> bool:
        s = self._session
        if not s:
            return False
        s.stop()
        s.join(10)
        return True

    def session_ended(self, session: Session):
        """Appelé par le thread de l'enregistrement une fois le WAV fermé."""
        with self._lock:
            if self._session is session:
                self._session = None
        self.emit_state()
        if session.error:
            self.toast(f"Enregistrement interrompu : {session.error}", "error")
        if not session.path.exists():
            self.emit_files()
            return
        if session.frames == 0:
            session.path.unlink(missing_ok=True)
            self.emit_files()
            return
        duration = _duration(session.seconds)
        fmt = settings.get("audio_format")
        logger.log(f"Enregistreur : fin, {duration} → {session.path.name}")
        if fmt in ("mp3", "m4a") and not self._shutting_down:
            self._convert(session.path, fmt)
        else:
            self.emit_files()
            self._auto_transcribe(session.path)

    def shutdown(self):
        """Fermeture de l'application : l'enregistrement en cours est gardé en WAV."""
        self._shutting_down = True
        self.stop()

    def _convert(self, wav: Path, fmt: str):
        with self._lock:
            self._converting.add(wav.name)
        self.emit_files()

        def run():
            target = _unique(wav.with_suffix(f".{fmt}"))
            error = transcode(wav, target)
            if error:
                target.unlink(missing_ok=True)
                logger.log(f"Enregistreur : conversion en {fmt.upper()} impossible ({error}), WAV gardé.")
                self.toast(f"Conversion en {fmt.upper()} impossible : l'enregistrement reste en WAV.", "error")
            else:
                try:
                    wav.unlink()
                except OSError:
                    pass
                self.toast(f"Enregistré : {target.name}", "success")
            with self._lock:
                self._converting.discard(wav.name)
            self.emit_files()
            self._auto_transcribe(wav if error else target)

        threading.Thread(target=run, daemon=True, name="recorder-convert").start()

    def _new_path(self) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d %Hh%M")
        folder = self.folder()
        return _unique(folder / f"Enregistrement {stamp}.wav", also=FORMATS)

    # ── Fichiers ───────────────────────────────
    def files(self) -> list[dict]:
        try:
            folder = self.folder()
            entries = list(os.scandir(folder))
        except OSError:
            return []
        current = self._session.path.name if self._session else None
        with self._lock:
            converting = set(self._converting)
        # Fichier en cours d'écriture par une conversion : masqué jusqu'à la fin
        pending = {Path(n).stem.lower() for n in converting}
        texts = {Path(e.name).stem.lower() for e in entries if e.name.lower().endswith(".txt")}
        files = []
        for entry in entries:
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in AUDIO_EXTS or not entry.is_file() or entry.name == current:
                continue
            if ext != ".wav" and Path(entry.name).stem.lower() in pending:
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            job = self.transcriber.job(entry.path)
            files.append({
                "name":         entry.name,
                "title":        Path(entry.name).stem,
                "format":       ext[1:].upper(),
                "size":         st.st_size,
                "mtime":        datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "duration":     _wav_seconds(entry.path) if ext == ".wav" else None,
                "converting":   entry.name in converting,
                "transcript":   Path(entry.name).stem.lower() in texts,
                "transcribing": _job_state(job),
                "url":          self._url(entry.name, st.st_mtime_ns),
            })
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return files

    def _url(self, name: str, version: int) -> str | None:
        if not self.playback_host:
            return None
        return f"https://{self.playback_host}/{quote(name)}?v={version}"

    def _file(self, name: str) -> Path | None:
        """Fichier audio du dossier (jamais en dehors), None s'il n'existe pas."""
        if not isinstance(name, str) or not name or os.path.basename(name) != name:
            return None
        path = self.folder() / name
        return path if path.suffix.lower() in AUDIO_EXTS and path.is_file() else None

    def _busy(self, name: str) -> bool:
        if self.transcriber.job(str(self.folder() / name)):
            return True
        with self._lock:
            return name in self._converting or (self._session is not None and self._session.path.name == name)

    def rename(self, name: str, title: str) -> dict:
        path = self._file(name)
        if not path:
            return {"error": "Ce fichier n'existe plus."}
        if self._busy(name):
            return {"error": "Fichier en cours d'utilisation."}
        title = (title or "").strip().rstrip(".")
        if not title or INVALID_NAME.search(title):
            return {"error": 'Nom invalide (caractères interdits : \\ / : * ? " < > |).'}
        target = path.with_name(title + path.suffix)
        if target.name == path.name:
            return {"ok": True}
        if target.exists() and target.name.lower() != path.name.lower():
            return {"error": "Un fichier porte déjà ce nom."}
        try:
            path.rename(target)
        except OSError as e:
            return {"error": f"Renommage impossible ({e.strerror or e})"}
        # La transcription suit son enregistrement
        text, new_text = path.with_suffix(".txt"), target.with_suffix(".txt")
        if text.is_file() and (not new_text.exists() or new_text.name.lower() == text.name.lower()):
            try:
                text.rename(new_text)
            except OSError:
                pass
        self.emit_files()
        return {"ok": True, "name": target.name}

    def delete(self, name: str) -> bool:
        path = self._file(name)
        if not path or self._busy(name):
            return False
        ok = recycle(str(path))
        if ok:
            logger.log(f"Enregistreur : {name} envoyé à la corbeille.")
            text = path.with_suffix(".txt")
            if text.is_file():
                recycle(str(text))
        self.emit_files()
        return ok

    # ── Transcription ──────────────────────────
    def transcribe(self, name: str) -> dict:
        """Met l'enregistrement en file de transcription (modèle et langue des réglages)."""
        if STT_MISSING:
            return {"error": STT_MISSING}
        path = self._file(name)
        if not path:
            return {"error": "Ce fichier n'existe plus."}
        with self._lock:
            if name in self._converting:
                return {"error": "Conversion en cours : la transcription pourra suivre."}
        job = self.transcriber.submit(str(path), settings.get("transcribe_model"),
                                      settings.get("transcribe_language"),
                                      self._on_transcribe_progress, self._on_transcribed)
        if job is None:
            return {"error": "Transcription déjà en cours."}
        self._on_transcribe_progress(job)
        return {"ok": True}

    def cancel_transcription(self, name: str) -> bool:
        return self.transcriber.cancel(str(self.folder() / name))

    def _auto_transcribe(self, path: Path):
        if settings.get("audio_transcribe") and not STT_MISSING and path.exists() and not self._shutting_down:
            self.transcribe(path.name)

    def _on_transcribe_progress(self, job):
        self._bridge.emit("rec:transcribe", {"name": os.path.basename(job.path), **_job_state(job)})

    def _on_transcribed(self, job, text: str | None, error: str):
        path = Path(job.path)
        if text is not None:
            if not text.strip():
                text = "(aucune parole reconnue)"
            try:
                path.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
                self._bridge.emit("rec:transcribed", {"name": path.name, "title": path.stem})
            except OSError as e:
                self.toast(f"Transcription non enregistrée : {e.strerror or e}", "error")
        elif error and error != "cancelled":
            self.toast(f"Transcription impossible : {error}", "error")
        self.emit_files()

    def transcript(self, name: str) -> dict | None:
        """Texte de la transcription (et chemin du fichier texte)."""
        path = self._file(name)
        text = path.with_suffix(".txt") if path else None
        if not text or not text.is_file():
            return None
        try:
            return {"text": text.read_text(encoding="utf-8-sig", errors="replace").strip(), "path": str(text)}
        except OSError:
            return None

    def open_transcript(self, name: str) -> bool:
        info = self.transcript(name)
        if info:
            os.startfile(info["path"])
        return bool(info)

    def reveal(self, name: str):
        path = self._file(name)
        reveal_in_explorer(str(path or self.folder()))

    def copy_file(self, name: str) -> bool:
        path = self._file(name)
        return bool(path) and set_clipboard_files([str(path)])

    def set_folder(self, path: str) -> str | None:
        if not path or not os.path.isdir(path):
            return None
        settings.set("audio_dir", "" if Path(path) == default_folder() else str(Path(path)))
        folder = str(self.folder())
        # Page servie par la jonction : elle suit le nouveau dossier. Sinon
        # (dossier servi directement), la lecture attend le prochain démarrage.
        if self.playback_host and not (self._served == LINK and self._link()):
            self.playback_host = ""
        self.emit_files()
        return folder

    def served_folder(self) -> Path:
        """Dossier à servir à la page (une seule fois, au démarrage) : la jonction,
        ou le dossier lui-même si elle est impossible (lecteur réseau…)."""
        self._served = LINK if self._link() else self.folder()
        return self._served

    def _link(self) -> bool:
        """(Ré)oriente la jonction vers le dossier des enregistrements."""
        try:
            if _is_junction(LINK):
                os.rmdir(LINK)          # retire le lien seul, jamais le contenu
            elif LINK.exists():
                return False            # vrai dossier à ce nom : on n'y touche pas
            import _winapi
            _winapi.CreateJunction(str(self.folder()), str(LINK))
            return True
        except OSError as e:
            logger.log(f"Enregistreur : jonction vers le dossier impossible ({e})")
            return False


def _job_state(job) -> dict | None:
    """Transcription en attente ou en cours, pour l'interface."""
    return {"state": job.state, "progress": round(job.progress, 3)} if job else None


def _is_junction(path: Path) -> bool:
    try:
        return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except OSError:
        return False


def _unique(path: Path, also=()) -> Path:
    """Chemin libre : « nom (2).ext », « nom (3).ext »… (aussi libre sous les extensions `also`)."""
    def taken(p: Path) -> bool:
        return p.exists() or any(p.with_suffix(f".{ext}").exists() for ext in also)

    candidate, n = path, 2
    while taken(candidate):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def _wav_seconds(path: str) -> float | None:
    """Durée d'un WAV d'après son en-tête (les autres formats : lue par l'interface)."""
    try:
        with wave.open(path, "rb") as f:
            return f.getnframes() / f.getframerate()
    except Exception:
        return None


def _duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
