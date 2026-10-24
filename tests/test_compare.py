"""compare.py : différences ligne à ligne et mot à mot, fusion, lecture et réécriture d'un
fichier dans son format, choix gardés d'une session à l'autre, explication et choix par
l'IA (modèle remplacé par une doublure)."""

from tests import _env  # noqa: F401  (données temporaires)

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import compare

LEFT = ["Bonjour Julie,", "Le rendez-vous est fixé au 12 novembre.", "Le prix est de 1 200 €.",
        "Merci de confirmer.", "", "Cordialement"]
RIGHT = ["Bonjour Julie,", "Le rendez-vous est fixé au 14 novembre.", "Une ligne ajoutée.",
         "Le prix est de 1 200 €.", "Merci de confirmer.", "", "Bien à vous"]


class Bridge:
    def __init__(self):
        self.events = []
        self.done = threading.Event()

    def emit(self, name, data=None):
        self.events.append((name, data))
        if (name == "compare:resolve" and data.get("finished")) or (
                name == "compare:qa" and data["item"]["state"] in ("done", "error", "stopped")):
            self.done.set()


def reply(content, done_reason="stop"):
    return SimpleNamespace(message=SimpleNamespace(content=content), done_reason=done_reason)


def chunk(text):
    return SimpleNamespace(message=SimpleNamespace(content=text), done_reason=None)


class DiffTest(unittest.TestCase):
    def test_blocks(self):
        blocks = compare.diff(LEFT, RIGHT)
        self.assertEqual([(b["a0"], b["a1"], b["b0"], b["b1"]) for b in blocks],
                         [(1, 2, 1, 3), (5, 6, 6, 7)])
        # La ligne modifiée est mise en regard de sa nouvelle version, l'ajout reste seul
        self.assertEqual([r[:2] for r in blocks[0]["rows"]], [[1, 1], [None, 2]])
        left_spans, right_spans = blocks[0]["rows"][0][2:]
        self.assertEqual(LEFT[1][slice(*left_spans[0])], "12")
        self.assertEqual(RIGHT[1][slice(*right_spans[0])], "14")

    def test_whole_line_when_nothing_in_common(self):
        [block] = compare.diff(["a", "b", "c"], ["a", "zzz", "c"])
        self.assertEqual(block["rows"], [[1, 1, None, None]])

    def test_spans_in_utf16_units(self):
        [block] = compare.diff(["prix 😀 : 12 €"], ["prix 😀 : 14 €"])
        _, _, ls, rs = block["rows"][0]
        self.assertEqual(ls, [[10, 12]])        # l'émoji compte pour deux, comme en JavaScript
        self.assertEqual(rs, [[10, 12]])

    def test_ignore_space_and_case(self):
        self.assertEqual(compare.diff(["  Foo   bar "], ["foo bar"], {"space": True, "case": True}), [])
        self.assertEqual(len(compare.diff(["  Foo   bar "], ["foo bar"], {"space": True})), 1)

    def test_identical_and_empty(self):
        self.assertEqual(compare.diff(LEFT, LEFT), [])
        [block] = compare.diff([], ["x"])
        self.assertEqual(block["rows"], [[None, 0, None, None]])

    def test_stats(self):
        st = compare.stats(LEFT, RIGHT, compare.diff(LEFT, RIGHT))
        self.assertEqual((st["blocks"], st["changed"], st["added"], st["removed"], st["same"]), (2, 2, 1, 0, 4))


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.blocks = compare.diff(LEFT, RIGHT)

    def test_choices(self):
        lines, open_ = compare.merge(LEFT, RIGHT, self.blocks, [{"choice": "right"}, {"choice": "left_right"}])
        self.assertEqual(open_, 0)
        self.assertEqual(lines, RIGHT[:6] + ["Cordialement", "Bien à vous"])

    def test_unresolved_take_default(self):
        lines, open_ = compare.merge(LEFT, RIGHT, self.blocks, [None, None], "right")
        self.assertEqual((lines, open_), (RIGHT, 2))
        lines, _ = compare.merge(LEFT, RIGHT, self.blocks, [None, None], "left")
        self.assertEqual(lines, LEFT)

    def test_written_text(self):
        lines, _ = compare.merge(LEFT, RIGHT, self.blocks, [{"choice": "text", "text": "A\r\nB"}, {"choice": "text", "text": ""}])
        self.assertEqual(lines, [LEFT[0], "A", "B", *LEFT[2:5]])

    def test_clean_text(self):
        self.assertEqual(compare.clean_text("```text\nx = 1\ny\n```"), "x = 1\ny")
        self.assertEqual(compare.clean_text("ligne\n\n"), "ligne")


class FileTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_env.DATA))

    def write(self, name, data: bytes) -> str:
        path = self.dir / name
        path.write_bytes(data)
        return str(path)

    def test_format_kept(self):
        for data, encoding, newline in (
                ("é\r\nà\r\n".encode("utf-8"), "utf-8", "\r\n"),
                ("é\nà".encode("cp1252"), "cp1252", "\n"),
                (b"\xef\xbb\xbf" + "é\r\nà".encode("utf-8"), "utf-8-sig", "\r\n"),
                ("é\r\nà\r\n".encode("utf-16"), "utf-16", "\r\n")):
            side = compare.read_file(self.write("t.txt", data))
            self.assertEqual((side["encoding"], side["newline"]), (encoding, newline))
            self.assertEqual(compare.split_lines(side["text"]), ["é", "à"])
            text = side["text"]
            self.assertEqual(compare.encode(text, side), data)

    def test_missing_character_in_encoding(self):
        side = compare.read_file(self.write("t.txt", "é\n".encode("cp1252")))
        with self.assertRaises(UnicodeEncodeError):
            compare.encode("→\n", side)
        self.assertEqual(compare.encode("→\n", side, utf8=True), "→\n".encode("utf-8"))

    def test_binary_and_folder_refused(self):
        with self.assertRaises(compare.UserError):
            compare.read_file(self.write("x.bin", b"MZ\x00\x00\x01"))
        with self.assertRaises(compare.UserError):
            compare.read_file(str(self.dir))
        with self.assertRaises(compare.UserError):
            compare.read_file(str(self.dir / "absent.txt"))

    def test_mixed_newlines(self):
        side = compare.read_file(self.write("t.txt", b"a\r\nb\r\nc\n"))
        self.assertEqual((side["newline"], side["mixed"]), ("\r\n", True))


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_env.DATA))
        state = Path(self.dir / "compare.json")
        patcher = mock.patch.object(compare, "STATE_FILE", state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.left = self.dir / "contrat v1.txt"
        self.right = self.dir / "contrat v2.txt"
        self.left.write_bytes("\r\n".join(LEFT).encode("cp1252") + b"\r\n")
        self.right.write_text("\n".join(RIGHT) + "\n", encoding="utf-8")
        self.calls = []

    def service(self):
        chat = SimpleNamespace(available=True, models=["gemma4:e4b"], models_error="", failure=str, check=lambda: None)
        self.bridge = Bridge()
        svc = compare.CompareService(self.bridge, chat)
        self.addCleanup(svc.shutdown)
        return svc

    def loaded(self):
        svc = self.service()
        svc.load("left", str(self.left))
        state = svc.load("right", str(self.right))
        self.assertEqual(len(state["blocks"]), 2)
        return svc

    def test_choose_result_save_as(self):
        svc = self.loaded()
        svc.choose(0, "right")
        result = svc.result()
        self.assertEqual(result["unresolved"], 1)
        self.assertIn("14 novembre", result["text"])
        self.assertIn("Cordialement", result["text"])
        out = self.dir / "fusion.txt"
        saved = svc.save("as", result["text"], str(out))
        self.assertTrue(saved["ok"])
        # Format du fichier de gauche : Windows-1252, fins de ligne CRLF
        self.assertEqual(out.read_bytes(), result["text"].replace("\n", "\r\n").encode("cp1252"))

    def test_encoding_problem_reported(self):
        svc = self.loaded()
        out = self.dir / "fusion.txt"
        res = svc.save("as", "flèche → ici\n", str(out))
        self.assertFalse(res["ok"])
        self.assertTrue(res["encoding"])
        self.assertTrue(svc.save("as", "flèche → ici\n", str(out), utf8=True)["ok"])
        self.assertEqual(out.read_bytes(), "flèche → ici\r\n".encode("utf-8"))

    def test_replace_side_sends_old_version_to_recycle_bin(self):
        svc = self.loaded()
        svc.choose(0, "right")
        svc.choose(1, "right")
        recycled = []

        def fake_recycle(path, warn_permanent=False, owner=0):
            recycled.append(path)
            os.replace(path, path + ".corbeille")
            return True

        with mock.patch.object(compare, "recycle", side_effect=fake_recycle):
            res = svc.save("left", svc.result()["text"])
        self.assertTrue(res["ok"])
        self.assertEqual(recycled, [str(self.left)])
        self.assertEqual(res["state"]["blocks"], [])          # gauche relue : plus de différence
        self.assertEqual(self.left.read_bytes(), "\r\n".join(RIGHT).encode("cp1252") + b"\r\n")
        self.assertFalse([p for p in self.dir.iterdir() if p.suffix == ".tmp"])

    def test_choices_restored_next_session(self):
        svc = self.loaded()
        svc.choose(1, "left_right")
        svc.set_hint("La droite est plus récente.")
        svc.shutdown()
        again = self.service()
        state = again.state()
        self.assertEqual([b["choice"] for b in state["blocks"]], [None, "left_right"])
        self.assertEqual(state["hint"], "La droite est plus récente.")
        self.assertEqual(state["sides"]["left"]["encoding"], "Windows-1252")

    def test_choice_follows_block_when_file_changes_elsewhere(self):
        svc = self.loaded()
        svc.choose(1, "right")
        self.right.write_text("Titre\n" + "\n".join(RIGHT) + "\n", encoding="utf-8")
        os.utime(self.right, (1, 1))
        state = svc.state(check=True)
        self.assertEqual([b["choice"] for b in state["blocks"]], [None, None, "right"])

    def test_swap_mirrors_choices(self):
        svc = self.loaded()
        svc.choose(0, "right")
        svc.choose(1, "left_right")
        state = svc.swap()
        self.assertEqual(state["sides"]["left"]["name"], "contrat v2.txt")
        self.assertEqual([b["choice"] for b in state["blocks"]], ["left", "right_left"])

    def test_pasted_text_side(self):
        svc = self.service()
        svc.set_text("left", "a\r\nb\r\n", "Brouillon")
        state = svc.set_text("right", "a\nc\n")
        self.assertEqual(state["sides"]["left"]["name"], "Brouillon")
        self.assertFalse(state["sides"]["left"]["writable"])
        self.assertEqual(len(state["blocks"]), 1)
        with self.assertRaises(compare.UserError):
            svc.set_text("left", "   ")

    def test_ai_resolve(self):
        svc = self.loaded()
        answers = iter([
            {"raison": "Les deux apportent une information.", "choix": "fusion",
             "texte": "```\nLe rendez-vous est fixé au 14 novembre (et non le 12).\nUne ligne ajoutée.\n```"},
            {"raison": "Formule plus récente.", "choix": "fusion", "texte": "Bien à vous"},
        ])

        def fake_chat(label, priority=0, **kw):
            self.calls.append(kw)
            return reply(json.dumps(next(answers)))

        with mock.patch.object(compare.llm, "chat", side_effect=fake_chat):
            self.assertTrue(svc.resolve()["ok"])
            self.assertTrue(self.bridge.done.wait(5))
        state = svc.state()
        first, second = state["blocks"]
        self.assertEqual((first["choice"], first["by"]), ("text", "ia"))
        self.assertEqual(first["text"], "Le rendez-vous est fixé au 14 novembre (et non le 12).\nUne ligne ajoutée.")
        self.assertEqual(second["choice"], "right")              # « fusion » identique à la droite
        self.assertEqual(second["reason"], "Formule plus récente.")
        self.assertEqual(self.calls[0]["format"], compare.RESOLVE_SCHEMA)
        prompt = self.calls[0]["messages"][-1]["content"]
        self.assertIn("Différence 1 sur 2", prompt)
        self.assertIn("contrat v1.txt", prompt)
        self.assertIn("Le rendez-vous est fixé au 12 novembre.", prompt)

    def test_ai_resolve_error_and_user_choice_kept(self):
        svc = self.loaded()
        started = threading.Event()
        release = threading.Event()

        def fake_chat(label, priority=0, **kw):
            started.set()
            release.wait(2)
            return reply("pas du JSON")

        with mock.patch.object(compare.llm, "chat", side_effect=fake_chat):
            svc.resolve()
            self.assertTrue(started.wait(2))
            svc.choose(1, "left")                      # choisi à la main pendant que l'IA travaille
            release.set()
            self.assertTrue(self.bridge.done.wait(5))
        first, second = svc.state()["blocks"]
        self.assertEqual(first["ai"], {"state": "error", "error": "Réponse illisible du modèle."})
        self.assertIsNone(first["choice"])
        self.assertEqual((second["choice"], second["by"], second["ai"]), ("left", "user", None))

    def test_explain_prompt(self):
        svc = self.loaded()

        def fake_chat(label, priority=0, **kw):
            self.calls.append(kw)
            return iter([chunk("- Date : différence 1.")])

        with mock.patch.object(compare.llm, "chat", side_effect=fake_chat):
            self.assertTrue(svc.explain()["ok"])
            self.assertTrue(self.bridge.done.wait(5))
        content = self.calls[0]["messages"][-1]["content"]
        self.assertIn("## Différence 1 — ligne 2 à gauche, lignes 2 à 3 à droite", content)
        self.assertIn("2 différence(s)", content)
        self.assertEqual(svc.state()["thread"][0]["answer"], "- Date : différence 1.")

    def test_explain_needs_two_texts(self):
        svc = self.service()
        svc.load("left", str(self.left))
        self.assertIn("deux textes", svc.explain()["error"])


if __name__ == "__main__":
    unittest.main()
