"""Serializacion deterministica + SHA-256 (Seccion 14.2 del spec JSA v3.0).

Todo hash del sistema (snapshot_hash, config_hash, output_hash, hash de
ProvenanceNode) se calcula sobre la MISMA disciplina: claves ordenadas
alfabeticamente, floats con precision fija, sin espacios ambiguos,
timestamps ya en UTC ISO-8601 antes de llegar aqui. Esto es lo que permite
que un tercero recalcule el hash de forma independiente y lo compare
(Seccion 15, regla 3) sin tener que adivinar el formato exacto.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

_FLOAT_PRECISION = 9


def _normalize(value: Any) -> Any:
    """Convierte un valor a una forma JSON-serializable canonica y estable."""
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, float):
        # Precision fija: evita que 0.1 + 0.2 y 0.30000000000000004 hasheen distinto.
        return round(value, _FLOAT_PRECISION)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def deterministic_json(value: Any) -> str:
    """Serializacion canonica: claves ordenadas, sin espacios, floats fijos."""
    normalized = _normalize(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_value(value: Any) -> str:
    """Hashea cualquier valor (dict, modelo Pydantic, lista, escalar) de forma
    determinista. Punto unico de calculo para snapshot_hash/config_hash/
    output_hash/hash de ProvenanceNode -- nunca reimplementar esta logica en
    otro modulo (si difiere, la recomputacion independiente de la Seccion 15
    regla 3 dejaria de coincidir con el original por accidente, no por una
    alteracion real)."""
    return sha256_hex(deterministic_json(value))


def hash_model_excluding(model: BaseModel, *, exclude: set[str]) -> str:
    """Hashea un modelo Pydantic excluyendo campos dados (tipicamente el
    propio campo de hash, para poder calcularlo antes de asignarlo)."""
    dumped = model.model_dump(mode="python", exclude=exclude)
    return hash_value(dumped)
