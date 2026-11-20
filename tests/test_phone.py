"""phone.py : messages du protocole de scrcpy 5.0.1 (tailles et champs, comme
ControlMessageReader du serveur les lit), lecture du flux vidéo, liste des appareils
du serveur adb (serveur factice), événements de la page. Ni téléphone ni adb requis."""

from tests import _env  # noqa: F401  (données temporaires)

import base64
import json
import socket
import struct
import threading
import unittest
from unittest import mock

import phone
from storage import settings


class Messages(unittest.TestCase):
    def test_touch_layout(self):
        raw = phone.msg_touch(phone.TOUCH_DOWN, -1, 100, 200, 1080, 2400)
        self.assertEqual(len(raw), 32)
        kind, action, pid, x, y, w, h, pressure, action_button, buttons = struct.unpack(">BBqiiHHHii", raw)
        self.assertEqual((kind, action, pid, x, y, w, h), (2, 0, -1, 100, 200, 1080, 2400))
        self.assertEqual((pressure, action_button, buttons), (0xFFFF, 1, 1))
        up = struct.unpack(">BBqiiHHHii", phone.msg_touch(phone.TOUCH_UP, -1, 1, 2, 10, 20))
        self.assertEqual(up[7:], (0, 1, 0))           # levé : pas de pression, plus de bouton
        move = struct.unpack(">BBqiiHHHii", phone.msg_touch(phone.TOUCH_MOVE, 3, 1, 2, 10, 20))
        self.assertEqual(move[7:], (0xFFFF, 0, 1))

    def test_scroll_layout(self):
        raw = phone.msg_scroll(5, 6, 1080, 2400, 0, -1)
        self.assertEqual(len(raw), 21)
        kind, x, y, w, h, hs, vs, buttons = struct.unpack(">BiiHHhhi", raw)
        self.assertEqual((kind, x, y, w, h, hs, buttons), (3, 5, 6, 1080, 2400, 0, 0))
        # Le serveur lit vs / 2^15 × 16 : un cran vers le bas
        self.assertAlmostEqual(vs / 0x8000 * 16, -1, places=3)
        # Bornes [-16, 16]
        self.assertEqual(struct.unpack(">BiiHHhhi", phone.msg_scroll(0, 0, 1, 1, 99, -99))[5:7], (0x7FFF, -0x8000))

    def test_keycode_and_press(self):
        raw = phone.msg_keycode(phone.KEY_DOWN, 66, 1, 0x3000)
        self.assertEqual(len(raw), 14)
        self.assertEqual(struct.unpack(">BBiii", raw), (0, 0, 66, 1, 0x3000))
        press = phone.msg_press(29, 0x3000)
        self.assertEqual(len(press), 28)
        self.assertEqual(struct.unpack(">BBiii", press[14:])[1], phone.KEY_UP)

    def test_text_chunks_keep_characters_whole(self):
        text = "é" * 400                                  # 800 octets en UTF-8
        chunks = phone.msg_text(text)
        decoded = ""
        for raw in chunks:
            kind, size = struct.unpack(">BI", raw[:5])
            self.assertEqual(kind, 1)
            self.assertLessEqual(size, phone.TEXT_MAX)
            self.assertEqual(len(raw), 5 + size)
            decoded += raw[5:].decode("utf-8")              # jamais coupé au milieu d'un caractère
        self.assertEqual(decoded, text)
        self.assertEqual(phone.msg_text(""), [])

    def test_clipboard(self):
        raw = phone.msg_set_clipboard("Bonjour 👋", paste=True)
        kind, seq, paste, size = struct.unpack(">BqBI", raw[:14])
        self.assertEqual((kind, seq, paste), (9, 0, 1))
        self.assertEqual(raw[14:].decode("utf-8"), "Bonjour 👋")
        self.assertEqual(size, len("Bonjour 👋".encode("utf-8")))
        self.assertEqual(phone.msg_get_clipboard(phone.COPY_KEY_CUT), b"\x08\x02")
        long = phone.msg_set_clipboard("a" * (phone.CLIPBOARD_MAX + 10), paste=False)
        self.assertEqual(len(long), 14 + phone.CLIPBOARD_MAX)

    def test_small_messages(self):
        self.assertEqual(phone.msg_back_or_screen_on(), b"\x04\x00\x04\x01")
        self.assertEqual(phone.msg_display_power(False), b"\x0a\x00")
        self.assertEqual(phone.msg_empty(phone.T_RESET_VIDEO), b"\x11")
        self.assertEqual(phone.msg_empty(phone.T_ROTATE), b"\x0b")


