"""
Asegura que pytest encuentre los módulos del proyecto (data/, model/, db/, etc.)
sin importar desde qué carpeta se invoque `pytest`.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
