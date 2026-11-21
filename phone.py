"""
phone.py – Section Smartphone : l'écran d'un téléphone Android branché en
débogage USB, affiché dans Boostache et piloté à la souris et au clavier.

- Présence : une connexion au serveur adb (host:track-devices-l) reçoit la
  liste des appareils à chaque branchement ou débranchement, sans
  interrogation répétée. La section n'existe que tant qu'un téléphone est
  branché ; les émulateurs sont ignorés (ils ont leur propre fenêtre).
- Écran et commandes : scrcpy-server (vendor/scrcpy, Genymobile, Apache 2.0)
  poussé sur le téléphone et lancé par adb. La vidéo H.264 arrive par un
  tunnel adb (adb forward) et part telle quelle vers la page (message
  WebView2, sans passer par bridge.py), qui la décode (WebCodecs) ; le
  toucher, le clavier et le presse-papiers passent par la seconde connexion.
- Options de la section (réglages phone_screen_off, phone_keep_awake) : écran
  du téléphone éteint pendant l'affichage (l'image continue d'arriver), téléphone
  gardé éveillé (keep_active du serveur : activité signalée toutes les 4 s, il ne
  se met pas en veille et ne se verrouille donc pas tant que la section est affichée).
- Capture d'écran : celle du téléphone en pleine résolution (adb screencap), ouverte
  dans Captures ; noire (écran éteint par l'option, application protégée) ou
  impossible : l'image affichée dans la page.
- Capture défilante : la page est poussée vers le haut d'un glissement de doigt (arrêté
  net avant d'être levé, sans élan), capturée de nouveau, et chaque capture est raccordée
  à la précédente par les lignes qu'elles ont en commun, jusqu'en bas de la page (rien
  n'a bougé), à l'arrêt demandé ou à la hauteur maximale de Captures.
- Copier-coller : ce qui est copié sur le téléphone arrive dans le
  presse-papiers de Windows (clipboard_autosync du serveur) ; ce qui est
  copié dans Windows pendant que l'écran est affiché part dans celui du
  téléphone (appui long → Coller) ; Ctrl+V sur l'écran colle le
  presse-papiers de Windows, Ctrl+C / Ctrl+X copient la sélection du téléphone.

Le protocole est celui de scrcpy 5.0.1 (doc/develop.md, ControlMessageReader,
DeviceMessageWriter, Streamer) : il change d'une version du serveur à l'autre.
"""

import base64
import io
import os
import queue
import random
import shutil
import socket
import struct
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from engine import logger
from storage import settings

SCRCPY_VERSION = "5.0.1"
SERVER_FILE = Path(__file__).resolve().parent / "vendor" / "scrcpy" / f"scrcpy-server-v{SCRCPY_VERSION}"
DEVICE_PATH = "/data/local/tmp/boostache-scrcpy-server.jar"
ADB_HOST = ("127.0.0.1", 5037)
MAX_SIZE = 1920                  # plus grand côté de la vidéo (px)
BIT_RATE = 8_000_000
MAX_FPS = 60
CONNECT_TIMEOUT = 20.0           # s pour que le serveur réponde après son lancement
MAX_INFLIGHT = 24                # paquets vidéo en attente du thread de l'interface
ECHO_DELAY = 10.0                # s : presse-papiers renvoyé par le téléphone après l'avoir reçu
PASTE_PAUSE = 0.4                # s laissées à l'application pour coller avant le message suivant
RESTORE_DELAY = 1.0              # s : presse-papiers du téléphone rétabli après des caractères collés
CREATE_NO_WINDOW = 0x08000000
SCREENCAP_TIMEOUT = 20           # s pour la capture d'écran du téléphone
# Capture défilante
SWIPE_FROM, SWIPE_TO = 0.70, 0.28    # glissement vers le haut (fractions de la hauteur de l'écran)
SWIPE_STEPS = 30                     # déplacements du doigt, de plus en plus courts…
SWIPE_STEP = 0.016                   # … toutes les 16 ms
SWIPE_HOLD = 0.25                    # s doigt immobile avant d'être levé : pas d'élan, la page s'arrête net
SETTLE = 0.3                         # s laissées à la page après le glissement avant la capture
RETRY_DELAY = 0.5                    # s avant une nouvelle capture quand le raccord échoue (rebond, chargement)
LONG_MAX_SCREENS = 40
FINGER = -2                          # POINTER_ID_GENERIC_FINGER de scrcpy : un doigt, pas la souris
SIG_COLS = 96                        # colonnes de la signature d'une ligne (moyennes par bloc)
SIG_RIGHT = 0.03                     # bord droit ignoré : barre de défilement affichée pendant le défilement
ROW_TOL = 3.0                        # écart moyen (niveaux de gris) sous lequel deux lignes sont les mêmes
ROW_INFO = 12.0                      # écart entre les blocs d'une ligne qui a du contenu (pas un fond uni)
MATCH_GOOD = 0.15                    # lignes différentes tolérées au raccord (heure, animation…)
MATCH_FAIR = 0.5                     # … quand aucun autre décalage n'en approche (image qui finit de se charger)
OVERLAY_FROM = 0.65                  # ce qui est posé sur la page en bas est cherché sous cette hauteur
                                     # (plus haut, la suite prise dans la capture suivante remonterait
                                     # jusqu'à l'en-tête de l'application)…
OVERLAY_ROWS = 3                     # … sur au moins ces lignes à la suite
# Pour les essais : émulateurs affichés comme des téléphones
INCLUDE_EMULATORS = os.environ.get("BOOSTACHE_PHONE_EMULATORS") == "1"

# ── Protocole scrcpy (5.0.1) ───────────────────────────────────────────────
CODEC_H264 = 0x68323634
FLAG_SESSION = 1 << 63
FLAG_CONFIG = 1 << 62
FLAG_KEY = 1 << 61

T_KEYCODE, T_TEXT, T_TOUCH, T_SCROLL, T_BACK_OR_ON = 0, 1, 2, 3, 4
T_NOTIFICATIONS, T_SETTINGS, T_COLLAPSE = 5, 6, 7
T_GET_CLIPBOARD, T_SET_CLIPBOARD, T_DISPLAY_POWER, T_ROTATE = 8, 9, 10, 11
T_RESET_VIDEO = 17

D_CLIPBOARD, D_ACK_CLIPBOARD, D_UHID_OUTPUT = 0, 1, 2

