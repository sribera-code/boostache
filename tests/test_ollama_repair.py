"""ollama_admin : réparation d'Ollama par Boostache (installateur repris dans
Téléchargements, signature exigée, raison d'un échec lue dans le journal)."""

from tests import _env  # noqa: F401  (données temporaires)

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ollama_admin

# Extrait du journal de la mise à jour ratée du 2026-11-07 (Inno Setup)
FAILED_LOG = """\
2026-11-07 08:21:43.794   Dest filename: C:\\Users\\x\\AppData\\Local\\Programs\\Ollama\\lib\\ollama\\rocm_v7_1\\amd_comgr_3.dll
2026-11-07 08:21:48.868   DeleteFile: The existing file appears to be in use (5). Retrying.
2026-11-07 08:21:52.928   Defaulting to Abort for suppressed message box (Abort/Retry/Ignore):
                          C:\\Users\\x\\AppData\\Local\\Programs\\Ollama\\lib\\ollama\\rocm_v7_1\\amd_comgr_3.dll

                          An error occurred while trying to replace the existing file:
                          DeleteFile failed; code 5.
                          Accès refusé.
2026-11-07 08:21:52.928   User canceled the installation process.
2026-11-07 08:21:52.928   Rolling back changes.
"""


class InstallFailureTest(unittest.TestCase):
    def test_file_in_use(self):
        self.assertEqual(ollama_admin.install_failure(FAILED_LOG),
                         "amd_comgr_3.dll est utilisé par un autre programme")

    def test_no_reason(self):
        self.assertEqual(ollama_admin.install_failure("Log opened.\nInstallation process succeeded.\n"), "")


class RecentInstallerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(ollama_admin, "downloads_dir", return_value=self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _file(self, name: str, age: float) -> Path:
        p = self.dir / name
        p.write_bytes(b"x")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_newest_recent_copy(self):
        self._file("OllamaSetup.exe", 3600)
        newest = self._file("OllamaSetup (1).exe", 60)
        self._file("Autre.exe", 1)
        self.assertEqual(ollama_admin.recent_installer(), newest)

    def test_too_old(self):
        self._file("OllamaSetup.exe", ollama_admin.INSTALLER_REUSE + 60)
        self.assertIsNone(ollama_admin.recent_installer())

    def test_none(self):
        self.assertIsNone(ollama_admin.recent_installer())


class SignatureTest(unittest.TestCase):
    def test_other_signer_refused(self):
        """python.exe (signé par la Python Software Foundation, ou pas du tout) : refusé."""
        self.assertFalse(ollama_admin.signed_by_ollama(Path(sys.executable)))

    def test_unsigned_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ pas un vrai programme")
        self.addCleanup(os.unlink, f.name)
        self.assertFalse(ollama_admin.signed_by_ollama(Path(f.name)))


if __name__ == "__main__":
    unittest.main()
