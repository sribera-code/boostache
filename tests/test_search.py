"""search.py : repli des accents, extraits, recherche dans les sections (services factices)."""

from tests import _env  # noqa: F401  (données temporaires)

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import search
from storage import clipboard_history


class FoldTest(unittest.TestCase):
    def test_accents_and_case(self):
        self.assertEqual(search.fold("Électricité DE FRANCE, Noël"), "electricite de france, noel")

    def test_same_length(self):
        text = "Œuvre à l’été\u00a0: «\u202fça\u202f» — Ça va\nbien"
        self.assertEqual(len(search.fold(text)), len(text))

    def test_words(self):
        self.assertEqual(search.words_of("Facture  EDF d'octobre"), ["facture", "edf", "octobre"])
        self.assertEqual(search.words_of("x"), ["x"])          # une lettre seule, s'il n'y a qu'elle
        self.assertEqual(search.words_of("  "), [])

    def test_snippet_marks(self):
        text = "Bonjour,\nvotre facture d'électricité d'octobre est disponible. " + "Suite. " * 40
        part = search.snippet(text, search.fold(text), ["electricite"])
        self.assertIn("électricité", part["text"])
        self.assertNotIn("\n", part["text"])
        a, b = part["marks"][0]
        self.assertEqual(part["text"][a:b], "électricité")
        self.assertTrue(part["text"].endswith("…"))

    def test_marks_merged(self):
        self.assertEqual(search.marks("Paris-Lyon", ["paris", "ris-l"]), [[0, 7]])


def fake_app():
    chat_tabs = [
        SimpleNamespace(id="t1", title="Voyage", messages=[
            {"role": "user", "content": "Quel train pour Lyon ?"},
            {"role": "assistant", "content": "Le TGV de 8 h 04 arrive à Lyon Part-Dieu à 10 h."}]),
        SimpleNamespace(id="t2", title="Recette", messages=[{"role": "user", "content": "Une tarte aux pommes"}]),
    ]
    rag = mock.Mock()
    rag.search.return_value = {"ok": True, "files": [], "passages": [
        {"name": "Contrat.pdf", "loc": "p. 3", "path": r"C:\Docs\Contrat.pdf", "chunk": 7,
         "text": "Le loyer est payable avant le 5 de chaque mois, à Lyon."}]}
    recorder = mock.Mock()
    recorder.files.return_value = []
    return SimpleNamespace(
        chat=SimpleNamespace(_lock=threading.Lock(), tabs=chat_tabs),
        notes=SimpleNamespace(_lock=threading.Lock(), _notes=[
            SimpleNamespace(slot=1, title="Courses", content="lait, œufs\npain"),
            SimpleNamespace(slot=2, title="Lyon", content="Musées à voir à Lyon : Confluences")]),
        assistant=SimpleNamespace(_lock=threading.Lock(), feed=[
            {"id": "f1", "source": "gmail", "title": "EDF", "text": "Votre facture d'octobre",
             "summary": "Facture de 84,30 euros à régler avant le 25 octobre.", "action": "Payer la facture",
             "at": "2026-10-23T10:00:00"}]),
        calc=SimpleNamespace(entries=[{"n": 1, "code": "ttc(120)", "result": "144.0"},
                                      {"n": 2, "code": "jour(date(2026, 12, 25))", "result": "'vendredi'"}]),
        recorder=recorder, rag=rag)


class SearchServiceTest(unittest.TestCase):
    def setUp(self):
        self.svc = search.SearchService(fake_app())
        patcher = mock.patch.object(search.custom_tasks, "rows", return_value=[
            {"idx": 0, "label": "Sauvegarde du soir", "period": "chaque jour à 22:00", "action_code": "backup()"}])
        patcher.start()
        self.addCleanup(patcher.stop)

    def groups(self, query):
        return {g["section"]: g["items"] for g in self.svc.search(query)["groups"]}

    def test_all_words_required(self):
        found = self.groups("lyon tgv")
        self.assertEqual([it["target"]["tab"] for it in found["chat"]], ["t1"])
        self.assertNotIn("notes", found)

    def test_message_to_show(self):
        it = self.groups("part-dieu")["chat"][0]
        self.assertEqual(it["target"], {"tab": "t1", "message": 1})
        a, b = it["marks"][0]
        self.assertEqual(it["snippet"][a:b].lower(), "part")

    def test_title_ranks_first(self):
        notes = self.groups("lyon")["notes"]
        self.assertEqual(notes[0]["target"]["slot"], 2)
        self.assertEqual(notes[0]["title_marks"], [[0, 4]])

    def test_note_position(self):
        it = self.groups("pain")["notes"][0]
        self.assertEqual(it["target"], {"slot": 1, "pos": len("lait, œufs\n")})

    def test_accent_insensitive_feed(self):
        it = self.groups("regler facture")["assistant"][0]
        self.assertEqual(it["target"], {"id": "f1"})
        self.assertTrue(it["title"].startswith("Gmail"))

    def test_calc_and_tasks(self):
        self.assertEqual(self.groups("vendredi")["calc"][0]["target"], {"n": 2})
        self.assertEqual(self.groups("sauvegarde")["tasks"][0]["target"], {"idx": 0})

    def test_documents_lexical_only(self):
        found = self.groups("loyer")
        self.assertEqual(found["rag"][0]["title"], "Contrat.pdf · p. 3")
        self.svc._app.rag.search.assert_called_with("loyer", None, semantic=False)

    def test_clipboard(self):
        clipboard_history.add("Numéro de suivi : 6A123456789")
        it = self.groups("numero suivi")["clipboard"][0]
        self.assertIn("6A123456789", it["snippet"])

    def test_failing_source_ignored(self):
        self.svc._app.rag.search.side_effect = RuntimeError("index fermé")
        self.assertIn("chat", self.groups("lyon"))

    def test_empty_query(self):
        self.assertEqual(self.svc.search("  ")["groups"], [])


if __name__ == "__main__":
    unittest.main()
