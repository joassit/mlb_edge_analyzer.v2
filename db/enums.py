"""
Enums para los strings que antes eran "mágicos" en GameAnalysis/Bet/Pick
(ver auditoría externa, PASO 3). Todos heredan de (str, Enum) a propósito:
un MarketPriceSource.MANUAL compara igual, hashea igual y serializa igual
que el string "manual" ya persistido en filas existentes -- ninguna
columna de la base cambia de tipo (siguen siendo String), así que no hace
falta ninguna migración de datos. Filas viejas con el string crudo siguen
comparando correctamente contra estos enums.

NOTA: GameAnalysis.decision NO se convierte -- su propio comentario en
db/database.py lo documenta como "texto libre" (nota manual del operador),
no un conjunto fijo de estados, y no tiene ningún uso en el código más
allá de guardarse/leerse tal cual.
"""

from enum import Enum


class MarketPriceSource(str, Enum):
    API_LIVE = "api_live"
    API_CACHE = "api_cache"
    API_STALE_CACHE = "api_stale_cache"
    MANUAL = "manual"


class BetResult(str, Enum):
    PENDING = "pending"
    WIN = "win"
    LOSS = "loss"


class PickResult(str, Enum):
    PENDING = "pending"
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
