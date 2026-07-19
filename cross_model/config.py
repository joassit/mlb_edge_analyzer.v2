"""Configuracion de `cross_model` -- nombre de variable de entorno propio,
nunca comparte namespace con `DATABASE_URL`/`HISTORICAL_DATABASE_URL`
(legado) ni `JSA_DATABASE_URL`/`JSA_HISTORICAL_DATABASE_URL` (JSA). En
produccion, las 5 variables pueden apuntar a la MISMA instancia fisica de
Postgres (eso es lo que permite cruzar resultados con SQL directo) --
pero cada una controla una tabla/namespace distinto, nunca se asume que
comparten valor por default."""

from __future__ import annotations

import os

UNIFIED_DATABASE_URL = os.getenv("UNIFIED_DATABASE_URL") or "sqlite:///unified_model_predictions.db"
