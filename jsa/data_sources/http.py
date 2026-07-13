"""Sesion HTTP compartida con reintentos automaticos para la MLB Stats API.

Mismo patron probado en `mlb_edge_analyzer.v2/data/http.py`: un timeout
transitorio no debe matar la consulta de una vez, reintenta con backoff
exponencial antes de rendirse. Modulo unico para no repetir la
configuracion de `Retry` en cada archivo que golpea la MLB Stats API.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
session.mount(
    "https://",
    HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])),
)
