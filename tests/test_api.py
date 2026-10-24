"""api.py : filtres des fenêtres Ouvrir / Enregistrer acceptés par pywebview (un libellé
refusé empêche la fenêtre de s'ouvrir)."""

from tests import _env  # noqa: F401  (données temporaires)

import unittest

from webview.util import parse_file_type

import api


class FileTypesTest(unittest.TestCase):
    def test_every_filter_is_valid(self):
        names = [n for n in dir(api) if n.endswith("_TYPES") and isinstance(getattr(api, n), tuple)]
        self.assertIn("NOTE_OPEN_TYPES", names)
        for name in names:
            for file_type in getattr(api, name):
                with self.subTest(name=name, file_type=file_type[:40]):
                    parse_file_type(file_type)


if __name__ == "__main__":
    unittest.main()
