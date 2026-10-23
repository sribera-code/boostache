"""reminders.py : échéances lues en français, moments proposés, rappels donnés (voix et
notification remplacées par des doublures : rien n'est dit ni affiché)."""

from tests import _env  # noqa: F401  (données temporaires)

import time
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import reminders


class DeadlineTest(unittest.TestCase):
    TODAY = date(2026, 10, 23)          # vendredi

    def test_day_month(self):
        self.assertEqual(reminders.deadline("à régler avant le 25 octobre.", self.TODAY), date(2026, 10, 25))
        self.assertEqual(reminders.deadline("le 1er décembre 2026", self.TODAY), date(2026, 12, 1))
        self.assertEqual(reminders.deadline("échéance au 3 fevrier", self.TODAY), date(2027, 2, 3))

    def test_numeric(self):
        self.assertEqual(reminders.deadline("à payer le 3/11", self.TODAY), date(2026, 11, 3))
        self.assertEqual(reminders.deadline("RDV le 05/01/27", self.TODAY), date(2027, 1, 5))

    def test_tomorrow_and_past(self):
        self.assertEqual(reminders.deadline("Rendez-vous demain à 14 h", self.TODAY), date(2026, 10, 24))
        self.assertIsNone(reminders.deadline("payé le 2 janvier 2025", self.TODAY))
        self.assertIsNone(reminders.deadline("rien ici, 84,30 euros", self.TODAY))
        self.assertIsNone(reminders.deadline("le 31/02", self.TODAY))

    def test_labels(self):
        today = self.TODAY
        self.assertEqual(reminders.when_label(datetime(2026, 10, 24, 9, 0), today), "demain à 9 h")
        self.assertEqual(reminders.when_label(datetime(2026, 11, 1, 18, 30), today), "dimanche 1er novembre à 18 h 30")
        self.assertEqual(reminders.day_label(datetime(2027, 1, 5), today), "mardi 5 janvier 2027")


class SuggestionsTest(unittest.TestCase):
    def test_with_deadline(self):
        now = datetime(2026, 10, 23, 10, 12)
        labels = [s["label"] for s in reminders.suggestions("Facture à régler avant le 25 octobre.", now)]
        self.assertEqual(labels[0], "Dans 1 heure")
        self.assertIn("Ce soir à 18 h", labels)
        self.assertIn("Demain matin (samedi, 9 h)", labels)
        self.assertIn("Le jour de l'échéance (dimanche 25 octobre, 9 h)", labels)
        self.assertIn("Lundi matin (9 h)", labels)      # un vendredi
        # La veille de l'échéance (samedi 9 h) = demain matin : pas en double
        ats = [s["at"] for s in reminders.suggestions("avant le 25 octobre", now)]
        self.assertEqual(len(ats), len(set(ats)))

    def test_evening_gone(self):
        now = datetime(2026, 10, 20, 17, 45)
        labels = [s["label"] for s in reminders.suggestions("", now)]
        self.assertNotIn("Ce soir à 18 h", labels)
        self.assertNotIn("Lundi matin (9 h)", labels)    # un mardi


class FakeBridge:
    def __init__(self):
        self.events = []

    def emit(self, name, data=None):
        self.events.append((name, data))


class ServiceTest(unittest.TestCase):
    def setUp(self):
        reminders.FILE.unlink(missing_ok=True)
        self.spoken = []
        speak = mock.patch.object(reminders.tts, "speak", side_effect=lambda text, **kw: self.spoken.append(text))
        quiet = mock.patch.object(type(reminders.tts), "speaking", new_callable=mock.PropertyMock, return_value=False)
        for p in (speak, quiet):
            p.start()
            self.addCleanup(p.stop)
        self.bridge = FakeBridge()
        self.svc = reminders.ReminderService(self.bridge)
        self.notes = []
        self.svc.notifier = lambda title, text: self.notes.append((title, text))
        self.addCleanup(self.svc.shutdown)

    def test_add_validates(self):
        self.assertFalse(self.svc.add("", datetime.now() + timedelta(hours=1))["ok"])
        self.assertFalse(self.svc.add("x", "demain")["ok"])
        self.assertFalse(self.svc.add("x", (datetime.now() - timedelta(hours=1)).isoformat())["ok"])
        res = self.svc.add("  Payer   la facture ", (datetime.now() + timedelta(hours=1)).isoformat(),
                           "EDF : 84,30 euros", {"kind": "assistant", "id": "f1"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["reminder"]["text"], "Payer la facture")
        self.assertEqual(self.bridge.events[-1][0], "reminders")

    def test_persisted(self):
        self.svc.add("Appeler Marie", (datetime.now() + timedelta(days=2)).isoformat())
        again = reminders.ReminderService(FakeBridge())
        self.assertEqual([r["text"] for r in again.snapshot()], ["Appeler Marie"])

    def test_fires_once_with_voice_and_notification(self):
        soon = datetime.now().replace(second=0, microsecond=0)        # minute en cours : dû tout de suite
        self.svc.add("Sortir le linge", soon.isoformat())
        self.svc.start()
        for _ in range(40):
            if self.spoken:
                break
            time.sleep(0.1)
        self.assertEqual(self.notes, [("Rappel", "Sortir le linge")])
        self.assertTrue(self.spoken[0].startswith("Rappel. Sortir le linge"))
        item = self.svc.snapshot()[0]
        self.assertTrue(item["done"])
        self.assertIn(("reminders:fired", mock.ANY), [(n, mock.ANY) for n, _ in self.bridge.events])
        time.sleep(0.3)
        self.assertEqual(len(self.notes), 1)

    def test_late_at_launch(self):
        res = self.svc.add("Arroser", (datetime.now() + timedelta(hours=1)).isoformat())
        rid = res["reminder"]["id"]
        with self.svc._lock:                     # Boostache fermé à l'heure prévue
            self.svc.items[0]["at"] = (datetime.now() - timedelta(hours=3)).isoformat(timespec="minutes")
        self.svc._changed()
        self.svc.start()
        for _ in range(40):
            if self.notes:
                break
            time.sleep(0.1)
        self.assertTrue(self.notes[0][0].startswith("Rappel en retard, prévu"))
        # Remis à plus tard : de nouveau à venir
        self.assertTrue(self.svc.snooze(rid, (datetime.now() + timedelta(minutes=10)).isoformat())["ok"])
        self.assertFalse(self.svc.snapshot()[0]["done"])

    def test_delete(self):
        rid = self.svc.add("A", (datetime.now() + timedelta(hours=1)).isoformat())["reminder"]["id"]
        self.assertTrue(self.svc.delete(rid))
        self.assertFalse(self.svc.delete(rid))
        self.assertEqual(self.svc.snapshot(), [])


if __name__ == "__main__":
    unittest.main()
