"""
speech.py – Lecture à voix haute.

Trois moteurs, du plus naturel au plus simple (même choix que le projet
audio-report) :
  - edge  : voix neuronales de Microsoft, celles de « Lire à voix haute »
            d'Edge (edge-tts, gratuit, sans clé). Le texte part chez
            Microsoft : il faut internet ;
  - piper : voix neuronale locale (onnxruntime, processeur), modèles dans
            ~/.local/share/piper/voices (python -m piper.download_voices …) ;
  - sapi  : voix de Windows (SAPI 5), toujours là.
Un moteur qui ne répond pas (réseau coupé, voix absente) cède la place au
suivant de la liste ; on ne remonte jamais vers edge, qui envoie le texte.

Le texte est découpé en phrases. Chacune est synthétisée en PCM 16 bits mono
pendant que la précédente est jouée (la lecture démarre vite, sans trou), puis
jouée par WASAPI (wasapi.RenderStream) : pause, phrase précédente ou suivante et
changement de vitesse en cours de lecture.

Usage :
    tts.speak("Bonjour", on_done=ma_callback, source="notes:1")
    tts.pause() / tts.skip(-1) / tts.stop()
    tts.subscribe(fn)   # fn(state: dict), voir TTSEngine.state()
"""

import asyncio
import io
import locale
import math
import re
import ssl
import threading
import time
from collections import OrderedDict, namedtuple
from pathlib import Path

ENGINES = ("edge", "piper", "sapi")          # aussi l'ordre de repli
ENGINE_LABELS = {"edge": "Voix naturelle", "piper": "Hors ligne", "sapi": "Windows"}
VOICE_NAMES = {"edge": "voix naturelle", "piper": "voix hors ligne", "sapi": "voix de Windows"}

# Voix françaises d'edge-tts (python -m edge_tts --list-voices) : id, nom, région, genre
EDGE_VOICES = (
    ("fr-FR-DeniseNeural", "Denise", "France", "F"),
    ("fr-FR-HenriNeural", "Henri", "France", "M"),
    ("fr-FR-EloiseNeural", "Éloïse", "France", "F"),
    ("fr-FR-VivienneMultilingualNeural", "Vivienne", "France, multilingue", "F"),
    ("fr-FR-RemyMultilingualNeural", "Rémy", "France, multilingue", "M"),
    ("fr-CA-SylvieNeural", "Sylvie", "Québec", "F"),
    ("fr-CA-AntoineNeural", "Antoine", "Québec", "M"),
    ("fr-CA-JeanNeural", "Jean", "Québec", "M"),
    ("fr-CA-ThierryNeural", "Thierry", "Québec", "M"),
    ("fr-BE-CharlineNeural", "Charline", "Belgique", "F"),
    ("fr-BE-GerardNeural", "Gérard", "Belgique", "M"),
    ("fr-CH-ArianeNeural", "Ariane", "Suisse", "F"),
    ("fr-CH-FabriceNeural", "Fabrice", "Suisse", "M"),
)
DEFAULT_VOICES = {"edge": "fr-CA-SylvieNeural", "piper": "fr_FR-siwis-medium", "sapi": ""}
SPEEDS = (0.75, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0)
MIN_SPEED, MAX_SPEED = 0.5, 2.0

PIPER_DIR = Path.home() / ".local" / "share" / "piper" / "voices"
EDGE_RATE = 24000                 # edge-tts renvoie du MP3 mono 24 kHz
SAPI_RATE = 22050
SAPI_FORMAT = 22                  # SAFT22kHz16BitMono
SVSF_IS_NOT_XML = 16              # « < » lu tel quel, pas comme une balise

AHEAD = 3                         # phrases synthétisées d'avance
MIN_CHUNK = 40                    # une phrase plus courte est lue avec la suivante
MAX_CHUNK = 280                   # au-delà, coupée aux virgules (ou aux espaces)
LINE_PAUSE = 0.15                 # silence après une ligne (s)
PARAGRAPH_PAUSE = 0.45            # … après un paragraphe
RESTART_AFTER = 2.5               # « précédent » au-delà : reprend la phrase en cours
EDGE_RETRY_AFTER = 60             # edge en panne : pas retenté avant (s)
CACHE_BYTES = 48 << 20            # audio déjà synthétisé (relecture, retour en arrière)
PIPER_IDLE = 600                  # voix Piper libérée après 10 min sans lecture

