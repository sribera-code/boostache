"""python -m tests : lance tous les tests (rien d'affiché à l'écran, Ollama non requis)."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE.parent))
result = unittest.TextTestRunner(verbosity=1 if "-q" in sys.argv else 2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