TEXT_MAX = 300                       # octets UTF-8 par message INJECT_TEXT
# Tapés par INJECT_TEXT : ASCII et minuscules accentuées, que le serveur décompose en touches
# mortes (KeyComposition). Les autres (majuscules accentuées : accent en trop ; œ, €, « », ’…
# ignorés) sont collés par le presse-papiers du téléphone, rétabli ensuite.
TYPABLE = frozenset(map(chr, range(0x20, 0x7F))) | frozenset("\n\tàáâãäèéêëìíîïñòóôõöùúûüýÿçÇ")
CLIPBOARD_MAX = (1 << 18) - 14       # octets UTF-8 par message SET_CLIPBOARD
KEY_DOWN, KEY_UP = 0, 1
TOUCH_DOWN, TOUCH_UP, TOUCH_MOVE = 0, 1, 2
BUTTON_PRIMARY = 1
POINTER_MOUSE = -1
COPY_KEY_COPY, COPY_KEY_CUT = 1, 2

# Touches Android (KeyEvent.KEYCODE_*) des boutons de la section
KEYS = {"back": 4, "home": 3, "recents": 187, "power": 26, "volume_up": 24, "volume_down": 25}
PANELS = {"notifications": T_NOTIFICATIONS, "settings": T_SETTINGS, "collapse": T_COLLAPSE}


class SessionError(Exception):
    """Échec de la connexion au téléphone (message pour l'interface)."""


# ─────────────────────────────────────────────
#  Messages de commande (client → téléphone)
# ─────────────────────────────────────────────
def _utf8_cut(text: str, limit: int) -> bytes:
    """Texte en UTF-8, coupé à limit octets sans couper de caractère."""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return raw
    cut = limit
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut]


def msg_keycode(action: int, keycode: int, repeat: int = 0, meta: int = 0) -> bytes:
    return struct.pack(">BBiii", T_KEYCODE, action, keycode, repeat, meta)


def msg_press(keycode: int, meta: int = 0) -> bytes:
    return msg_keycode(KEY_DOWN, keycode, 0, meta) + msg_keycode(KEY_UP, keycode, 0, meta)


def split_typable(text: str) -> list[tuple[bool, str]]:
    """Suites de caractères tapables (True) ou à coller (False), dans l'ordre."""
    runs: list[tuple[bool, str]] = []
    for ch in text:
        typable = ch in TYPABLE
        if runs and runs[-1][0] == typable:
            runs[-1] = (typable, runs[-1][1] + ch)
        else:
            runs.append((typable, ch))
    return runs


def msg_text(text: str) -> list[bytes]:
    """Texte tapé, en messages de TEXT_MAX octets au plus."""
    out = []
    while text:
        raw = _utf8_cut(text, TEXT_MAX)
        if not raw:
            break
        out.append(struct.pack(">BI", T_TEXT, len(raw)) + raw)
        text = text[len(raw.decode("utf-8", "replace")):]
    return out


def msg_touch(action: int, pointer_id: int, x: int, y: int, width: int, height: int) -> bytes:
    """Doigt posé, déplacé ou levé (bouton principal de la souris : un doigt).
    width × height : taille de la vidéo à laquelle x, y se rapportent."""
    up = action == TOUCH_UP
    pressure = 0 if up else 0xFFFF
    action_button = BUTTON_PRIMARY if action in (TOUCH_DOWN, TOUCH_UP) else 0
    buttons = 0 if up else BUTTON_PRIMARY
    return struct.pack(">BBqiiHHHii", T_TOUCH, action, pointer_id, x, y, width, height,
                       pressure, action_button, buttons)


def _i16fp(value: float) -> int:
    """[-16, 16] (crans de molette) → virgule fixe signée sur 16 bits de [-1, 1]."""
    v = max(-1.0, min(value / 16, 1.0))
    return max(-0x8000, min(int(v * 0x8000), 0x7FFF))


def msg_scroll(x: int, y: int, width: int, height: int, hscroll: float, vscroll: float) -> bytes:
    return struct.pack(">BiiHHhhi", T_SCROLL, x, y, width, height, _i16fp(hscroll), _i16fp(vscroll), 0)


def msg_back_or_screen_on() -> bytes:
    return struct.pack(">BB", T_BACK_OR_ON, KEY_DOWN) + struct.pack(">BB", T_BACK_OR_ON, KEY_UP)


def msg_get_clipboard(copy_key: int) -> bytes:
    return struct.pack(">BB", T_GET_CLIPBOARD, copy_key)


def msg_set_clipboard(text: str, paste: bool, sequence: int = 0) -> bytes:
    raw = _utf8_cut(text, CLIPBOARD_MAX)
    return struct.pack(">BqBI", T_SET_CLIPBOARD, sequence, 1 if paste else 0, len(raw)) + raw


def msg_display_power(on: bool) -> bytes:
    return struct.pack(">BB", T_DISPLAY_POWER, 1 if on else 0)


def msg_empty(kind: int) -> bytes:
    return struct.pack(">B", kind)


# ─────────────────────────────────────────────
#  Liste des appareils (serveur adb)
# ─────────────────────────────────────────────
def find_adb() -> str | None:
    """adb du PATH (celui des outils Android de l'utilisateur, pour ne pas lancer
    un serveur adb d'une autre version qui arrêterait le sien), sinon celui du SDK."""
    found = shutil.which("adb")
    if found:
        return found
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk")]
    for root in roots:
        if root and (Path(root) / "platform-tools" / "adb.exe").is_file():
            return str(Path(root) / "platform-tools" / "adb.exe")
    return None


def parse_devices(text: str) -> list[dict]:
    """Réponse de host:track-devices-l : une ligne par appareil,
    « numéro   état product:… model:… device:… transport_id:… »."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        out.append({
            "serial": serial,
            "state": state,
            "model": props.get("model", "").replace("_", " "),
            "emulator": serial.startswith("emulator-"),
        })
    return out


def device_name(manufacturer: str, model: str, market: str = "") -> str:
    """« Pixel 7 » → « Google Pixel 7 », « SM-S918B » → « Samsung SM-S918B » ;
    nom commercial s'il est connu."""
    market, model, manufacturer = market.strip(), model.strip(), manufacturer.strip()
    if market:
        return market
    if not model:
        return manufacturer.capitalize() or "Téléphone"
    if not manufacturer or manufacturer.lower() in model.lower():
        return model
    return f"{manufacturer[:1].upper()}{manufacturer[1:]} {model}"


