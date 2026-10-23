"""minutes.py : découpage d'une longue transcription, notes par partie puis compte rendu
(JSON mis en forme), fichier rangé à côté de l'enregistrement (modèle remplacé par une
doublure)."""

from tests import _env  # noqa: F401  (données temporaires)

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import minutes

REPORT = {"bref": "Point sur le budget.", "decisions": ["valider le stand de 12 m²"],
          "actions": [{"qui": "karim", "quoi": "envoyer les invitations", "quand": "avant le 15 novembre"},
                      {"qui": "", "quoi": "demander le tarif au traiteur", "quand": ""},
                      {"qui": "", "quoi": " ", "quand": "lundi"}],
          "points": [], "questions": ["option végétarienne ?"]}


def chunk(text):
    return SimpleNamespace(message=SimpleNamespace(content=text), done_reason=None)


def answer(data=REPORT):
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)), done_reason="stop")


class FakeRecorder:
    def __init__(self, folder: Path, text: str):
        self.folder = folder
        (folder / "Réunion.mp3").write_bytes(b"")
        (folder / "Réunion.txt").write_text(text, encoding="utf-8")

    def _file(self, name):
        p = self.folder / name
        return p if p.exists() else None

    def transcript(self, name):
        p = self._file(name)
        return {"text": p.with_suffix(".txt").read_text(encoding="utf-8"), "path": str(p.with_suffix(".txt"))}


class Bridge:
    def __init__(self):
        self.events = []
        self.done = threading.Event()

    def emit(self, name, data=None):
        self.events.append((name, data))
        if name == "minutes:state" and data["state"] in ("done", "error", "stopped"):
            self.done.set()


class SplitTest(unittest.TestCase):
    def test_short_text_one_part(self):
        self.assertEqual(minutes.split_text("Bonjour à tous.", 100), ["Bonjour à tous."])

    def test_cut_between_sentences(self):
        text = " ".join(f"Phrase numéro {i} de la réunion." for i in range(200))
        parts = minutes.split_text(text, 500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertTrue(all(p.endswith(".") for p in parts))
        self.assertEqual(" ".join(parts), text)

    def test_path(self):
        self.assertEqual(minutes.minutes_path(Path("C:/x/Réunion.mp3")).name, "Réunion.compte-rendu.md")

    def test_render(self):
        text = minutes.render(REPORT)
        self.assertIn("## Décisions\n\n- Valider le stand de 12 m²", text)
        self.assertIn("- **Karim** : envoyer les invitations — avant le 15 novembre", text)
        self.assertIn("- Demander le tarif au traiteur\n", text)
        self.assertNotIn("Points abordés", text)             # section vide omise
        self.assertNotIn("lundi", text)                      # action sans contenu ignorée
        self.assertEqual(minutes.render({}), "")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_env.DATA))
        self.calls = []

    def service(self, text):
        chat = SimpleNamespace(available=True, models=["gemma4:e4b"], models_error="", failure=str)
        self.bridge = Bridge()
        svc = minutes.MinutesService(self.bridge, chat, FakeRecorder(self.dir, text))

        def fake_chat(label, priority=0, **kw):
            self.calls.append(kw["messages"][-1]["content"])
            if not kw.get("stream"):                    # compte rendu final : JSON
                return answer()
            return iter([chunk("- note")])              # notes d'une partie

        patcher = mock.patch.object(minutes.llm, "chat", side_effect=fake_chat)
        patcher.start()
        self.addCleanup(patcher.stop)
        return svc

    def test_short_transcript_one_call_and_saved(self):
        svc = self.service("On valide le budget de 12 000 euros. Paul envoie le devis lundi.")
        self.assertTrue(svc.start("Réunion.mp3")["ok"])
        self.assertTrue(self.bridge.done.wait(5))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("12 000 euros", self.calls[0])
        saved = svc.get("Réunion.mp3")
        self.assertEqual(saved["state"], "done")
        self.assertEqual(saved["text"], minutes.render(REPORT))
        self.assertEqual(saved["model"], "gemma4:e4b")
        self.assertTrue((self.dir / "Réunion.compte-rendu.md").is_file())

    def test_long_transcript_read_by_parts(self):
        text = " ".join(f"Le point {i} est traité." for i in range(6000))
        with mock.patch.object(minutes, "num_ctx", return_value=4096):
            svc = self.service(text)
            svc.start("Réunion.mp3")
            self.assertTrue(self.bridge.done.wait(10))
        parts = [c for c in self.calls if c.startswith("# Partie")]
        self.assertEqual(len(parts), minutes.MINUTES_PARTS_MAX)
        self.assertIn("Notes prises sur les", self.calls[-1])
        phases = [d["phase"] for n, d in self.bridge.events if n == "minutes:state"]
        self.assertIn(f"Lecture de la partie 1/{minutes.MINUTES_PARTS_MAX}…", phases)

    def test_stop(self):
        gate = threading.Event()

        def slow(label, priority=0, **kw):
            gate.wait(2)
            return answer()

        svc = self.service("Texte court.")
        with mock.patch.object(minutes.llm, "chat", side_effect=slow):
            svc.start("Réunion.mp3")
            time.sleep(0.2)
            svc.stop()
            gate.set()
            self.assertTrue(self.bridge.done.wait(5))
        self.assertEqual(self.bridge.events[-1][1]["state"], "stopped")
        self.assertFalse((self.dir / "Réunion.compte-rendu.md").exists())

    def test_no_transcript(self):
        svc = self.service("   ")
        self.assertFalse(svc.start("Réunion.mp3")["ok"])
        self.assertEqual(svc.get("Réunion.mp3")["state"], "none")


if __name__ == "__main__":
    unittest.main()
