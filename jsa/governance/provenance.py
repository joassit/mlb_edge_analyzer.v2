"""Provenance Graph -- Seccion 14.4 del spec JSA v3.0.

Grafo dirigido aciclico persistente, append-only: nunca se poda ni se
sobrescribe (mismo principio que el Experiment Registry). Cada corrida es
un nodo; el `Reconstruction Token` del JSAReport (Seccion 11.8) es la
clave de busqueda directa sobre este grafo.

Regla de propagacion (Seccion 15): si una corrida esta INVALIDATED, todo
nodo que dependa de ella como input hereda una ADVERTENCIA de invalidacion
propagada -- se muestra, nunca se invalida en cascada automaticamente ni se
oculta."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, MetaData, String, Table, select
from sqlalchemy.engine import Engine

from jsa.domain.hashing import hash_value
from jsa.domain.models import ProvenanceNode

metadata = MetaData()

provenance_nodes = Table(
    "provenance_nodes", metadata,
    Column("node_id", String, primary_key=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("inputs", JSON, nullable=False),
    Column("outputs", JSON, nullable=False),
    Column("hash", String, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("version", String, nullable=False),
    Column("parent_nodes", JSON, nullable=False, default=list),
    # Columna denormalizada (no forma parte del modelo Pydantic ProvenanceNode
    # del spec) -- solo para poder responder rapido "esta invalidado este
    # nodo" al chequear propagacion hacia nodos futuros, sin tener que
    # releer el RunManifest completo de cada nodo padre.
    Column("invalidated", Boolean, nullable=False, default=False),
)


def init_provenance(engine: Engine) -> None:
    metadata.create_all(engine)


def build_node(node_id: str, inputs: list[str], outputs: list[str], version: str, parent_nodes: list[str] | None = None) -> ProvenanceNode:
    timestamp = datetime.now(timezone.utc).isoformat()
    node_hash = hash_value({"node_id": node_id, "inputs": inputs, "outputs": outputs, "timestamp": timestamp, "version": version})
    return ProvenanceNode(
        node_id=node_id, inputs=inputs, outputs=outputs, hash=node_hash, timestamp=timestamp,
        version=version, parent_nodes=parent_nodes or [],
    )


def append_node(engine: Engine, node: ProvenanceNode, *, invalidated: bool) -> None:
    with engine.begin() as conn:
        conn.execute(
            provenance_nodes.insert().values(
                node_id=node.node_id, recorded_at=datetime.now(timezone.utc), inputs=node.inputs,
                outputs=node.outputs, hash=node.hash, timestamp=node.timestamp, version=node.version,
                parent_nodes=node.parent_nodes, invalidated=invalidated,
            )
        )


def check_propagated_warnings(engine: Engine, input_hashes: list[str]) -> list[str]:
    """Para cada hash de input que esta corrida declara usar, busca si
    proviene de un nodo ya marcado `invalidated=True` -- devuelve
    advertencias legibles, nunca invalida esta corrida automaticamente."""
    if not input_hashes:
        return []
    warnings: list[str] = []
    with engine.connect() as conn:
        rows = conn.execute(select(provenance_nodes).where(provenance_nodes.c.invalidated == True)).mappings().all()  # noqa: E712
    invalidated_output_hashes = {h for row in rows for h in (row["outputs"] or [])}
    for h in input_hashes:
        if h in invalidated_output_hashes:
            warnings.append(
                f"Advertencia de invalidacion propagada: el input con hash {h[:16]}... proviene de "
                f"un nodo del Provenance Graph marcado INVALIDATED. No se invalida esta corrida "
                f"automaticamente -- requiere revision."
            )
    return warnings