Audio = namedtuple("Audio", "pcm rate")


class EngineUnavailable(Exception):
    """Moteur inutilisable pour le moment (réseau, module ou voix absents) : on
    passe au suivant."""


# ─────────────────────────────────────────────
#  Préparation du texte
# ─────────────────────────────────────────────
_URL = re.compile(r"\b(?:https?://|www\.)([^\s/?#<>\"']+)[^\s<>\"']*", re.I)
_SYMBOLS = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")
_ARROW = re.compile(r"\s*(?:-+>|=+>|[→⇒➜➔])\s*")
_SENTENCE_END = re.compile(r"[.!?…]+[\"»”’')\]]*\s+(?=[«\"“'(\[A-ZÀ-ÖØ-Þ0-9—–-])")
_HAS_WORD = re.compile(r"[^\W_]")


def clean_text(text: str) -> str:
    """Ce qui se lit mal : puces de liste et émojis retirés, liens réduits à
    leur site, flèches changées en pause, ponctuation répétée simplifiée."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?m)^[ \t]*(?:[-*+•◦▪‣·][ \t]+)", "", text)      # puces de liste
    text = _URL.sub(lambda m: m.group(1).removeprefix("www."), text)
    text = _SYMBOLS.sub("", text)
    text = _ARROW.sub(", ", text)
    text = re.sub(r"([!?])\1+", r"\1", text)
    text = re.sub(r"\.{3,}", "…", text)
    return re.sub(r"[ \t\u00A0]+", " ", text)


def _sentences(line: str) -> list[str]:
    out, start = [], 0
    for m in _SENTENCE_END.finditer(line):
        out.append(line[start:m.end()].strip())
        start = m.end()
    out.append(line[start:].strip())
    return [s for s in out if s]


def _cut(sentence: str) -> list[str]:
    """Phrase trop longue : morceaux d'au plus MAX_CHUNK caractères, coupés après
    une virgule ou un point-virgule, sinon à un espace."""
    if len(sentence) <= MAX_CHUNK:
        return [sentence]
    out, cur = [], ""
    for part in re.split(r"(?<=[,;:])\s+", sentence):
        while len(part) > MAX_CHUNK:
            cut = part.rfind(" ", 0, MAX_CHUNK)
            cut = cut if cut > 0 else MAX_CHUNK
            if cur:
                out.append(cur)
                cur = ""
            out.append(part[:cut])
            part = part[cut:].lstrip()
        if cur and len(cur) + 1 + len(part) > MAX_CHUNK:
            out.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}" if cur else part
    if cur:
        out.append(cur)
    return out


def split_text(text: str) -> list[tuple[str, float]]:
    """Morceaux à synthétiser un par un : (texte, silence après, en secondes).
    Une ligne sans lettre ni chiffre (séparateur, cadre) n'est pas lue."""
    chunks: list[tuple[str, float]] = []
    for para in re.split(r"\n\s*\n", clean_text(text)):
        lines = [l.strip() for l in para.split("\n") if _HAS_WORD.search(l)]
        for li, line in enumerate(lines):
            merged: list[str] = []
            for piece in (p for s in _sentences(line) for p in _cut(s)):
                if merged and len(merged[-1]) < MIN_CHUNK and len(merged[-1]) + len(piece) < MAX_CHUNK:
                    merged[-1] += " " + piece
                else:
                    merged.append(piece)
            if len(merged) > 1 and len(merged[-1]) < MIN_CHUNK \
                    and len(merged[-2]) + len(merged[-1]) < MAX_CHUNK:
                merged[-2] += " " + merged.pop()
            for k, piece in enumerate(merged):
                end_of_line = k == len(merged) - 1
                pause = (PARAGRAPH_PAUSE if li == len(lines) - 1 else LINE_PAUSE) if end_of_line else 0.0
                chunks.append((piece, pause))
    if chunks:
        chunks[-1] = (chunks[-1][0], 0.0)
    return chunks


# ─────────────────────────────────────────────
#  Moteurs : texte → Audio (PCM 16 bits mono)
# ─────────────────────────────────────────────
def _short_error(error: Exception) -> str:
    text = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return (text or type(error).__name__)[:160]


_edge_ready = False