class Devices(unittest.TestCase):
    def test_parse_track_devices_l(self):
        text = ("R5CT1234ABC            device product:dm3qxeea model:SM_S918B device:dm3q transport_id:3\n"
                "emulator-5554          device product:sdk_gphone64_x86_64 model:sdk_gphone64_x86_64 "
                "device:emu64xa transport_id:1\n"
                "0123456789ABCDEF       unauthorized transport_id:4\n")
        devices = phone.parse_devices(text)
        self.assertEqual([d["serial"] for d in devices], ["R5CT1234ABC", "emulator-5554", "0123456789ABCDEF"])
        self.assertEqual(devices[0]["model"], "SM S918B")
        self.assertFalse(devices[0]["emulator"])
        self.assertTrue(devices[1]["emulator"])
        self.assertEqual(devices[2]["state"], "unauthorized")
        self.assertEqual(phone.parse_devices(""), [])

    def test_device_name(self):
        self.assertEqual(phone.device_name("Google", "Pixel 7"), "Google Pixel 7")
        self.assertEqual(phone.device_name("samsung", "SM-S918B"), "Samsung SM-S918B")
        self.assertEqual(phone.device_name("OnePlus", "OnePlus 12"), "OnePlus 12")
        self.assertEqual(phone.device_name("Xiaomi", "2311DRK48G", "Redmi Note 13"), "Redmi Note 13")
        self.assertEqual(phone.device_name("", ""), "Téléphone")

    def test_tracker_reads_updates(self):
        """Serveur adb factice : la commande attendue, puis deux listes successives."""
        server = socket.create_server(("127.0.0.1", 0))
        port = server.getsockname()[1]
        received = []
        lists = []
        done = threading.Event()

        def adb_server():
            conn, _ = server.accept()
            size = int(conn.recv(4), 16)
            received.append(conn.recv(size))
            conn.sendall(b"OKAY")
            for payload in (b"R5CT1234ABC\tunauthorized transport_id:3\n",
                            b"R5CT1234ABC            device model:SM_S918B transport_id:3\n", b""):
                conn.sendall(b"%04x" % len(payload) + payload)
            done.wait(5)
            conn.close()

        threading.Thread(target=adb_server, daemon=True).start()

        def on_change(devices):
            lists.append(devices)
            if len(lists) == 3:
                done.set()

        with mock.patch.object(phone, "ADB_HOST", ("127.0.0.1", port)):
            tracker = phone.DeviceTracker("adb-introuvable", on_change)
            tracker.start()
            self.assertTrue(done.wait(5))
            tracker.stop()
        server.close()
        self.assertEqual(received, [b"host:track-devices-l"])
        self.assertEqual(lists[0][0]["state"], "unauthorized")
        self.assertEqual(lists[1][0]["state"], "device")
        self.assertEqual(lists[2], [])                    # téléphone débranché


