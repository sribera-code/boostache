"""calc.py : interpréteur à part, interruption, sessions rejouées, calcul modifié sur place
(lui et les suivants recalculés), code de l'assistant refusé s'il sort des calculs."""

from tests import _env  # noqa: F401  (données temporaires)

import threading
import time
import unittest

import calc


def results(c):
    return [(e["code"], e.get("result")) for e in c.entries]


class CalcTest(unittest.TestCase):
    def setUp(self):
        calc.CALC_FILE.unlink(missing_ok=True) if hasattr(calc, "CALC_FILE") else None
        self.c = calc.CalcService()
        self.c.clear()
        self.addCleanup(self.c.shutdown)

    def test_result_and_helpers(self):
        self.assertEqual(self.c.run("x = 6*7\nx")["entry"]["result"], "42")
        self.assertEqual(self.c.run("np.mean([1, 2, 3])")["entry"]["result"], "2.0")
        self.assertEqual(self.c.run("ttc(100)")["entry"]["result"], "120.0")

    def test_interrupt_keeps_nothing_running(self):
        res = {}
        th = threading.Thread(target=lambda: res.update(self.c.run("while True: pass")))
        th.start()
        time.sleep(1)
        self.assertFalse(self.c.run("1 + 1")["ok"])            # un calcul à la fois
        self.c.interrupt()
        th.join(10)
        self.assertTrue(res["entry"].get("interrupted") or res["entry"].get("error"))
        self.assertEqual(self.c.run("2 + 2")["entry"]["result"], "4")

    def test_replay_restores_variables(self):
        self.c.run("x = 6*7")
        session = self.c.entries[-1]["session"]
        self.c.reset()
        self.assertIn("NameError", self.c.run("x")["entry"].get("error", ""))
        self.c.replay(session)
        self.assertEqual(self.c.run("x")["entry"]["result"], "42")

    def test_recompute_chain(self):
        for code in ["x = 1", "y = x + 1", "x += 10", "x * 100", "y"]:
            self.c.run(code)
        n = self.c.entries[0]["n"]
        self.assertTrue(self.c.recompute(n + 1, "y = x * 5")["ok"])
        self.assertEqual([r for _, r in results(self.c)][3:], ["1100", "5"])
        self.c.recompute(n, "x = 2")
        self.assertEqual([r for _, r in results(self.c)][3:], ["1200", "10"])

    def test_recompute_timeout_then_fix(self):
        r = self.c.run("while True: pass", timeout=2)
        self.assertTrue(r["entry"].get("error"))
        fixed = self.c.recompute(r["entry"]["n"], "sum(range(10))")
        self.assertTrue(fixed["ok"])
        self.assertEqual(self.c.entries[-1]["result"], "45")

    def test_unsafe(self):
        self.assertTrue(calc.unsafe("import os"))
        self.assertTrue(calc.unsafe("open('a')"))
        self.assertTrue(calc.unsafe("np.load('x')"))
        self.assertTrue(calc.unsafe("().__class__"))
        self.assertFalse(calc.unsafe("import math\nsqrt(2)"))
        self.assertFalse(calc.unsafe("from numpy import array"))


if __name__ == "__main__":
    unittest.main()
