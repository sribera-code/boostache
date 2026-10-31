"""recorder.py : « Dernières minutes » (tampon circulaire, nouvelle durée, fichier
écrit à l'arrêt) et transcription en direct limitée — sans périphérique audio ni modèle."""

from tests import _env  # noqa: F401  (données temporaires)

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

import recorder
from recorder import WINDOW_RATE, RingBuffer
from storage import settings


def stereo(values):
    """Échantillons mono (au rythme du tampon) → stéréo 48 kHz, comme la capture."""
    mono = np.repeat(np.asarray(values, np.float32), 2)          # 24 → 48 kHz
    return np.repeat(mono, 2)                                    # deux canaux identiques


class RingBufferTest(unittest.TestCase):
    def test_keeps_only_the_last_minutes(self):
        ring = RingBuffer(1 / 60)                    # une seconde
        self.assertEqual(ring.size, WINDOW_RATE)
        first = np.full(WINDOW_RATE // 2, 0.25)
        ring.write(stereo(first))
        self.assertAlmostEqual(ring.seconds, 0.5)
        ring.write(stereo(np.full(WINDOW_RATE, 0.5)))  # dépasse : le début disparaît
        self.assertAlmostEqual(ring.seconds, 1.0)
        data = ring.last()
        self.assertEqual(len(data), WINDOW_RATE)
        self.assertTrue((data == int(0.5 * 32767)).all())

    def test_order_across_the_wrap(self):
        ring = RingBuffer(1 / 60)
        ramp = np.linspace(-0.9, 0.9, WINDOW_RATE + WINDOW_RATE // 3)
        for part in np.array_split(ramp, 7):           # par paquets, comme la capture
            ring.write(stereo(part))
        data = ring.last()
        expected = (np.clip(ramp[-WINDOW_RATE:], -1, 1) * 32767).astype(np.int16)
        # (arrondis en float32 : un pas de 16 bits d'écart au plus)
        self.assertLessEqual(np.abs(data.astype(int) - expected).max(), 1)
        self.assertLessEqual(np.abs(ring.last(0.25).astype(int) - expected[-WINDOW_RATE // 4:]).max(), 1)

    def test_odd_packets_are_not_lost(self):
        ring = RingBuffer(1)
        samples = stereo(np.full(1000, 0.1))
        for i in range(0, len(samples), 6):            # 3 trames stéréo à 48 kHz : moitiés reportées
            ring.write(samples[i:i + 6])
        self.assertEqual(ring.filled, 1000)

    def test_resize_keeps_the_most_recent(self):
        ring = RingBuffer(1 / 60)
        ramp = np.linspace(0, 0.9, WINDOW_RATE)
        ring.write(stereo(ramp))
        ring.resize(0.5 / 60)                          # une demi-seconde
        self.assertEqual(ring.filled, WINDOW_RATE // 2)
        kept = ring.last()
        self.assertLessEqual(np.abs(kept.astype(int) - (ramp[-WINDOW_RATE // 2:] * 32767).astype(int)).max(), 1)
        ring.resize(2 / 60)                            # plus long : rien d'inventé
        self.assertTrue(np.array_equal(ring.last(), kept))
        ring.write(stereo(np.full(100, 0.5)))
        self.assertEqual(ring.filled, WINDOW_RATE // 2 + 100)


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=_env.DATA))
        settings.set("audio_dir", str(self.folder))
        settings.set("audio_format", "wav")
        settings.set("audio_transcribe", True)
        self.service = recorder.RecorderService(mock.Mock(), is_visible=lambda: False)

    def session(self, seconds):
        ring = RingBuffer(1)
        ring.write(stereo(np.full(int(WINDOW_RATE * seconds), 0.2)))
        return SimpleNamespace(ring=ring, window=1, live=None, saved=None, error="", frames=int(seconds * 48000),
                               seconds=seconds, path=self.folder / "Enregistrement en cours.wav")

    def test_stop_saves_the_last_minutes(self):
        session = self.session(3)
        with mock.patch.object(self.service, "transcribe") as transcribe:
            self.service.session_ended(session)
        self.assertIsNone(session.ring)                         # mémoire libérée
        path = session.saved
        self.assertTrue(path.name.startswith("Enregistrement "))
        with wave.open(str(path), "rb") as f:
            self.assertEqual((f.getframerate(), f.getnchannels(), f.getnframes()), (WINDOW_RATE, 1, WINDOW_RATE * 3))
        transcribe.assert_called_once_with(path.name)           # pas de texte en direct : transcription auto

    def test_live_text_is_written_next_to_the_audio(self):
        session = self.session(2)
        live = SimpleNamespace(text="Bonjour à tous.", state="done", pending=0.0, error="",
                               done=mock.Mock(is_set=lambda: False), finish=mock.Mock(), cancel=mock.Mock())
        session.live = live
        self.service._live_names.add(session.path.name)
        with mock.patch.object(self.service, "transcribe") as transcribe:
            self.service.session_ended(session)
            transcript = session.saved.with_suffix(".txt")
            self.assertFalse(transcript.exists())               # pas avant la fin de la transcription
            live.done = mock.Mock(is_set=lambda: True)
            self.service._on_live(session, live)
        self.assertEqual(transcript.read_text(encoding="utf-8"), "Bonjour à tous.\n")
        transcribe.assert_not_called()
        self.assertFalse((self.folder / "Enregistrement en cours.wav").exists())

    def test_too_short(self):
        session = self.session(0.1)
        self.service.session_ended(session)
        self.assertIsNone(session.saved)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_minutes_setting(self):
        settings.set("audio_window_minutes", 7)
        self.assertEqual(self.service.window_minutes(), recorder.WINDOW_DEFAULT)
        settings.set("audio_window_minutes", 60)
        self.assertEqual(self.service.window_minutes(), 60)


class LiveWindowTest(unittest.TestCase):
    """Transcription en direct limitée aux dernières minutes : texte et son en attente bornés."""

    def test_old_text_and_audio_are_dropped(self):
        import transcriber
        rate = transcriber.RATE
        model = mock.Mock()
        model.transcribe.side_effect = lambda audio, **kw: (
            [SimpleNamespace(start=0.0, end=len(audio) / rate, text=" phrase")], SimpleNamespace(language_probability=1))
        owner = mock.Mock(model_for=lambda *a: model)
        with mock.patch("threading.Thread"):                    # pas de thread : _run appelé à la main
            live = transcriber.Live(owner, "small", "fr", lambda _: None, window=10)
        live._offset = 30 * rate                                # 30 s de son déjà reçues
        for start in (0, 12, 25):                               # morceaux de 3 s
            live._queue.put((start, np.zeros(3 * rate, np.float32)))
        live._queue.put(None)
        live._run()
        self.assertEqual(model.transcribe.call_count, 1)        # 0 s et 12 s : hors des 10 dernières secondes
        self.assertEqual([p[0] for p in live._parts], [25.0])
        self.assertEqual(live.state, "done")


if __name__ == "__main__":
    unittest.main()