class VideoStream(unittest.TestCase):
    def test_session_config_and_frames(self):
        """Paquet de session (taille), config (SPS/PPS) jointe à l'image clé, image suivante."""
        a, b = socket.socketpair()
        posts = []
        mirror = phone.Mirror("adb", "SERIAL", 7, lambda text, done: (posts.append(json.loads(text)), done(), True)[2],
                              lambda kind, data: None)
        mirror._video = a
        sps = b"\x00\x00\x00\x01\x67\x64\x00\x1f"
        key, delta = b"\x00\x00\x00\x01\x65KEY", b"\x00\x00\x00\x01\x41P"
        stream = (struct.pack(">III", 0x80000000, 1080, 2400)
                  + struct.pack(">QI", phone.FLAG_CONFIG, len(sps)) + sps
                  + struct.pack(">QI", phone.FLAG_KEY | 1000, len(key)) + key
                  + struct.pack(">QI", 2000, len(delta)) + delta)
        b.sendall(stream)
        b.close()
        with self.assertRaises(EOFError):
            mirror._video_loop()
        a.close()
        self.assertEqual(posts[0], {"phone": 7, "s": [1080, 2400]})
        self.assertEqual(mirror.size, (1080, 2400))
        self.assertEqual(posts[1]["k"], 1)
        self.assertEqual(base64.b64decode(posts[1]["d"]), sps + key)
        self.assertEqual(posts[2]["k"], 0)
        self.assertEqual(base64.b64decode(posts[2]["d"]), delta)
        self.assertEqual(mirror._inflight, 0)

    def test_backlog_skips_to_next_key_frame(self):
        a, b = socket.socketpair()
        posts = []
        # Interface qui ne rend jamais la main : les images en attente s'accumulent
        mirror = phone.Mirror("adb", "SERIAL", 1, lambda text, done: (posts.append(json.loads(text)), True)[1],
                              lambda kind, data: None)
        mirror._video = a
        frames = b"".join(struct.pack(">QI", 0, 1) + b"x" for _ in range(phone.MAX_INFLIGHT + 5))
        b.sendall(struct.pack(">QI", phone.FLAG_KEY, 1) + b"k" + frames)
        b.close()
        with self.assertRaises(EOFError):
            mirror._video_loop()
        a.close()
        self.assertEqual(len(posts), phone.MAX_INFLIGHT + 1)    # clé + MAX_INFLIGHT, le reste sauté
        self.assertEqual(mirror._outq.get_nowait(), phone.msg_empty(phone.T_RESET_VIDEO))  # image clé demandée