def _recv_exact(sock, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if not k:
            raise EOFError("connexion fermée")
        got += k
    return bytes(buf)


class DeviceTracker:
    """Connexion au serveur adb : on_change(appareils) à chaque changement (liste vide
    quand le serveur ne répond plus). Le serveur est lancé s'il ne tourne pas."""

    def __init__(self, adb: str, on_change):
        self.adb = adb
        self.on_change = on_change
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="adb-devices")
        self._thread.start()

    def stop(self):
        self._stop.set()
        sock = self._sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _run(self):
        delay = 1.0
        started_server = False
        while not self._stop.is_set():
            try:
                sock = self._connect(start_server=not started_server)
                started_server = True
                delay = 1.0
                self._read(sock)
            except (OSError, EOFError, ValueError) as e:
                if not self._stop.is_set():
                    logger.debug(f"Smartphone : serveur adb injoignable ({e})")
            finally:
                if self._sock:
                    self._sock.close()
                    self._sock = None
            if self._stop.is_set():
                return
            self.on_change([])
            # Serveur arrêté (adb kill-server…) : relancé, de moins en moins souvent
            self._stop.wait(delay)
            delay = min(delay * 2, 30.0)
            started_server = False

    def _connect(self, start_server: bool) -> socket.socket:
        try:
            sock = socket.create_connection(ADB_HOST, timeout=3)
        except OSError:
            if not start_server:
                raise
            subprocess.run([self.adb, "start-server"], capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
            sock = socket.create_connection(ADB_HOST, timeout=3)
        self._sock = sock
        if self._stop.is_set():
            raise OSError("arrêt")
        cmd = b"host:track-devices-l"
        sock.sendall(b"%04x" % len(cmd) + cmd)
        status = _recv_exact(sock, 4)
        if status != b"OKAY":
            raise OSError(f"adb a répondu {status!r}")
        sock.settimeout(None)       # attente des changements : sans limite
        return sock

    def _read(self, sock):
        while not self._stop.is_set():
            size = int(_recv_exact(sock, 4), 16)
            payload = _recv_exact(sock, size).decode("utf-8", "replace") if size else ""
            self.on_change(parse_devices(payload))


# ─────────────────────────────────────────────
#  Session : écran et commandes d'un téléphone
# ─────────────────────────────────────────────
class Mirror:
    """Serveur scrcpy lancé sur le téléphone, vidéo et commandes. Les événements
    (état, presse-papiers) passent par on_event(kind, data) ; la vidéo par
    post(texte JSON) vers la page."""

    def __init__(self, adb: str, serial: str, sid: int, post, on_event):
        self.adb = adb
        self.serial = serial
        self.sid = sid
        self.post = post
        self.on_event = on_event
        self.state = "connecting"
        self.name = ""
        self.size = None                  # (largeur, hauteur) de la vidéo
        self.error = ""
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._video: socket.socket | None = None
        self._control: socket.socket | None = None
        self._port = 0
        self._outq: queue.Queue = queue.Queue()
        self._server_log: deque[str] = deque(maxlen=40)
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._need_key = False
        self.phone_text: str | None = None       # dernier texte copié sur le téléphone
        self.clip_known: str | None = None       # contenu connu de son presse-papiers
        self._sent: deque[tuple[str, float]] = deque(maxlen=32)   # textes mis dans son presse-papiers
        self._restore_to: str | None = None      # à rétablir après des caractères collés
        self._restore_timer: threading.Timer | None = None
        self._clip_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="phone-video")

    def start(self):
        self._thread.start()

    def info(self) -> dict:
        return {"sid": self.sid, "serial": self.serial, "state": self.state, "name": self.name,
                "size": list(self.size) if self.size else None, "error": self.error}

    # ── Commandes ──
    def send(self, data: bytes | float | threading.Event):
        """Message de commande, pause (secondes) avant le suivant, ou Event levé une fois
        que tout ce qui le précède est parti."""
        if data and not self._stop.is_set():
            self._outq.put(data)

    def set_clipboard(self, text: str, paste: bool):
        """Presse-papiers du téléphone (et collage dans le champ actif si paste)."""
        with self._clip_lock:
            self.clip_known = text
            self._restore_to = None          # remplacé : plus rien à rétablir
            self._sent.append((text, time.monotonic()))
        self.send(msg_set_clipboard(text, paste))
        if paste:
            self.send(PASTE_PAUSE)

    def received_clipboard(self, text: str) -> bool:
        """Presse-papiers signalé par le téléphone : faux si c'est l'écho de ce qu'on vient
        de lui donner (son écouteur est appelé après coup), sinon une copie faite dessus."""
        now = time.monotonic()
        with self._clip_lock:
            if any(t == text and now - at < ECHO_DELAY for t, at in self._sent):
                return False
            self.phone_text = self.clip_known = text
            self._restore_to = None
            return True

    def type_text(self, text: str):
        """Texte tapé : les caractères que le téléphone ne sait pas taper sont collés."""
        for typable, run in split_typable(text):
            if typable:
                for raw in msg_text(run):
                    self.send(raw)
                continue
            with self._clip_lock:
                if self._restore_timer is None:
                    self._restore_to = self.clip_known
                else:
                    self._restore_timer.cancel()
                self._sent.append((run, time.monotonic()))
                timer = threading.Timer(RESTORE_DELAY, self._restore_clipboard)
                timer.daemon = True
                self._restore_timer = timer
            self.send(msg_set_clipboard(run, paste=True))
            self.send(PASTE_PAUSE)
            timer.start()

    def _restore_clipboard(self):
        with self._clip_lock:
            self._restore_timer = None
            text, self._restore_to = self._restore_to, None
        if text is not None and not self._stop.is_set():
            self.set_clipboard(text, paste=False)

    def stop(self):
        """Ferme les connexions : le serveur du téléphone s'arrête de lui-même."""
        if self._stop.is_set():
            return
        self._stop.set()
        self._outq.put(None)
        for sock in (self._video, self._control):
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def join(self, timeout: float):
        self._thread.join(timeout)

    # ── Démarrage ──
    def _adb(self, *args, timeout=30) -> subprocess.CompletedProcess:
        return subprocess.run([self.adb, "-s", self.serial, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)

    def _run(self):
        try:
            self._connect()
            if self._stop.is_set():
                return
            self.state = "running"
            if settings.get("phone_screen_off", False):
                # Traité après l'allumage de l'écran par le serveur au démarrage
                self.send(msg_display_power(False))
            self._emit()
            threading.Thread(target=self._send_loop, daemon=True, name="phone-control-out").start()
            threading.Thread(target=self._device_messages, daemon=True, name="phone-control-in").start()
            self._video_loop()
        except SessionError as e:
            self.error = str(e)
        except (OSError, EOFError, subprocess.SubprocessError, ValueError, struct.error) as e:
            if not self._stop.is_set():
                self.error = self._server_error() or ("Connexion au téléphone perdue." if self.state == "running"
                                                      else f"Connexion au téléphone impossible ({e}).")
        except Exception as e:
            logger.log(f"Smartphone : erreur inattendue ({e})")
            self.error = f"Erreur inattendue : {e}"
        finally:
            self._close()
            if self.error:
                logger.warning(f"Smartphone : {self.error}", details="\n".join(self._server_log) or None)
            self.state = "stopped"
            self._emit()

    def _connect(self):
        if not SERVER_FILE.is_file():
            raise SessionError("Fichier scrcpy-server introuvable (dossier vendor/scrcpy).")
        res = self._adb("push", str(SERVER_FILE), DEVICE_PATH, timeout=60)
        if res.returncode != 0:
            raise SessionError(f"Envoi de scrcpy au téléphone impossible : "
                               f"{(res.stderr or res.stdout).strip() or res.returncode}")
        scid = random.randrange(1, 0x7FFFFFFF)
        socket_name = f"scrcpy_{scid:08x}"
        res = self._adb("forward", "tcp:0", f"localabstract:{socket_name}", timeout=15)
        try:
            self._port = int(res.stdout.strip())
        except ValueError:
            raise SessionError(f"Tunnel adb impossible : {(res.stderr or res.stdout).strip()}") from None
        if self._stop.is_set():
            return
        self._proc = subprocess.Popen(self.server_command(scid), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._read_server_output, daemon=True, name="phone-server-log").start()

        # Tunnel « forward » : la connexion aboutit même si rien n'écoute encore sur le
        # téléphone ; le serveur envoie un octet sur la première dès qu'il l'a acceptée
        deadline = time.monotonic() + CONNECT_TIMEOUT
        while True:
            if self._stop.is_set():
                return
            if self._proc.poll() is not None:
                raise SessionError(self._server_error() or "Le serveur scrcpy s'est arrêté au démarrage.")
            sock = None
            try:
                sock = socket.create_connection(("127.0.0.1", self._port), timeout=2)
                if sock.recv(1) == b"\x00":
                    self._video = sock
                    break
            except OSError:
                pass
            if sock is not None:
                sock.close()
            if time.monotonic() > deadline:
                raise SessionError(self._server_error() or "Le téléphone ne répond pas (scrcpy n'a pas démarré).")
            time.sleep(0.15)
        self._control = socket.create_connection(("127.0.0.1", self._port), timeout=5)
        self._control.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._control.settimeout(None)
        self._video.settimeout(10)
        name = _recv_exact(self._video, 64).split(b"\0", 1)[0].decode("utf-8", "replace")
        self.name = name.strip()
        codec = struct.unpack(">I", _recv_exact(self._video, 4))[0]
        if codec in (0, 1):
            raise SessionError(self._server_error() or "Le téléphone n'a pas pu démarrer la vidéo.")
        if codec != CODEC_H264:
            raise SessionError(f"Format vidéo inattendu ({codec:#x}).")
        self._video.settimeout(None)

    def server_command(self, scid: int) -> list[str]:
        """Lancement du serveur sur le téléphone (options : voir Options.java de scrcpy)."""
        cmd = [self.adb, "-s", self.serial, "shell", f"CLASSPATH={DEVICE_PATH}", "app_process", "/",
               "com.genymobile.scrcpy.Server", SCRCPY_VERSION, f"scid={scid:08x}", "log_level=info",
               "audio=false", "tunnel_forward=true", f"max_size={MAX_SIZE}",
               f"video_bit_rate={BIT_RATE}", f"max_fps={MAX_FPS}", "clipboard_autosync=true"]
        if settings.get("phone_keep_awake", False):
            cmd.append("keep_active=true")      # activité signalée : ni veille ni verrouillage
        return cmd

    def _read_server_output(self):
        proc = self._proc
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            self._server_log.append(line)
            if "ERROR" in line or "WARN" in line:
                logger.debug(f"Smartphone (scrcpy) : {line}")

    def _server_error(self) -> str:
        """Dernière erreur écrite par le serveur (message pour l'interface)."""
        for line in reversed(self._server_log):
            if "ERROR" in line or "Exception" in line:
                text = line.split("ERROR:", 1)[-1].strip()
                return f"Erreur de scrcpy sur le téléphone : {text}"
        return ""

    # ── Vidéo ──
    def _video_loop(self):
        sock = self._video
        config = b""
        while not self._stop.is_set():
            header = _recv_exact(sock, 12)
            if header[0] & 0x80:
                # Nouvelle session de capture (rotation…) : taille de la vidéo
                width, height = struct.unpack(">II", header[4:])
                self.size = (width, height)
                self._post(f'{{"phone":{self.sid},"s":[{width},{height}]}}')
                self._emit()
                continue
            flags, size = struct.unpack(">QI", header)
            data = _recv_exact(sock, size)
            if flags & FLAG_CONFIG:
                config = data          # SPS/PPS : joints à chaque image clé
                continue
            key = bool(flags & FLAG_KEY)
            if key:
                data = config + data
            elif self._need_key or self._inflight > MAX_INFLIGHT:
                # Interface en retard : images sautées jusqu'à la prochaine image clé
                if not self._need_key:
                    self._need_key = True
                    self.send(msg_empty(T_RESET_VIDEO))
                continue
            self._need_key = False
            b64 = base64.b64encode(data).decode("ascii")
            self._post(f'{{"phone":{self.sid},"k":{1 if key else 0},"d":"{b64}"}}')

    def _post(self, text: str):
        with self._inflight_lock:
            self._inflight += 1
        if not self.post(text, self._posted):
            self._posted()

    def _posted(self):
        with self._inflight_lock:
            self._inflight -= 1

    # ── Commandes et presse-papiers ──
    def _send_loop(self):
        nothing = object()
        pending = nothing                 # élément lu après un lot de messages, traité ensuite
        while True:
            data = self._outq.get() if pending is nothing else pending
            pending = nothing
            if data is None:
                return
            if isinstance(data, float):
                time.sleep(data)
                continue
            if isinstance(data, threading.Event):
                data.set()
                continue
            # Messages à la suite : envoyés ensemble
            chunks = [data]
            while True:
                try:
                    more = self._outq.get_nowait()
                except queue.Empty:
                    break
                if not isinstance(more, bytes):
                    pending = more
                    break
                chunks.append(more)
            try:
                self._control.sendall(b"".join(chunks))
            except OSError:
                return

    def _device_messages(self):
        sock = self._control
        try:
            while not self._stop.is_set():
                kind = _recv_exact(sock, 1)[0]
                if kind == D_CLIPBOARD:
                    size = struct.unpack(">I", _recv_exact(sock, 4))[0]
                    text = _recv_exact(sock, size).decode("utf-8", "replace")
                    self.on_event("clipboard", {"text": text, "mirror": self})
                elif kind == D_ACK_CLIPBOARD:
                    _recv_exact(sock, 8)
                elif kind == D_UHID_OUTPUT:
                    _, size = struct.unpack(">HH", _recv_exact(sock, 4))
                    _recv_exact(sock, size)
                else:
                    logger.debug(f"Smartphone : message inconnu du téléphone ({kind})")
                    return
        except (OSError, EOFError):
            pass

    # ── Fin ──
    def _close(self):
        self._stop.set()
        self._outq.put(None)
        for sock in (self._video, self._control):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.wait(2)          # le serveur s'arrête quand ses connexions sont fermées
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._port:
            try:
                self._adb("forward", "--remove", f"tcp:{self._port}", timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass

    def _emit(self):
        self.on_event("state", self.info())


# ─────────────────────────────────────────────
#  Capture défilante : raccord des captures
# ─────────────────────────────────────────────
def row_signature(image):
    """Lignes de l'image en niveaux de gris, moyennées par blocs de colonnes
    (tableau hauteur × SIG_COLS), bord droit exclu."""
    import numpy as np
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    height, width = gray.shape
    usable = max(1, int(width * (1 - SIG_RIGHT)))
    cols = min(SIG_COLS, usable)
    block = usable // cols
    return gray[:, :block * cols].reshape(height, cols, block).mean(axis=2)


def _footer_start(prev, cur, d: int, default: int) -> int:
    """Début de ce qui ne défile pas en bas de l'écran pour un défilement de d : default
    (début des lignes inchangées : barre de navigation opaque), ou plus haut, ce sous quoi
    la page défile (trait des gestes, bouton flottant, barre translucide) : dans le bas de
    l'écran, des lignes de prev qui ne se retrouvent pas d plus haut dans cur (au moins
    OVERLAY_ROWS à la suite, pas une ligne isolée)."""
    import numpy as np
    height = len(prev)
    ys = np.arange(max(d, int(height * OVERLAY_FROM)), default)
    if ys.size < OVERLAY_ROWS:
        return default
    over = np.abs(prev[ys] - cur[ys - d]).mean(axis=1) > ROW_TOL
    runs = np.ones(ys.size - OVERLAY_ROWS + 1, bool)
    for k in range(OVERLAY_ROWS):
        runs &= over[k:ys.size - OVERLAY_ROWS + 1 + k]
    idx = np.flatnonzero(runs)
    return int(ys[idx[0]]) if idx.size else default


def match_scroll(prev, cur, expected: int = 0) -> tuple[int | None, int]:
    """Défilement entre deux captures (signatures de row_signature) : (d, bas), la ligne y
    de cur montrant ce que montrait la ligne y + d de prev, pour tout ce qui est au-dessus
    de bas (début de ce qui ne défile pas en bas de l'écran : barre de navigation, trait
    des gestes, boutons). d = 0 : rien n'a bougé ; None : pas de raccord. expected :
    décalage attendu, qui départage les raccords aussi bons (page presque vide, éléments
    qui se répètent)."""
    import numpy as np
    height = len(cur)
    same = np.abs(cur - prev).mean(axis=1) <= ROW_TOL
    changed = np.flatnonzero(~same)
    if changed.size == 0:
        return 0, height
    bottom = int(changed[-1]) + 1
    # Lignes qui ont du contenu et ont changé : celles qui ont défilé (un fond uni se
    # raccorde n'importe où, ce qui ne bouge pas en haut ne dit rien du défilement)
    info = (cur.max(axis=1) - cur.min(axis=1)) > ROW_INFO
    moving = np.flatnonzero(~same & info)
    if moving.size < max(10, height * 0.03) or moving.size < 0.15 * np.count_nonzero(info):
        return 0, bottom           # presque rien : heure de la barre d'état, animation
    # Un raccord compare les lignes de cur qui avaient leur place dans prev (au-dessus de
    # bas une fois décalées) : au moins un cinquième de celles qui ont défilé
    need = max(10, moving.size // 5)
    has_match = np.searchsorted(moving, bottom - np.arange(bottom), side="left")

    def mismatch(d: int, rows) -> float:
        ys = rows[rows + d < bottom]
        if ys.size == 0:
            return 1.0
        diff = np.abs(cur[ys] - prev[ys + d]).mean(axis=1)
        return float(np.count_nonzero(diff > ROW_TOL)) / max(1, ys.size)

    # Tous les décalages sur un échantillon des lignes, puis les meilleurs sur toutes
    sample = moving[::max(1, moving.size // 300)]
    coarse = np.full(bottom, np.inf)
    for d in range(1, bottom):
        if has_match[d] < need:
            break                  # de moins en moins de lignes à comparer
        coarse[d] = mismatch(d, sample)
    if not np.isfinite(coarse).any():
        return None, bottom
    best_coarse = float(coarse.min())
    candidates = np.flatnonzero(coarse <= best_coarse + 0.1)
    candidates = candidates[np.argsort(coarse[candidates])][:20]
    fine = {int(d): mismatch(int(d), moving) for d in candidates}
    best = min(fine.values())
    near = [d for d, s in fine.items() if s <= best + 0.02]
    d = min(near, key=lambda c: (abs(c - expected), c) if expected else (c, 0))
    score = fine[d]
    if score <= MATCH_GOOD:
        return d, _footer_start(prev, cur, d, bottom)
    if score <= MATCH_FAIR:
        # Raccord moyen : retenu s'il se détache nettement de tous les autres décalages
        others = coarse.copy()
        others[max(0, d - 8):d + 9] = np.inf
        if float(others.min()) >= score + 0.25:
            return d, _footer_start(prev, cur, d, bottom)
    return None, bottom


class ScrollStitcher:
    """Captures successives d'une page qu'on fait défiler vers le bas, assemblées : haut de
    la première capture, ce qui apparaît en bas de l'écran à chaque défilement, puis le
    bas fixe de la dernière (barre de navigation)."""

    def __init__(self, first):
        self.last = first.convert("RGB")
        self._sig = row_signature(self.last)
        self._bands: list = []           # morceaux de l'image, dans l'ordre
        self._end: int | None = None     # ligne de la dernière capture où s'arrête l'assemblé
        self.screens = 1

    @property
    def height(self) -> int:
        """Hauteur de l'image assemblée jusqu'ici."""
        return sum(b.height for b in self._bands) + self.last.height - (self._end or 0)

    def add(self, frame, expected: int = 0) -> int | None:
        """Capture prise après un défilement : décalage trouvé (0 : rien n'a bougé, la
        capture n'est pas gardée ; None : pas de raccord)."""
        frame = frame.convert("RGB")
        sig = row_signature(frame)
        d, bottom = match_scroll(self._sig, sig, expected)
        if not d:
            return d
        width = frame.width
        if self._end is None:
            self._bands.append(self.last.crop((0, 0, width, bottom)))
            self._end = bottom
        if self._end < d:
            # Bas de l'écran sans rien qui défile (page presque vide) : ce qui est sorti par
            # le haut de la nouvelle capture n'est que dans l'ancienne
            self._bands.append(self.last.crop((0, self._end, width, d)))
            self._end = d
        start = self._end - d                  # suite de l'assemblé, dans la nouvelle capture
        if bottom > start:
            self._bands.append(frame.crop((0, start, width, bottom)))
            self._end = bottom
        else:
            self._end = start
        self.last, self._sig = frame, sig
        self.screens += 1
        return d

    def image(self, max_height: int = 0):
        """Image assemblée, coupée à max_height (0 : entière)."""
        from PIL import Image
        if self._end is None:
            bands = [self.last]
        else:
            bands = [*self._bands, self.last.crop((0, self._end, self.last.width, self.last.height))]
        total = sum(b.height for b in bands)
        if max_height:
            total = min(total, max_height)
        out = Image.new("RGB", (self.last.width, total))
        y = 0
        for band in bands:
            if y >= total:
                break
            out.paste(band, (0, y))
            y += band.height
        return out


# ─────────────────────────────────────────────
#  Service de la section
# ─────────────────────────────────────────────
class PhoneService:
    """post(texte, fait) : envoie un message à la page (app.post_message)."""

    def __init__(self, bridge, post, set_clipboard=None, get_clipboard=None, add_capture=None):
        self.bridge = bridge
        self.post = post
        self._add_capture = add_capture          # image PIL, nom → onglet de Captures
        self._set_clipboard = set_clipboard
        self._get_clipboard = get_clipboard
        self.adb = find_adb()
        self._devices: list[dict] = []
        self._info: dict[str, dict] = {}        # numéro → {name, android}
        self._describing: set[str] = set()
        self._lock = threading.Lock()
        self._mirror: Mirror | None = None
        self._sid = 0
        self._long: threading.Thread | None = None       # capture défilante en cours
        self._long_stop: threading.Event | None = None
        self._tracker = DeviceTracker(self.adb, self._on_devices) if self.adb else None
        self._listed = threading.Event()        # première liste reçue du serveur adb
        if not self._tracker:
            self._listed.set()

    def wait_listed(self, timeout: float) -> bool:
        """Attend la première liste des appareils (démarrage de l'interface : la section
        Smartphone ouverte la dernière fois est rouverte si le téléphone est branché)."""
        return self._listed.wait(timeout)

    def start(self):
        if self._tracker:
            self._tracker.start()
        else:
            logger.info("Smartphone : adb introuvable (outils Android absents), section jamais affichée.")

    def shutdown(self):
        if self._long_stop:
            self._long_stop.set()
        if self._tracker:
            self._tracker.stop()
        mirror = self._mirror
        if mirror:
            mirror.stop()
            mirror.join(3)

    # ── Appareils ──
    def devices(self) -> list[dict]:
        with self._lock:
            out = []
            for d in self._devices:
                if d["emulator"] and not INCLUDE_EMULATORS:
                    continue
                info = self._info.get(d["serial"], {})
                out.append({"serial": d["serial"], "state": d["state"],
                            "name": info.get("name") or d["model"] or d["serial"],
                            "android": info.get("android", "")})
            return out

    def snapshot(self) -> dict:
        mirror = self._mirror
        return {"devices": self.devices(), "session": mirror.info() if mirror else None}

    def _on_devices(self, devices: list[dict]):
        with self._lock:
            self._devices = devices
            self._listed.set()
            unknown = [d["serial"] for d in devices if self.adb and d["state"] == "device"
                       and d["serial"] not in self._info and d["serial"] not in self._describing]
            self._describing.update(unknown)
        for serial in unknown:
            threading.Thread(target=self._describe, args=(serial,), daemon=True, name="phone-describe").start()
        mirror = self._mirror
        if mirror and mirror.state != "stopped" and not any(
                d["serial"] == mirror.serial and d["state"] == "device" for d in devices):
            mirror.stop()             # débranché : la vidéo s'arrête de toute façon
        self.bridge.emit("phone:devices", {"devices": self.devices()})

    def _describe(self, serial: str):
        """Nom du téléphone et version d'Android (getprop)."""
        props = ("ro.product.manufacturer", "ro.product.model", "ro.product.marketname",
                 "ro.build.version.release")
        try:
            res = subprocess.run([self.adb, "-s", serial, "shell", "; ".join(f"getprop {p}" for p in props)],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=15, creationflags=CREATE_NO_WINDOW)
            lines = (res.stdout.splitlines() + [""] * len(props))[:len(props)]
            manufacturer, model, market, release = (s.strip() for s in lines)
            # Pas encore de réponse (téléphone qui démarre) : redemandé au prochain changement
            info = ({"name": device_name(manufacturer, model, market), "android": release}
                    if res.returncode == 0 and (model or market) else None)
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"Smartphone : description de {serial} impossible ({e})")
            info = None
        with self._lock:
            self._describing.discard(serial)
            if info:
                self._info[serial] = info
        if info:
            self.bridge.emit("phone:devices", {"devices": self.devices()})

    # ── Session ──
    def mirror_start(self, serial: str, restart: bool = False) -> dict:
        if not self.adb:
            return {"error": "adb introuvable."}
        device = next((d for d in self.devices() if d["serial"] == serial), None)
        if device is None:
            return {"error": "Ce téléphone n'est plus branché."}
        if device["state"] != "device":
            return {"error": "Le téléphone n'a pas encore autorisé le débogage USB."}
        with self._lock:
            old = self._mirror
            if old and old.serial == serial and old.state != "stopped" and not restart:
                return old.info()
            self._sid += 1
            mirror = Mirror(self.adb, serial, self._sid, self.post, self._on_mirror_event)
            self._mirror = mirror
        if old:
            old.stop()
        mirror.start()
        logger.info(f"Smartphone : connexion à {device['name']}.")
        return mirror.info()

    # ── Capture d'écran ──
    def screenshot(self, sid) -> dict:
        """Capture de l'écran du téléphone affiché, ouverte dans Captures, en pleine
        résolution. Noire ou impossible : {"frame": True}, la page envoie alors l'image
        qu'elle affiche (screenshot_frame)."""
        mirror = self._mirror
        if not mirror or mirror.sid != sid or mirror.state != "running":
            return {"error": "Le téléphone n'est plus affiché."}
        image = self._screencap(mirror.serial)
        if image is None:
            return {"frame": True}
        return self._capture(image, mirror)

    def _screencap(self, serial: str):
        """Écran du téléphone en pleine résolution (image PIL) ; None si la capture est
        impossible ou toute noire (écran éteint, contenu protégé)."""
        try:
            res = subprocess.run([self.adb, "-s", serial, "exec-out", "screencap", "-p"],
                                 capture_output=True, timeout=SCREENCAP_TIMEOUT, creationflags=CREATE_NO_WINDOW)
            from PIL import Image
            image = Image.open(io.BytesIO(res.stdout))
            image.load()
        except Exception as e:
            logger.debug(f"Smartphone : capture par adb impossible ({e})")
            return None
        if image.convert("L").getbbox() is None:
            return None
        return image

    # ── Capture défilante ──
    def long_screenshot(self, sid) -> dict:
        """Lance la capture défilante du téléphone affiché ; avancement et fin par
        l'événement phone:long, image ouverte dans Captures."""
        mirror = self._mirror
        if not mirror or mirror.sid != sid or mirror.state != "running" or not mirror.size:
            return {"error": "Le téléphone n'est plus affiché."}
        if not self._add_capture:
            return {"error": "Section Captures indisponible."}
        with self._lock:
            if self._long and self._long.is_alive():
                return {"error": "Une capture défilante est déjà en cours."}
            stop = threading.Event()
            self._long_stop = stop
            self._long = threading.Thread(target=self._long_run, args=(mirror, stop), daemon=True,
                                          name="phone-long-capture")
            self._long.start()
        return {"ok": True}

    def long_stop(self, sid=None):
        """Arrête la capture défilante : ce qui est déjà assemblé est ouvert dans Captures."""
        mirror, stop = self._mirror, self._long_stop
        if stop and (sid is None or (mirror and mirror.sid == sid)):
            stop.set()

    def _long_run(self, mirror: Mirror, stop: threading.Event):
        from captures import MAX_SIDE

        def emit(state: str, **data):
            self.bridge.emit("phone:long", {"sid": mirror.sid, "state": state, **data})

        # Écran éteint par l'option : la capture serait noire, il est rallumé le temps de la capture
        screen_off = settings.get("phone_screen_off", False)
        try:
            if screen_off:
                mirror.send(msg_display_power(True))
                time.sleep(0.6)
            first = self._screencap(mirror.serial)
            if first is None:
                emit("error", error="Capture de l'écran du téléphone impossible "
                                    "(l'application affichée interdit peut-être les captures).")
                return
            stitcher = ScrollStitcher(first)
            emit("progress", screens=1, height=first.height)
            reason = "screens"
            while stitcher.screens < LONG_MAX_SCREENS:
                if stop.is_set():
                    reason = "stopped"
                    break
                expected = self._swipe(mirror, first.height)
                if expected is None:
                    reason = "lost"
                    break
                # Pas de raccord (rebond en bas de la page, image qui se charge) ou rien n'a
                # bougé (téléphone lent à réagir, ou bas de la page) : nouvelles captures
                d = frame = None
                for attempt in range(3):
                    time.sleep(SETTLE if attempt == 0 else RETRY_DELAY)
                    frame = self._screencap(mirror.serial)
                    if frame is None or frame.size != first.size:
                        frame = None       # écran éteint, verrouillé ou tourné
                        break
                    d = stitcher.add(frame, expected)
                    if d:
                        break
                if d is None:
                    reason = ("lost" if mirror.state != "running" else
                              "screen" if frame is None else "unmatched")
                    break
                if d == 0:
                    reason = "end"
                    break
                emit("progress", screens=stitcher.screens, height=stitcher.height)
                if stitcher.height >= MAX_SIDE:
                    reason = "limit"
                    break
            image = stitcher.image(max_height=MAX_SIDE)
            res = self._capture(image, mirror)
            logger.info(f"Smartphone : capture défilante, {stitcher.screens} écran(s), "
                        f"{image.width} × {image.height} px ({reason}).")
            if res.get("ok"):
                emit("done", reason=reason, screens=stitcher.screens, width=image.width, height=image.height)
            else:
                emit("error", error=res.get("error") or "Capture impossible.")
        except Exception as e:
            logger.log(f"Smartphone : capture défilante impossible ({e})")
            emit("error", error=f"Capture défilante impossible : {e}")
        finally:
            if screen_off and settings.get("phone_screen_off", False) and mirror.state == "running":
                mirror.send(msg_display_power(False))

    def _swipe(self, mirror: Mirror, device_height: int) -> int | None:
        """Glissement d'un doigt vers le haut au milieu de l'écran, immobile un instant avant
        d'être levé (pas d'élan). Retourne le décalage attendu en pixels de la capture,
        None si le téléphone n'est plus affiché."""
        if mirror.state != "running" or not mirror.size:
            return None
        w, h = mirror.size
        x, y0, y1 = w // 2, int(h * SWIPE_FROM), int(h * SWIPE_TO)
        mirror.send(msg_touch(TOUCH_DOWN, FINGER, x, y0, w, h))
        for i in range(1, SWIPE_STEPS + 1):
            # De plus en plus lentement jusqu'à l'arrêt : pas de vitesse au moment de lever
            y = y0 + round((y1 - y0) * (1 - (1 - i / SWIPE_STEPS) ** 2))
            mirror.send(SWIPE_STEP)
            mirror.send(msg_touch(TOUCH_MOVE, FINGER, x, y, w, h))
        # Doigt immobile : les derniers points de la vitesse calculée par Android sont à l'arrêt
        hold = max(1, round(SWIPE_HOLD / SWIPE_STEP))
        for _ in range(hold):
            mirror.send(SWIPE_STEP)
            mirror.send(msg_touch(TOUCH_MOVE, FINGER, x, y1, w, h))
        mirror.send(msg_touch(TOUCH_UP, FINGER, x, y1, w, h))
        done = threading.Event()
        mirror.send(done)
        if not done.wait((SWIPE_STEPS + hold) * SWIPE_STEP * 2 + 5) or mirror.state != "running":
            return None
        return round((y0 - y1) * device_height / h)

    def screenshot_frame(self, sid, data_url: str) -> dict:
        """Image affichée dans la page (data URL PNG), à défaut de la capture du téléphone."""
        mirror = self._mirror
        if not mirror or mirror.sid != sid:
            return {"error": "Le téléphone n'est plus affiché."}
        try:
            from PIL import Image
            raw = base64.b64decode(str(data_url).split(",", 1)[1])
            image = Image.open(io.BytesIO(raw))
            image.load()
        except Exception as e:
            return {"error": f"Capture impossible : {e}"}
        return self._capture(image, mirror)

    def _capture(self, image, mirror) -> dict:
        if not self._add_capture:
            return {"error": "Section Captures indisponible."}
        name = next((d["name"] for d in self.devices() if d["serial"] == mirror.serial), mirror.name or "Téléphone")
        try:
            self._add_capture(image, name)
        except Exception as e:
            logger.log(f"Smartphone : capture non ajoutée ({e})")
            return {"error": f"Capture impossible : {e}"}
        return {"ok": True, "width": image.width, "height": image.height}

    def option_changed(self, key: str):
        """Option de la section modifiée : appliquée au téléphone affiché."""
        mirror = self._mirror
        if not mirror or mirror.state == "stopped":
            return
        if key == "phone_screen_off":
            if mirror.state == "running":       # sinon : appliquée une fois connecté
                mirror.send(msg_display_power(not settings.get(key, False)))
        elif key == "phone_keep_awake":
            # Option du serveur, donnée à son lancement : nouvelle connexion
            self.mirror_start(mirror.serial, restart=True)

    def mirror_stop(self, sid: int | None = None):
        mirror = self._mirror
        if mirror and (sid is None or mirror.sid == sid):
            mirror.stop()

    def _on_mirror_event(self, kind: str, data: dict):
        if kind == "state":
            self.bridge.emit("phone:state", data)
        elif kind == "clipboard":
            text, mirror = data["text"], data["mirror"]
            if not text or not mirror.received_clipboard(text):
                return
            if self._set_clipboard:
                # Repris ensuite par clipboard_listener : pc_clipboard le reconnaît
                ok = self._set_clipboard(text)
                self.bridge.emit("phone:clipboard", {"chars": len(text), "ok": bool(ok)})

    def pc_clipboard(self, text: str):
        """Copie faite dans Windows (clipboard_listener) : passée au téléphone affiché."""
        mirror = self._mirror
        if not text or not mirror or mirror.state != "running" or text == mirror.phone_text:
            return
        mirror.set_clipboard(text, paste=False)

    def input(self, sid, events) -> dict | None:
        """Événements de la page, dans l'ordre (voir web/js/views/phone.js) :
        ["t", action, x, y, l, h, doigt]  toucher (souris : doigt -1)
        ["s", x, y, l, h, dx, dy]        molette (crans)
        ["k", action, code, répét., méta] touche Android
        ["p", code, méta]                touche pressée puis relâchée
        ["x", texte]                     texte tapé
        ["c", 1 | 2]                     copier / couper la sélection du téléphone
        ["v"]                            coller le presse-papiers de Windows
        ["b"]                            retour (ou rallumer l'écran)
        ["n", volet]                     notifications, réglages rapides, refermer
        ["o"]                            pivoter ; ["d", allumé] écran du téléphone
        ["r"]                            nouvelle image clé (décodeur réinitialisé)"""
        mirror = self._mirror
        if not mirror or mirror.sid != sid or mirror.state != "running" or not isinstance(events, list):
            return None
        out: list[bytes] = []
        result = None

        def flush():
            if out:
                mirror.send(b"".join(out))
                out.clear()

        for ev in events[:500]:
            try:
                kind = ev[0]
                if kind == "t":
                    action, x, y, w, h, pid = (int(v) for v in ev[1:7])
                    if action in (TOUCH_DOWN, TOUCH_UP, TOUCH_MOVE) and w > 0 and h > 0:
                        out.append(msg_touch(action, pid, max(0, min(x, w - 1)), max(0, min(y, h - 1)), w, h))
                elif kind == "s":
                    x, y, w, h = (int(v) for v in ev[1:5])
                    if w > 0 and h > 0:
                        out.append(msg_scroll(max(0, min(x, w - 1)), max(0, min(y, h - 1)), w, h,
                                              float(ev[5]), float(ev[6])))
                elif kind == "k":
                    action, code, repeat, meta = (int(v) for v in ev[1:5])
                    if action in (KEY_DOWN, KEY_UP):
                        out.append(msg_keycode(action, code, max(0, repeat), meta))
                elif kind == "p":
                    out.append(msg_press(int(ev[1]), int(ev[2]) if len(ev) > 2 else 0))
                elif kind == "x":
                    flush()
                    mirror.type_text(str(ev[1]))
                elif kind == "c":
                    out.append(msg_get_clipboard(COPY_KEY_CUT if int(ev[1]) == COPY_KEY_CUT else COPY_KEY_COPY))
                elif kind == "v":
                    text = self._get_clipboard() if self._get_clipboard else ""
                    if text:
                        flush()
                        mirror.set_clipboard(text, paste=True)
                    else:
                        result = {"empty_clipboard": True}
                elif kind == "b":
                    out.append(msg_back_or_screen_on())
                elif kind == "key":
                    out.append(msg_press(KEYS[str(ev[1])]))
                elif kind == "n":
                    out.append(msg_empty(PANELS[str(ev[1])]))
                elif kind == "o":
                    out.append(msg_empty(T_ROTATE))
                elif kind == "d":
                    out.append(msg_display_power(bool(ev[1])))
                elif kind == "r":
                    out.append(msg_empty(T_RESET_VIDEO))
            except (IndexError, KeyError, TypeError, ValueError, struct.error):
                continue
        flush()
        return result
