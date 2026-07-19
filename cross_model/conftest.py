"""Permite `import jsa....` y `import cross_model....` sin importar desde
donde se invoque pytest -- inserta la raiz del repo (el padre de este
directorio `cross_model/`) en sys.path. Mismo patron que `jsa/conftest.py`."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