class PageEvents(unittest.TestCase):
    def setUp(self):
        self.bridge = mock.Mock()
        with mock.patch.object(phone, "find_adb", return_value=None):
            self.service = phone.PhoneService(self.bridge, lambda *a: True,
                                              set_clipboard=mock.Mock(return_value=True),
                                              get_clipboard=mock.Mock(return_value="depuis Windows"))
        self.mirror = phone.Mirror("adb", "SERIAL", 3, None, self.service._on_mirror_event)
        self.mirror.state = "running"
        self.service._mirror = self.mirror

    def sent(self, pauses=None) -> bytes:
        """Messages en file (les pauses entre deux envois vont dans pauses)."""
        out = b""
        while not self.mirror._outq.empty():
            item = self.mirror._outq.get_nowait()
            if isinstance(item, float):
                if pauses is not None:
                    pauses.append((len(out), item))
            else:
                out += item
        return out

    def test_untypable_characters_are_pasted_then_clipboard_restored(self):
        # Majuscules accentuées, œ, € : pas de touches pour eux, collés un par un
        self.mirror.clip_known = "copié avant"
        self.assertEqual(phone.split_typable("été Œuf€!"),
                         [(True, "été "), (False, "Œ"), (True, "uf"), (False, "€"), (True, "!")])
        with mock.patch.object(phone, "RESTORE_DELAY", 0.05):
            self.service.input(3, [["x", "a€É"], ["x", "bœ"]])
            pauses = []
            raw = self.sent(pauses)
            # Une suite de caractères à coller part d'un coup
            expected = (phone.msg_text("a")[0] + phone.msg_set_clipboard("€É", paste=True)
                        + phone.msg_text("b")[0] + phone.msg_set_clipboard("œ", paste=True))
            self.assertEqual(raw, expected)
            # Pause après chaque collage : l'application colle avant que le presse-papiers change
            self.assertEqual([at for at, _ in pauses],
                             [len(phone.msg_text("a")[0]) + len(phone.msg_set_clipboard("€É", paste=True)),
                              len(expected)])
            # Puis le presse-papiers d'avant revient, une seule fois
            phone.time.sleep(0.3)
            self.assertEqual(self.sent(), phone.msg_set_clipboard("copié avant", paste=False))
        # Les échos des caractères collés ne sont pas des copies faites sur le téléphone
        for text in ("€É", "œ", "copié avant"):
            self.service._on_mirror_event("clipboard", {"text": text, "mirror": self.mirror})
        self.service._set_clipboard.assert_not_called()

    def test_unknown_clipboard_not_restored(self):
        with mock.patch.object(phone, "RESTORE_DELAY", 0.05):
            self.service.input(3, [["x", "€"]])
            phone.time.sleep(0.3)
        self.assertEqual(self.sent(), phone.msg_set_clipboard("€", paste=True))

    def test_events_in_order(self):
        res = self.service.input(3, [["t", 0, 10, 20, 100, 200, -1], ["t", 2, 15, 25, 100, 200, -1],
                                     ["t", 1, 999, -5, 100, 200, -1], ["x", "é"], ["key", "home"]])
        self.assertIsNone(res)
        raw = self.sent()
        down, move, up = (struct.unpack(">BBqiiHHHii", raw[i * 32:(i + 1) * 32]) for i in range(3))
        self.assertEqual((down[1], move[1], up[1]), (0, 2, 1))
        self.assertEqual((up[3], up[4]), (99, 0))               # ramené dans l'écran
        self.assertEqual(raw[96:96 + 7], b"\x01\x00\x00\x00\x02\xc3\xa9")
        self.assertEqual(raw[103:], phone.msg_press(3))

    def test_other_session_ignored(self):
        self.assertIsNone(self.service.input(2, [["x", "a"]]))
        self.assertEqual(self.sent(), b"")
        self.service.input(3, [["t", "pas un nombre"], ["bizarre"], ["x", "b"]])
        self.assertEqual(self.sent(), phone.msg_text("b")[0])

    def test_paste_windows_clipboard(self):
        self.service.input(3, [["v"]])
        self.assertEqual(self.sent(), phone.msg_set_clipboard("depuis Windows", paste=True))
        self.service._get_clipboard.return_value = ""
        self.assertEqual(self.service.input(3, [["v"]]), {"empty_clipboard": True})

    def test_clipboard_both_ways_without_echo(self):
        # Copié sur le téléphone → Windows ; la copie que Windows signale ensuite ne repart pas
        self.service._on_mirror_event("clipboard", {"text": "du téléphone", "mirror": self.mirror})
        self.service._set_clipboard.assert_called_once_with("du téléphone")
        self.bridge.emit.assert_called_with("phone:clipboard", {"chars": 12, "ok": True})
        self.service.pc_clipboard("du téléphone")
        self.assertEqual(self.sent(), b"")
        # Copié dans Windows → téléphone, sans coller ; le téléphone le renvoie aussitôt :
        # le presse-papiers de Windows n'est pas réécrit (il perdrait sa mise en forme)
        self.service.pc_clipboard("de Windows")
        self.assertEqual(self.sent(), phone.msg_set_clipboard("de Windows", paste=False))
        self.service._set_clipboard.reset_mock()
        self.service._on_mirror_event("clipboard", {"text": "de Windows", "mirror": self.mirror})
        self.service._set_clipboard.assert_not_called()
        # Plus tard, la même copie faite sur le téléphone compte
        with mock.patch.object(phone.time, "monotonic", return_value=phone.time.monotonic() + 10):
            self.service._on_mirror_event("clipboard", {"text": "de Windows", "mirror": self.mirror})
        self.service._set_clipboard.assert_called_once_with("de Windows")

    def test_paste_keeps_order(self):
        self.service.input(3, [["x", "a"], ["v"], ["x", "b"]])
        self.assertEqual(self.sent(), phone.msg_text("a")[0] + phone.msg_set_clipboard("depuis Windows", paste=True)
                         + phone.msg_text("b")[0])

    def test_options(self):
        """Écran éteint : appliqué tout de suite ; téléphone éveillé : option du serveur,
        nouvelle connexion ; sans session, rien n'est envoyé."""
        try:
            settings.set("phone_screen_off", True)
            self.service.option_changed("phone_screen_off")
            self.assertEqual(self.sent(), phone.msg_display_power(False))
            settings.set("phone_screen_off", False)
            self.service.option_changed("phone_screen_off")
            self.assertEqual(self.sent(), phone.msg_display_power(True))
            self.assertNotIn("keep_active=true", self.mirror.server_command(1))
            settings.set("phone_keep_awake", True)
            self.assertIn("keep_active=true", self.mirror.server_command(1))
            with mock.patch.object(self.service, "mirror_start") as start:
                self.service.option_changed("phone_keep_awake")
            start.assert_called_once_with("SERIAL", restart=True)
            self.mirror.state = "stopped"
            with mock.patch.object(self.service, "mirror_start") as start:
                self.service.option_changed("phone_keep_awake")
                self.service.option_changed("phone_screen_off")
            start.assert_not_called()
            self.assertEqual(self.sent(), b"")
        finally:
            settings.set("phone_screen_off", False)
            settings.set("phone_keep_awake", False)

    def test_screenshot(self):
        """Capture du téléphone (adb screencap) ouverte dans Captures ; noire ou illisible :
        la page envoie l'image qu'elle affiche."""
        import io
        from PIL import Image

        def png(color, size=(108, 234)):
            buf = io.BytesIO()
            Image.new("RGB", size, color).save(buf, "PNG")
            return buf.getvalue()

        added = []
        self.service._add_capture = lambda image, name: added.append((image.size, name))
        self.service.adb = "adb"
        with mock.patch.object(phone.subprocess, "run",
                               return_value=mock.Mock(stdout=png((30, 120, 200)), returncode=0)) as run:
            self.assertEqual(self.service.screenshot(3), {"ok": True, "width": 108, "height": 234})
        self.assertEqual(run.call_args[0][0], ["adb", "-s", "SERIAL", "exec-out", "screencap", "-p"])
        self.assertEqual(added, [((108, 234), "Téléphone")])
        # Écran éteint par l'option, application protégée : capture toute noire
        with mock.patch.object(phone.subprocess, "run", return_value=mock.Mock(stdout=png((0, 0, 0)), returncode=0)):
            self.assertEqual(self.service.screenshot(3), {"frame": True})
        with mock.patch.object(phone.subprocess, "run", return_value=mock.Mock(stdout=b"error: closed", returncode=1)):
            self.assertEqual(self.service.screenshot(3), {"frame": True})
        frame = "data:image/png;base64," + base64.b64encode(png((10, 10, 10), (88, 192))).decode()
        self.assertEqual(self.service.screenshot_frame(3, frame), {"ok": True, "width": 88, "height": 192})
        self.assertEqual(added[-1], ((88, 192), "Téléphone"))
        self.assertIn("error", self.service.screenshot_frame(3, "data:image/png;base64,pas une image"))
        # Autre session : rien
        self.assertIn("error", self.service.screenshot(2))
        self.assertEqual(len(added), 2)

    def test_devices_hide_emulators(self):
        self.service._on_devices(phone.parse_devices(
            "emulator-5554 device model:sdk transport_id:1\nR5CT device model:SM_S918B transport_id:2\n"))
        self.assertEqual([d["serial"] for d in self.service.devices()], ["R5CT"])
        self.assertTrue(self.service.wait_listed(0))


if __name__ == "__main__":
    unittest.main()
