"""diagnostics.py : arrêt de Windows reconnu dans le journal Système (sortie de wevtutil
simulée), y compris avec le démarrage rapide (le noyau mis en veille prolongée)."""

from tests import _env  # noqa: F401  (données temporaires)

import subprocess
import time
import unittest
from unittest import mock

import diagnostics


def event(provider, event_id, target=None):
    data = f"<EventData><Data Name='TargetState'>{target}</Data></EventData>" if target is not None else ""
    return (f"<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='{provider}'/>"
            f"<EventID>{event_id}</EventID><TimeCreated SystemTime='2026-10-22T22:37:25.69Z'/></System>{data}</Event>")


class ShutdownTest(unittest.TestCase):
    def stopped(self, *events, code=0):
        diagnostics._stopped.clear()
        out = "".join(events).encode()
        with mock.patch.object(diagnostics.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], code, out, b"")) as run:
            result = diagnostics._windows_stopped_since("session.log", time.time() - 3600)
        return result, run.call_args[0][0]

    def test_fast_startup_shutdown(self):
        self.assertTrue(self.stopped(event("Microsoft-Windows-Kernel-Power", 42, target=6))[0])

    def test_sleep_is_not_a_shutdown(self):
        self.assertFalse(self.stopped(event("Microsoft-Windows-Kernel-Power", 42, target=4))[0])

    def test_restart_and_clean_shutdown(self):
        self.assertTrue(self.stopped(event("User32", 1074))[0])
        self.assertTrue(self.stopped(event("EventLog", 6006))[0])

    def test_nothing_or_failure(self):
        self.assertFalse(self.stopped()[0])
        self.assertFalse(self.stopped(event("User32", 1074), code=5)[0])

    def test_query_bounds(self):
        _, args = self.stopped()
        query = args[3]
        self.assertTrue(query.startswith("/q:*[System[TimeCreated[@SystemTime>='"))
        self.assertIn("@SystemTime<='", query)

    def test_cached_per_session(self):
        diagnostics._stopped.clear()
        with mock.patch.object(diagnostics.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            diagnostics._windows_stopped_since("a.log", 100.0)
            diagnostics._windows_stopped_since("a.log", 100.0)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
