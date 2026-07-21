"""Administracion puntual de permisos de Postgres sobre el historico
compartido de `jsa/` -- NUNCA se corre contra SQLite (la ingesta local de
desarrollo no tiene roles), solo tiene sentido contra el Neon compartido
donde ya vive el rol de solo lectura `jsa_v2` que usa `JSA_V2_PROJECT`
(ver docs/scope_handoff.md de ese repo).

Se agrega esta pieza especifica (fuera de `jsa/historical/cli.py`, que es
para comandos de dominio -- ingesta/validacion/backfill -- no
administracion de roles) porque la configuracion original de `jsa_v2`
sobre `historical_game`/`historical_snapshot`/`historical_statcast_event`
se hizo a mano en Neon el 2026-07-19, fuera de version control en ambos
repos -- no hay ningun script existente para replicar el patron cuando se
agrega una tabla nueva (`historical_model_prediction`, 2026-07-21). Este
script asume que la conexion pasada por `--db` tiene privilegio
suficiente (dueno de la tabla o miembro de un rol con GRANT OPTION) --
si no lo tiene, Postgres devuelve un error claro (`InsufficientPrivilege`)
en vez de fallar en silencio."""

from __future__ import annotations

import argparse
import re

from sqlalchemy import create_engine, text

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, *, label: str) -> None:
    """Los nombres de rol/tabla nunca vienen de input de usuario final (son
    flags de un workflow_dispatch operado por el propio equipo), pero se
    valida igual antes de interpolar en el SQL -- nunca se acepta un
    identificador con comillas/punto-y-coma/espacios."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"{label} invalido: '{name}' -- solo letras/digitos/guion bajo, sin espacios ni comillas")


def grant_select(database_url: str, *, table: str, role: str) -> str:
    _validate_identifier(table, label="table")
    _validate_identifier(role, label="role")
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text(f'GRANT SELECT ON "{table}" TO "{role}"'))
    return f'GRANT SELECT ON "{table}" TO "{role}" -- ejecutado sin error'


def main() -> None:
    parser = argparse.ArgumentParser(description="Otorga SELECT sobre una tabla de jsa/historical/ a un rol de Postgres ya existente (nunca crea el rol).")
    parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica (debe ser Postgres real, con privilegio de GRANT sobre --table)")
    parser.add_argument("--table", required=True, help="Nombre de la tabla (debe existir ya, ej. historical_model_prediction)")
    parser.add_argument("--role", required=True, help="Rol de Postgres ya existente al que se le otorga SELECT (ej. jsa_v2)")
    args = parser.parse_args()

    result = grant_select(args.db, table=args.table, role=args.role)
    print(result)


if __name__ == "__main__":
    main()
