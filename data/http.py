"""
Sesión HTTP compartida con reintentos automáticos para la MLB Stats API.

Mismo patrón que ya usaba data/weather.py para Open-Meteo -- antes de esto,
un timeout transitorio en data/mlb_api.py o data/stats.py mataba esa
consulta de una vez; ahora reintenta con backoff exponencial antes de
rendirse. Un solo módulo para no repetir la configuración de Retry en cada
archivo que golpea la MLB Stats API.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504]
)))
