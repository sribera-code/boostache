"""api.py : filtres des fenêtres Ouvrir / Enregistrer acceptés par pywebview (un libellé
refusé empêche la fenêtre de s'ouvrir) ; noms de paramètres utilisables côté JS."""

from tests import _env  # noqa: F401  (données temporaires)

import inspect
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


class ParamNamesTest(unittest.TestCase):
    """pywebview crée pour chaque méthode une fonction JS dont les paramètres portent
    les noms Python : un paramètre « window » masque l'objet global et l'appel échoue
    (Cannot read properties of undefined (reading '_checkValue'))."""
    SHADOWED = {"window", "pywebview", "Promise", "Array", "arguments", "Math", "__id", "promise"}

    def test_no_param_shadows_js_globals(self):
        for name, fn in inspect.getmembers(api.Api, inspect.isfunction):
            if name.startswith("_"):
                continue
            params = set(inspect.getfullargspec(fn).args)
            with self.subTest(method=name):
                self.assertFalse(params & self.SHADOWED, sorted(params & self.SHADOWED))


if __name__ == "__main__":
    unittest.main()