def _edge_setup():
    """Certificats vérifiés par le magasin de Windows (truststore) : derrière un
    proxy TLS, OpenSSL refuse la chaîne. Limité à edge-tts, sans toucher au
    reste du processus (pas d'inject_into_ssl)."""
    global _edge_ready
    if _edge_ready:
        return
    _edge_ready = True
    try:
        import truststore
        import edge_tts.communicate
        edge_tts.communicate._SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass


def _decode_mp3(data: bytes) -> bytes:
    import av
    out = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=EDGE_RATE)
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            out += [f.to_ndarray().tobytes() for f in resampler.resample(frame)]
    out += [f.to_ndarray().tobytes() for f in resampler.resample(None)]
    return b"".join(out)


def synth_edge(text: str, voice: str, speed: float) -> Audio:
    try:
        import edge_tts
        from edge_tts.exceptions import NoAudioReceived
    except ImportError as e:
        raise EngineUnavailable("module edge-tts absent (pip install edge-tts)") from e
    _edge_setup()

    async def fetch() -> bytes:
        data = bytearray()
        comm = edge_tts.Communicate(text, voice or DEFAULT_VOICES["edge"],
                                    rate=f"{round((speed - 1) * 100):+d}%",
                                    connect_timeout=6, receive_timeout=20)
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        return bytes(data)

    try:
        mp3 = asyncio.run(fetch())
    except NoAudioReceived:
        return Audio(b"", EDGE_RATE)          # rien de prononçable
    except Exception as e:
        raise EngineUnavailable(_short_error(e)) from e
    return Audio(_decode_mp3(mp3), EDGE_RATE)


_piper_lock = threading.Lock()
_piper_loaded: dict = {}                       # une seule voix gardée en mémoire
_piper_timer: threading.Timer | None = None


def _piper_path(name: str) -> Path:
    path = Path(name)
    if path.suffix == ".onnx" and path.exists():
        return path
    return PIPER_DIR / f"{name.removesuffix('.onnx')}.onnx"


def _piper_voice(name: str):
    name = name or DEFAULT_VOICES["piper"]
    with _piper_lock:
        voice = _piper_loaded.get(name)
        if voice is None:
            path = _piper_path(name)
            if not path.exists() or not Path(f"{path}.json").exists():
                raise EngineUnavailable(f"voix Piper « {name} » absente de {PIPER_DIR}")
            try:
                from piper import PiperVoice
            except ImportError as e:
                raise EngineUnavailable("module piper-tts absent (pip install piper-tts)") from e
            voice = PiperVoice.load(str(path))
            _piper_loaded.clear()
            _piper_loaded[name] = voice
        return voice


def _piper_release_later():
    global _piper_timer
    if _piper_timer:
        _piper_timer.cancel()
    _piper_timer = threading.Timer(PIPER_IDLE, _piper_loaded.clear)
    _piper_timer.daemon = True
    _piper_timer.start()


def synth_piper(text: str, voice: str, speed: float) -> Audio:
    model = _piper_voice(voice)
    from piper import SynthesisConfig
    config = SynthesisConfig(length_scale=(model.config.length_scale or 1.0) / speed)
    pcm = b"".join(chunk.audio_int16_bytes for chunk in model.synthesize(text, config))
    return Audio(pcm, model.config.sample_rate)


_sapi_local = threading.local()


def _sapi_voice(voice_id: str):
    """SpVoice du thread courant (COM initialisé), réglé sur la voix voulue."""
    import comtypes.client
    sp = getattr(_sapi_local, "voice", None)
    if sp is None:
        sp = _sapi_local.voice = comtypes.client.CreateObject("SAPI.SpVoice")
        _sapi_local.id = ""
    if voice_id != _sapi_local.id:
        for token in sp.GetVoices():
            if token.Id == voice_id:
                sp.Voice = token
                break
        _sapi_local.id = voice_id
    return sp


def synth_sapi(text: str, voice: str, speed: float) -> Audio:
    import comtypes.client
    try:
        sp = _sapi_voice(voice or "")
        stream = comtypes.client.CreateObject("SAPI.SpMemoryStream")
        fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
        fmt.Type = SAPI_FORMAT
        stream.Format = fmt
        sp.AudioOutputStream = stream
        sp.Rate = max(-10, min(10, round(10 * math.log(speed, 3))))     # ±10 : vitesse ×3 ou ÷3
        sp.Speak(text, SVSF_IS_NOT_XML)
        return Audio(bytes(stream.GetData()), SAPI_RATE)
    except Exception as e:
        raise EngineUnavailable(_short_error(e)) from e


