"""Commun aux tests, importé en premier par chacun : les données de Boostache vont
dans un dossier temporaire (jamais celles de l'utilisateur), le dépôt est dans
sys.path."""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="boostache-tests-"))
os.environ["BOOSTACHE_DATA_DIR"] = str(DATA)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
atexit.register(shutil.rmtree, DATA, ignore_errors=True)
