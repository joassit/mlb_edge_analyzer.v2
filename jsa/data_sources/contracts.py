"""Contrato de validacion de esquema para respuestas de APIs externas.

Un cambio de forma en la respuesta de una API (campo renombrado, nivel de
anidacion distinto) no debe tumbar el analisis completo del dia. `require()`
camina el payload nivel por nivel y devuelve un `SchemaError` con el path
exacto que falto, en vez de dejar que un KeyError/TypeError generico se
propague sin contexto (mismo patron probado en
`mlb_edge_analyzer.v2/data/contracts.py`)."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaError:
    context: str
    path: str

    def __str__(self) -> str:
        return f"{self.context}: falta '{self.path}' en la respuesta de la API"


def require(payload: dict, path: list[str], context: str) -> Any | SchemaError:
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return SchemaError(context=context, path=".".join(path))
        node = node[key]
    return node