SYNTH = {"edge": synth_edge, "piper": synth_piper, "sapi": synth_sapi}


# ─────────────────────────────────────────────
#  Voix disponibles
# ─────────────────────────────────────────────
def _module_present(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def piper_voices() -> list[dict]:
    quality = {"x_low": "très légère", "low": "légère", "medium": "moyenne", "high": "haute"}
    out = []
    for onnx in sorted(PIPER_DIR.glob("*.onnx")) if PIPER_DIR.is_dir() else []:
        if not Path(f"{onnx}.json").exists():
            continue
        parts = onnx.stem.split("-")              # fr_FR-siwis-medium
        lang = parts[0] if parts else ""
        name = "-".join(parts[1:-1]) if len(parts) > 2 else onnx.stem
        q = quality.get(parts[-1], parts[-1]) if len(parts) > 2 else ""
        out.append({"id": onnx.stem, "label": name[:1].upper() + name[1:] if name.islower() else name,
                    "hint": ", ".join(x for x in (lang.replace("_", "-"), f"qualité {q}" if q else "") if x)})
    return out


def sapi_voices() -> list[dict]:
    import comtypes.client
    from wasapi import com_thread
    out = []
    try:
        with com_thread():
            sp = comtypes.client.CreateObject("SAPI.SpVoice")
            for token in sp.GetVoices():
                name = re.sub(r"^Microsoft\s+|\s+Desktop$", "", token.GetDescription().partition(" - ")[0])
                hint = []
                try:          # « 40C » (LCID, plusieurs possibles séparés par « ; ») → fr-FR
                    lcid = int(token.GetAttribute("Language").split(";")[0], 16)
                    hint.append(locale.windows_locale[lcid].replace("_", "-"))
                except Exception:
                    pass
                try:
                    hint.append({"Female": "femme", "Male": "homme"}[token.GetAttribute("Gender")])
                except Exception:
                    pass
                out.append({"id": token.Id, "label": name, "hint": ", ".join(hint)})
    except Exception:
        pass
    return out


def list_voices() -> dict:
    """Moteurs et voix proposés dans les Paramètres."""
    edge = [{"id": v, "label": name, "hint": f"{region}, {'femme' if g == 'F' else 'homme'}"}
            for v, name, region, g in EDGE_VOICES]
    voices = {
        "edge": edge if _module_present("edge_tts") else [],
        "piper": piper_voices() if _module_present("piper") else [],
        "sapi": sapi_voices(),
    }
    return {"engines": [{"id": e, "label": ENGINE_LABELS[e], "available": bool(voices[e])} for e in ENGINES],
            "voices": voices, "speeds": list(SPEEDS)}


def warm_up(engine: str):
    """Modules du moteur importés d'avance (la première lecture démarre plus vite)."""
    try:
        if engine == "edge":
            import av, edge_tts  # noqa: F401
            _edge_setup()
        elif engine == "piper":
            import piper  # noqa: F401
    except Exception:
        pass


def voice_label(engine: str, voice: str) -> str:
    if engine == "edge":
        return next((name for v, name, *_ in EDGE_VOICES if v == voice), voice.split("-")[-1].removesuffix("Neural"))
    if engine == "piper":
        name = "-".join(voice.split("-")[1:-1]) or voice
        return name[:1].upper() + name[1:]
    if engine == "sapi":         # …\Tokens\TTS_MS_FR-FR_HORTENSE_11.0
        m = re.search(r"TTS_MS_[A-Z]{2}-[A-Z]{2}_([A-Z]+)_", voice or "")
        return m.group(1).title() if m else (voice.rsplit("\\", 1)[-1] if voice else "Windows")
    return voice


# ─────────────────────────────────────────────
#  Lecture
# ─────────────────────────────────────────────
class _AudioCache:
    def __init__(self, limit: int):
        self._items: OrderedDict = OrderedDict()
        self._size = 0
        self._limit = limit
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            audio = self._items.get(key)
            if audio is not None:
                self._items.move_to_end(key)
            return audio

    def put(self, key, audio: Audio):
        with self._lock:
            if key in self._items:
                return
            self._items[key] = audio
            self._size += len(audio.pcm)
            while self._size > self._limit and len(self._items) > 1:
                _key, old = self._items.popitem(last=False)
                self._size -= len(old.pcm)


class _Session:
    """Une lecture : un thread synthétise les phrases à venir, un autre les joue."""

    def __init__(self, owner: "TTSEngine", chunks, source, on_done, engines, voice):
        self.owner = owner
        self.chunks = chunks
        self.source = source
        self.on_done = on_done
        self.engines = engines            # ordre de repli, le premier sert
        self.first_engine = engines[0]
        self.voice = voice                # voix imposée au premier moteur (essai d'une voix)
        self.speed = owner.speed
        self.cond = threading.Condition()
        self.index = 0
        self.seek = 0                     # incrémenté à chaque saut : le lecteur vide son tampon
        self.paused = False
        self.stopped = False
        self.over = False                 # lecteur terminé : le synthétiseur s'arrête aussi
        self.failed = ""
        self.waiting = True               # phrase courante pas encore prête
        self.ready: dict[int, tuple[Audio, str, str]] = {}
        self.playing = ("", "")           # (moteur, voix) de la phrase jouée
        self.played = 0.0                 # secondes jouées de la phrase en cours
        self._threads = [threading.Thread(target=self._synth_loop, daemon=True, name="tts-synth"),
                         threading.Thread(target=self._play_loop, daemon=True, name="tts-play")]

    def start(self):
        for t in self._threads:
            t.start()

    def stop(self):
        with self.cond:
            self.stopped = True
            self.cond.notify_all()

    def join(self, timeout: float):
        if threading.current_thread() is not self._threads[1]:
            self._threads[1].join(timeout)

    # ── Synthèse ───────────────────────────
    def _synth_loop(self):
        from wasapi import com_thread
        with com_thread():                # SAPI
            while True:
                with self.cond:
                    while True:
                        if self.stopped or self.over or self.failed:
                            return
                        # (fin du texte : on attend, un retour en arrière reste possible)
                        end = min(self.index + AHEAD + 1, len(self.chunks))
                        i = next((k for k in range(self.index, end) if k not in self.ready), None)
                        if i is not None:
                            break
                        self.cond.wait()
                    speed = self.speed
                result = self._synth(i, speed)
                with self.cond:
                    if result is None:
                        self.cond.notify_all()
                        return
                    if speed == self.speed:
                        self.ready[i] = result
                        for k in [k for k in self.ready if k < self.index - 1]:
                            del self.ready[k]
                        self.cond.notify_all()

    def _synth(self, i: int, speed: float):
        text, pause = self.chunks[i]
        while self.engines and not self.stopped:
            engine = self.engines[0]
            voice = self.owner.voice_for(engine, self.voice if engine == self.first_engine else "")
            key = (engine, voice, round(speed, 2), text)
            audio = self.owner._cache.get(key)
            if audio is None:
                try:
                    audio = SYNTH[engine](text, voice, speed)
                except EngineUnavailable as e:
                    with self.cond:
                        if self.engines and self.engines[0] == engine:
                            self.engines.pop(0)
                    self.owner._engine_failed(self, engine, str(e))
                    continue
                except Exception as e:
                    self.owner._logger.log(f"Lecture : phrase sautée ({VOICE_NAMES[engine]}, {_short_error(e)})")
                    return Audio(b"", EDGE_RATE), engine, voice
                self.owner._cache.put(key, audio)
            silence = bytes(int(pause * audio.rate) * 2) if audio.pcm else b""
            return Audio(audio.pcm + silence, audio.rate), engine, voice
        if not self.stopped:
            with self.cond:
                self.failed = "aucune voix disponible"
        return None

    # ── Lecture ────────────────────────────
    def _play_loop(self):
        import comtypes
        import wasapi
        stream = None
        try:
            with wasapi.com_thread():
                while True:
                    announce = False
                    with self.cond:
                        while not (self.stopped or self.failed or self.index >= len(self.chunks)
                                   or self.index in self.ready):
                            if not self.waiting:
                                self.waiting = announce = True
                                break
                            self.cond.wait(0.25)
                    if announce:
                        self.owner._emit()
                        continue
                    with self.cond:
                        if self.stopped or self.failed:
                            break
                        i, seek = self.index, self.seek
                        if i >= len(self.chunks):
                            item = None
                        else:
                            item = self.ready[i]
                            self.waiting = False
                            self.playing = item[1:]
                            self.played = 0.0
                    if item is None:              # fin du texte : laisser finir le tampon
                        if stream is None or self._drain(stream, seek) == "done":
                            break
                        continue
                    self.owner._emit()
                    try:
                        if item[0].pcm:
                            if stream is not None and stream.rate != item[0].rate:   # repli sur un autre moteur
                                self._drain(stream, seek)
                                stream.close()
                                stream = None
                            if stream is None:
                                stream = wasapi.RenderStream(wasapi.get_device(wasapi.E_RENDER)[0], item[0].rate)
                            outcome = self._play(stream, item[0].pcm, seek)
                        else:
                            outcome = "done"      # rien à dire (phrase sautée)
                    except comtypes.COMError as e:
                        if not wasapi.is_invalidated(e):
                            raise
                        try:
                            stream.close()
                        except Exception:
                            pass
                        stream = None             # sortie débranchée : on reprend la phrase sur l'autre
                        time.sleep(0.2)
                        continue
                    if outcome == "stop":
                        break
                    if outcome == "done":
                        with self.cond:
                            if self.index == i and self.seek == seek:
                                self.index += 1
                                self.cond.notify_all()
        except Exception as e:
            self.owner._logger.log(f"Lecture interrompue : {_short_error(e)}")
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            with self.cond:
                self.over = True
                self.cond.notify_all()
            self.owner._session_ended(self)

    def _interrupted(self, stream, seek: int) -> str:
        """« stop », « seek » ou « » ; gère la pause (attend la reprise)."""
        with self.cond:
            if self.paused and not self.stopped and self.seek == seek:
                stream.pause()
                while self.paused and not self.stopped and self.seek == seek:
                    self.cond.wait()
                if not self.stopped and self.seek == seek:
                    stream.resume()
            if self.stopped:
                stream.flush()
                return "stop"
            if self.seek != seek:
                stream.flush()
                return "seek"
        return ""

    def _play(self, stream, pcm: bytes, seek: int) -> str:
        step = stream.rate * stream.frame // 4    # 250 ms par écriture au plus
        off = 0
        while True:
            outcome = self._interrupted(stream, seek)
            if outcome:
                return outcome
            if off >= len(pcm):
                return "done"
            off += stream.write(pcm[off:off + step])
            self.played = max(0.0, off / stream.frame / stream.rate - stream.pending())
            time.sleep(0.01)

    def _drain(self, stream, seek: int) -> str:
        while stream.pending() > 0:
            outcome = self._interrupted(stream, seek)
            if outcome:
                return outcome
            time.sleep(0.02)
        return "done"


class TTSEngine:
    """Lecture à voix haute. speak() arrête la lecture précédente avant d'en
    démarrer une nouvelle. Thread-safe."""

    def __init__(self, logger):
        self._logger = logger
        self._lock = threading.Lock()
        self._listeners: list = []
        self._session: _Session | None = None
        self._cache = _AudioCache(CACHE_BYTES)
        self._down: dict[str, float] = {}      # moteur → pas retenté avant cet instant
        self.engine = "edge"
        self.voices = dict(DEFAULT_VOICES)
        self.speed = 1.0

    def configure(self, engine: str | None = None, voices: dict | None = None, speed: float | None = None):
        if engine in ENGINES:
            self.engine = engine
            self._down.pop(engine, None)
        if voices:
            self.voices.update({k: v for k, v in voices.items() if k in ENGINES and isinstance(v, str)})
        if speed is not None:
            self.set_speed(speed)

    def subscribe(self, listener):
        """listener(state: dict) à chaque changement (début, phrase, pause, fin)."""
        self._listeners.append(listener)

    def voice_for(self, engine: str, override: str = "") -> str:
        voice = override or self.voices.get(engine) or DEFAULT_VOICES[engine]
        if engine == "piper" and not _piper_path(voice).exists():
            voice = next((v["id"] for v in piper_voices()), voice)
        return voice

    # ── Commandes ──────────────────────────
    def speak(self, text: str, on_done=None, source: str | None = None,
              engine: str | None = None, voice: str = ""):
        """Lit text à haute voix. engine / voice : pour essayer une voix sans la
        choisir (sinon, réglages de configure())."""
        with self._lock:
            old, self._session = self._session, None
        if old:
            old.stop()
            old.join(1.0)
        if _piper_timer:
            _piper_timer.cancel()
        chunks = split_text(text)
        if not chunks:
            if old:
                self._emit()
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
            return
        session = _Session(self, chunks, source, on_done, self._engines(engine or self.engine), voice)
        with self._lock:
            self._session = session
        self._emit()
        session.start()

    def stop(self):
        """Arrête la lecture en cours."""
        session = self._session
        if session:
            session.stop()
            session.join(1.0)

    def pause(self, paused: bool | None = None):
        """Met en pause ou reprend (bascule si paused vaut None)."""
        session = self._session
        if not session:
            return
        with session.cond:
            session.paused = (not session.paused) if paused is None else bool(paused)
            session.cond.notify_all()
        self._emit()

    def skip(self, delta: int):
        """Phrase suivante (+1) ou précédente (-1 : reprend la phrase en cours si
        elle est entamée depuis plus de RESTART_AFTER secondes)."""
        session = self._session
        if not session:
            return
        with session.cond:
            target = session.index + delta
            if delta == -1 and session.played > RESTART_AFTER:
                target = session.index
            session.index = max(0, min(target, len(session.chunks)))
            session.seek += 1
            session.played = 0.0
            session.cond.notify_all()
        self._emit()

    def set_speed(self, speed: float):
        try:
            speed = round(max(MIN_SPEED, min(float(speed), MAX_SPEED)), 2)
        except (TypeError, ValueError):
            return
        self.speed = speed
        session = self._session
        if session and session.speed != speed:
            with session.cond:
                session.speed = speed
                # La phrase en cours finit à l'ancienne vitesse, les suivantes sont refaites
                for k in [k for k in session.ready if k > session.index]:
                    del session.ready[k]
                session.cond.notify_all()
            self._emit()

    # ── État ───────────────────────────────
    @property
    def speaking(self) -> bool:
        return self._session is not None

    def state(self) -> dict:
        session = self._session
        if not session:
            return {"speaking": False, "source": None, "speed": self.speed}
        n = len(session.chunks)
        engine, voice = session.playing if session.playing[0] else (
            session.engines[0] if session.engines else self.engine, "")
        voice = voice or self.voice_for(engine, session.voice if engine == session.first_engine else "")
        return {
            "speaking": True, "source": session.source, "paused": session.paused,
            "waiting": session.waiting, "index": min(session.index, n - 1), "total": n,
            "text": session.chunks[min(session.index, n - 1)][0], "speed": session.speed,
            "engine": engine, "voice": voice_label(engine, voice),
        }

    # ── Interne ────────────────────────────
    def _engines(self, first: str) -> list[str]:
        if first not in ENGINES:
            first = ENGINES[0]
        order = list(ENGINES[ENGINES.index(first):])
        now = time.monotonic()
        usable = [e for e in order if self._down.get(e, 0) <= now]
        return usable or order[-1:]

    def _emit(self, notice: str = "", error: bool = False):
        state = self.state()
        if notice:
            state["notice"] = notice
            state["notice_kind"] = "error" if error else "info"
        for fn in list(self._listeners):
            try:
                fn(state)
            except Exception:
                pass

    def _engine_failed(self, session: _Session, engine: str, error: str):
        if engine == "edge":
            self._down[engine] = time.monotonic() + EDGE_RETRY_AFTER
        nxt = session.engines[0] if session.engines else None
        self._logger.log(f"Lecture : {VOICE_NAMES[engine]} indisponible ({error})"
                         + (f", repli sur la {VOICE_NAMES[nxt]}." if nxt else "."))
        if session is self._session:
            if not nxt:
                notice = "Lecture impossible : aucune voix ne répond."
            elif engine == "edge":
                notice = "Voix naturelle injoignable (connexion ?) : lecture avec la voix hors ligne."
            else:
                notice = f"{VOICE_NAMES[engine].capitalize()} indisponible : lecture avec la {VOICE_NAMES[nxt]}."
            self._emit(notice, error=not nxt)

    def _session_ended(self, session: _Session):
        with self._lock:
            current = self._session is session
            if current:
                self._session = None
        if current:
            self._emit()
        if _piper_loaded:
            _piper_release_later()
        if session.on_done:
            try:
                session.on_done()
            except Exception:
                pass
