"""notes.py : notes enregistrées dans des fichiers (Enregistrer sous, Ouvrir), réécrites
dans leur encodage, fichiers modifiés ailleurs relus ou signalés (conflit), fichier disparu,
documents copiés dans une note, état retrouvé au démarrage suivant."""

from tests import _env  # noqa: F401  (données temporaires)

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import notes
from storage import NOTES_DIR, settings


def bump(path: Path, text: str | None = None, data: bytes | None = None):
    """Modification « ailleurs » : nouveau contenu (ou le même) et date changée à coup sûr."""
    st = path.stat()
    if text is not None or data is not None:
        path.write_bytes(data if data is not None else text.encode("utf-8"))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


class NotesFileTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(NOTES_DIR, ignore_errors=True)
        NOTES_DIR.mkdir(parents=True)
        self.dir = Path(tempfile.mkdtemp(prefix="boostache-notes-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        # Version écrasée : supprimée au lieu d'aller dans la vraie corbeille
        patcher = mock.patch.object(notes, "recycle", side_effect=lambda p: os.remove(p) or True)
        self.recycled = patcher.start()
        self.addCleanup(patcher.stop)
        settings.set("notes_file_autosave", True)
        self.svc = notes.NotesService()
        self.slot = self.svc.snapshot()["tabs"][0]["slot"]

    def test_save_as_then_every_save_writes_the_file(self):
        self.svc.save(self.slot, "Courses\nPain")
        self.assertEqual(self.svc.save_suggestion(self.slot)[1], "Courses.txt")
        ui = self.svc.save_as(self.slot, str(self.dir / "liste"))          # extension ajoutée
        path = self.dir / "liste.txt"
        self.assertEqual(ui["file"], {"path": str(path), "name": "liste.txt", "state": "", "error": "",
                                      "auto": True, "pending": False})
        self.assertEqual(ui["title"], "liste")
        self.assertEqual(path.read_bytes(), b"Courses\r\nPain")             # fins de ligne de Windows
        self.svc.save(self.slot, "Courses\nPain\nCafé")
        self.assertEqual(path.read_bytes(), "Courses\r\nPain\r\nCafé".encode("utf-8"))
        self.assertEqual(self.svc.save_suggestion(self.slot), (str(self.dir), "liste.txt"))

    def test_open_keeps_encoding_and_line_endings(self):
        path = self.dir / "ancien.txt"
        path.write_bytes("Été\nà Noël".encode("cp1252"))
        res = self.svc.open_paths([str(path)], reuse=self.slot)
        self.assertEqual(res["errors"], [])
        note = res["notes"][0]
        self.assertEqual((note["slot"], note["how"], note["content"]), (self.slot, "opened", "Été\nà Noël"))
        self.svc.save(self.slot, "Été\nà Noël\nfin")
        self.assertEqual(path.read_bytes(), "Été\nà Noël\nfin".encode("cp1252"))
        # Caractère absent de Windows-1252 : le fichier passe en UTF-8 plutôt que de le perdre
        self.svc.save(self.slot, "Été 🎄")
        self.assertEqual(path.read_bytes(), "Été 🎄".encode("utf-8"))
        # Déjà ouvert : la même note, pas une deuxième
        again = self.svc.open_paths([str(path)])
        self.assertEqual((again["notes"][0]["slot"], again["notes"][0]["how"]), (self.slot, "existing"))
        self.assertEqual(len(again["order"]), 1)

    def test_external_change_reloads_unchanged_note(self):
        path = self.dir / "a.txt"
        self.svc.save(self.slot, "un")
        self.svc.save_as(self.slot, str(path))
        self.assertEqual(self.svc.check(), [])
        bump(path, "un\r\ndeux")
        changed = self.svc.check()
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0]["reloaded"])
        self.assertEqual(changed[0]["content"], "un\ndeux")
        self.assertEqual(self.svc.check(), [])
        # La suite des modifications de la note repart dans le fichier
        self.svc.save(self.slot, "un\ndeux\ntrois")
        self.assertEqual(path.read_bytes(), b"un\r\ndeux\r\ntrois")

    def test_conflict_never_overwrites_without_a_choice(self):
        path = self.dir / "b.txt"
        self.svc.save(self.slot, "v1")
        self.svc.save_as(self.slot, str(path))
        bump(path, "version d'ailleurs")
        ui = self.svc.save(self.slot, "v1 modifiée")
        self.assertEqual(ui["file"]["state"], "conflict")
        self.assertEqual(path.read_text("utf-8"), "version d'ailleurs")
        # Modifications pas encore envoyées par l'interface : conflit aussi, pas de relecture
        self.assertEqual(self.svc.check(dirty=[self.slot]), [])
        self.assertEqual(self.svc.snapshot()["tabs"][0]["content"], "v1 modifiée")
        # La note gagne : l'autre version part à la corbeille
        ui = self.svc.write(self.slot)
        self.assertEqual(ui["file"]["state"], "")
        self.recycled.assert_called_once_with(str(path))
        self.assertEqual(path.read_text("utf-8"), "v1 modifiée")

    def test_conflict_resolved_for_the_file(self):
        path = self.dir / "c.txt"
        self.svc.save(self.slot, "v1")
        self.svc.save_as(self.slot, str(path))
        bump(path, "fichier")
        self.svc.save(self.slot, "note")
        ui = self.svc.reload(self.slot)
        self.assertEqual((ui["content"], ui["file"]["state"]), ("fichier", ""))
        self.recycled.assert_not_called()

    def test_touched_file_is_not_a_conflict(self):
        path = self.dir / "d.txt"
        self.svc.save(self.slot, "même texte")
        self.svc.save_as(self.slot, str(path))
        bump(path)                                  # date changée (synchronisation…), texte identique
        ui = self.svc.save(self.slot, "même texte, suite")
        self.assertEqual(ui["file"]["state"], "")
        self.assertEqual(path.read_text("utf-8"), "même texte, suite")

    def test_missing_file(self):
        path = self.dir / "sous" / "e.txt"
        path.parent.mkdir()
        self.svc.save(self.slot, "texte")
        self.svc.save_as(self.slot, str(path))
        shutil.rmtree(path.parent)
        changed = self.svc.check()
        self.assertEqual(changed[0]["file"]["state"], "missing")
        self.assertEqual(self.svc.save(self.slot, "texte 2")["file"]["state"], "missing")
        self.assertFalse(path.exists())             # rien de recréé sans le demander
        self.assertEqual(self.svc.write(self.slot)["file"]["state"], "")
        self.assertEqual(path.read_text("utf-8"), "texte 2")

    def test_restart_picks_up_changes_made_while_closed(self):
        path = self.dir / "f.txt"
        self.svc.save(self.slot, "avant")
        self.svc.save_as(self.slot, str(path))
        bump(path, "après")
        tab = notes.NotesService().snapshot()["tabs"][0]
        self.assertEqual((tab["content"], tab["file"]["state"], tab["title"]), ("après", "", "f"))

    def test_same_file_in_two_notes_refused(self):
        path = self.dir / "g.txt"
        self.svc.save(self.slot, "un")
        self.svc.save_as(self.slot, str(path))
        other = self.svc.new(self.slot)["slot"]
        with self.assertRaises(notes.UserError):
            self.svc.save_as(other, str(path))

    def test_unlink_keeps_note_and_file(self):
        path = self.dir / "Rendez-vous.txt"
        self.svc.save(self.slot, "lundi")
        self.svc.save_as(self.slot, str(path))
        ui = self.svc.unlink(self.slot)
        self.assertEqual((ui["file"], ui["title"]), (None, "Rendez-vous"))
        self.svc.save(self.slot, "mardi")
        self.assertEqual(path.read_text("utf-8"), "lundi")

    def test_open_errors_and_documents(self):
        binary = self.dir / "image.png"
        binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
        rtf = self.dir / "lettre.rtf"
        rtf.write_bytes(rb"{\rtf1\ansi Bonjour Julie\par}")
        res = self.svc.open_paths([str(binary), str(self.dir / "absent.txt"), str(rtf)], reuse=self.slot)
        self.assertEqual(len(res["errors"]), 2)
        note = res["notes"][0]
        self.assertEqual((note["how"], note["file"], note["label"]), ("imported", None, "lettre"))
        self.assertIn("Bonjour Julie", note["content"])

    def test_new_notes_follow_the_active_one(self):
        second = self.svc.new(self.slot)["slot"]
        self.svc.save(self.slot, "pas vide")
        a, b = self.dir / "a.md", self.dir / "b.md"
        a.write_text("A", "utf-8")
        b.write_text("B", "utf-8")
        res = self.svc.open_paths([str(a), str(b)], reuse=self.slot, after=self.slot)
        opened = [n["slot"] for n in res["notes"]]
        self.assertNotIn(self.slot, opened)         # la note active n'était pas vide
        self.assertEqual(res["order"], [self.slot, *opened, second])

    def test_manual_saving(self):
        path = self.dir / "manuel.txt"
        self.svc.save(self.slot, "v1")
        self.assertTrue(self.svc.save_as(self.slot, str(path))["file"]["auto"])
        ui = self.svc.set_auto(self.slot, False)
        self.assertEqual((ui["file"]["auto"], ui["file"]["pending"]), (False, False))
        # Note enregistrée dans Boostache seulement, jusqu'à Ctrl+S
        ui = self.svc.save(self.slot, "v2")
        self.assertTrue(ui["file"]["pending"])
        self.assertEqual(path.read_text("utf-8"), "v1")
        self.assertEqual(self.svc.check(), [])                          # rien d'écrit en passant
        self.assertEqual(path.read_text("utf-8"), "v1")
        ui = self.svc.save(self.slot, "v3", to_file=True)
        self.assertEqual((ui["file"]["pending"], path.read_text("utf-8")), (False, "v3"))
        # Redémarrage : les modifications en attente restent en attente, le choix aussi
        self.svc.save(self.slot, "v4")
        tab = notes.NotesService().snapshot()["tabs"][0]
        self.assertEqual((tab["content"], tab["file"]["auto"], tab["file"]["pending"]), ("v4", False, True))
        self.assertEqual(path.read_text("utf-8"), "v3")
        # Retour à l'automatique : ce qui attendait part tout de suite
        ui = self.svc.set_auto(self.slot, True)
        self.assertEqual((ui["file"]["pending"], path.read_text("utf-8")), (False, "v4"))

    def test_manual_choice_is_the_default_for_next_files(self):
        self.svc.save(self.slot, "a")
        self.svc.save_as(self.slot, str(self.dir / "a.txt"))
        self.svc.set_auto(self.slot, False)
        other = self.svc.new(self.slot)["slot"]
        self.assertFalse(self.svc.save_as(other, str(self.dir / "b.txt"))["file"]["auto"])
        (self.dir / "c.txt").write_text("c", "utf-8")
        self.assertFalse(self.svc.open_paths([str(self.dir / "c.txt")])["notes"][0]["file"]["auto"])

    def test_manual_note_still_follows_the_file(self):
        path = self.dir / "suivi.txt"
        self.svc.save(self.slot, "un")
        self.svc.save_as(self.slot, str(path))
        self.svc.set_auto(self.slot, False)
        bump(path, "un (modifié ailleurs)")
        self.assertTrue(self.svc.check()[0]["reloaded"])               # rien en attente : relu
        self.svc.save(self.slot, "deux")
        bump(path, "trois")
        self.assertEqual(self.svc.check()[0]["file"]["state"], "conflict")   # en attente : conflit
        self.assertEqual(self.svc.save(self.slot, "deux", to_file=True)["file"]["state"], "conflict")
        self.assertEqual(path.read_text("utf-8"), "trois")

    def test_default_filename(self):
        self.assertEqual(notes.default_filename('Réunion: "bilan"/2026…'), "Réunion_ _bilan__2026.txt")
        self.assertEqual(notes.default_filename(""), "Note.txt")


if __name__ == "__main__":
    unittest.main()
