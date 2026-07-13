"""Regresion real: `jsa_historical_ingest.yml` asignaba
`env: JSA_HISTORICAL_DATABASE_URL: ${{ secrets.JSA_HISTORICAL_DATABASE_URL }}`
a nivel de job -- si el secret no existe, GitHub lo resuelve a cadena
VACIA y la exporta igual (la variable EXISTE con valor ""). `os.getenv(key,
default)` solo aplica el default cuando la variable esta AUSENTE, no
cuando esta vacia -- esto tumbo la primera corrida real de la ingesta
2022 (run 29260616665) con `sqlalchemy.exc.ArgumentError: Could not parse
SQLAlchemy URL from given URL string`.

Estos tests fijan el comportamiento correcto (`os.getenv(key) or default`)
para que esta clase de bug no pueda reintroducirse en silencio."""

from __future__ import annotations

import importlib


def test_database_url_falls_back_on_empty_env_var(monkeypatch):
    monkeypatch.setenv("JSA_DATABASE_URL", "")
    import jsa.config as config

    importlib.reload(config)
    try:
        assert config.DATABASE_URL == "sqlite:///jsa.db"
    finally:
        monkeypatch.delenv("JSA_DATABASE_URL", raising=False)
        importlib.reload(config)


def test_database_url_falls_back_when_env_var_absent(monkeypatch):
    monkeypatch.delenv("JSA_DATABASE_URL", raising=False)
    import jsa.config as config

    importlib.reload(config)
    assert config.DATABASE_URL == "sqlite:///jsa.db"


def test_database_url_respects_real_value(monkeypatch):
    monkeypatch.setenv("JSA_DATABASE_URL", "postgresql://user:pass@host/db")
    import jsa.config as config

    importlib.reload(config)
    try:
        assert config.DATABASE_URL == "postgresql://user:pass@host/db"
    finally:
        monkeypatch.delenv("JSA_DATABASE_URL", raising=False)
        importlib.reload(config)


def test_historical_database_url_falls_back_on_empty_env_var(monkeypatch):
    monkeypatch.setenv("JSA_HISTORICAL_DATABASE_URL", "")
    import jsa.historical.config as historical_config

    importlib.reload(historical_config)
    try:
        assert historical_config.HISTORICAL_DATABASE_URL == "sqlite:///jsa_historical.db"
    finally:
        monkeypatch.delenv("JSA_HISTORICAL_DATABASE_URL", raising=False)
        importlib.reload(historical_config)


def test_historical_database_url_falls_back_when_env_var_absent(monkeypatch):
    monkeypatch.delenv("JSA_HISTORICAL_DATABASE_URL", raising=False)
    import jsa.historical.config as historical_config

    importlib.reload(historical_config)
    assert historical_config.HISTORICAL_DATABASE_URL == "sqlite:///jsa_historical.db"
