"""
Contrato de validación de esquema para respuestas de APIs externas.

Un cambio de forma en la respuesta de la MLB Stats API (campo renombrado,
nivel de anidación distinto) no debe tumbar el análisis completo del día.
`require()` camina el payload nivel por nivel y devuelve un `SchemaError`
con el path exacto que faltó, en vez de dejar que un KeyError/TypeError
genérico se propague sin contexto y aborte el resto de los juegos.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaError:
    context: str
    path: str

    def __str__(self) -> str:
        return f"{self.context}: falta '{self.path}' en la respuesta de la API"


def require(payload: dict, path: list[str], context: str) -> Any | SchemaError:
    """Devuelve el valor en `path` dentro de `payload`, o un SchemaError si
    algún nivel intermedio no existe o no es un dict."""
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return SchemaError(context=context, path=".".join(path))
        node = node[key]
    return node
