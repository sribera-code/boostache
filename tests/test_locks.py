"""locks.py : processus qui retiennent un dossier (fichier ouvert, DLL chargée, dossier
courant, programme lancé depuis lui). Seuls des processus lancés par le test sont
examinés et terminés."""

from tests import _env  # noqa: F401  (données temporaires)

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import locks

NO_WINDOW = 0x08000000


class LocksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="locktest-", dir=_env.DATA)
        os.makedirs(os.path.join(cls.root, "sub", "deep"))
        cls.file = os.path.join(cls.root, "a.txt")
        with open(cls.file, "w") as f:
            f.write("x")
        pyd = glob.glob(os.path.join(os.path.dirname(sys.executable), "DLLs", "_bz2.pyd"))[0]
        shutil.copy(pyd, os.path.join(cls.root, "x.dll"))
        shutil.copy(os.path.join(os.environ["WINDIR"], "System32", "PING.EXE"), os.path.join(cls.root, "p.exe"))
        cls.holder = subprocess.Popen(
            [sys.executable, "-c", "import ctypes, sys, time; f = open(sys.argv[1]); ctypes.WinDLL(sys.argv[2]); "
             "time.sleep(120)", cls.file, os.path.join(cls.root, "x.dll")], creationflags=NO_WINDOW)
        cls.cmd = subprocess.Popen(["cmd", "/c", "ping -n 120 127.0.0.1 >nul"], cwd=os.path.join(cls.root, "sub", "deep"),
                                   creationflags=NO_WINDOW)
        cls.exe = subprocess.Popen([os.path.join(cls.root, "p.exe"), "-n", "120", "127.0.0.1"], cwd=_env.DATA,
                                   stdout=subprocess.DEVNULL, creationflags=NO_WINDOW)
        time.sleep(1.5)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["taskkill", "/f", "/t", "/pid", str(cls.cmd.pid)], capture_output=True)
        for p in (cls.holder, cls.cmd, cls.exe):
            try:
                p.kill()
                p.wait(5)
            except Exception:
                pass
        time.sleep(0.3)
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_folder_holders(self):
        res = locks.scan_local([self.root])
        uses = {p["pid"]: {u["kind"] for u in p["uses"]} for p in res["processes"]}
        self.assertIn(self.holder.pid, uses)
        self.assertIn(self.cmd.pid, uses)
        self.assertIn(self.exe.pid, uses)

    def test_file_only(self):
        res = locks.scan_local([self.file])
        self.assertEqual([p["pid"] for p in res["processes"] if p["pid"] in (self.cmd.pid, self.exe.pid)], [])
        self.assertIn(self.holder.pid, [p["pid"] for p in res["processes"]])

    def test_terminate_checks_start_time(self):
        target = next(p for p in locks.scan_local([self.file])["processes"] if p["pid"] == self.holder.pid)
        locks.terminate_local([{"pid": self.holder.pid, "start": str(int(target["start"]) + 1)}])
        time.sleep(0.3)
        self.assertIsNone(self.holder.poll())                 # autre processus (pid réattribué) : épargné
        locks.terminate_local([{"pid": self.holder.pid, "start": target["start"]}])
        self.holder.wait(5)
        self.assertIsNotNone(self.holder.poll())


if __name__ == "__main__":
    unittest.main()
