"""
Obtiene el commit de git actual, para guardarlo junto con cada predicción.
Así, dentro de un año, puedes saber exactamente qué versión del código
generó una predicción específica.
"""

import subprocess
import os


def get_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return "unknown"
