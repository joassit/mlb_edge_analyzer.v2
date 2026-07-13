"""Permite `import jsa....` sin importar desde donde se invoque pytest --
inserta la raiz del repo (el padre de este directorio `jsa/`) en sys.path."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
