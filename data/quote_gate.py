"""
Gate de validez y frescura para cuotas de mercado.

Una cuota con away_price/home_price fuera de rango (esquema roto, un precio
que llegó como string) o desactualizada (last_update viejo -- ej. después
de un scratch de pitcher, el evento que más mueve líneas en MLB) no debe
alimentar ni la selección de mejor cuota ni el picks engine: en
edge-hunting, una línea vieja casi siempre "parece" tener valor, porque el
mercado ya incorporó información que tu snapshot todavía no tiene -- es
indistinguible de un edge real en el propio tracking si no se filtra aquí.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

MAX_QUOTE_AGE = timedelta(minutes=5)


@dataclass(frozen=True)
class GatedQuote:
    book: str
    away_price: int
    home_price: int
    age: timedelta | None  # None = no se pudo determinar (last_update ausente/malformado)
    fresh: bool


def gate_quote(price: dict, now: datetime | None = None,
               max_age: timedelta = MAX_QUOTE_AGE) -> GatedQuote | None:
    """
    Valida tipo/rango de un price crudo de The Odds API y lo marca fresh/stale.

    Devuelve None si el precio es inválido de raíz (tipo incorrecto, o una
    cuota americana matemáticamente imposible -- |odds| < 100 no existe en
    formato americano) -- no hay nada usable ahí, ni para pick ni para CLV.

    Si last_update falta o no se puede parsear, se trata como fresh: no hay
    evidencia de que esté vieja, y bookmakers reales a veces no lo reportan
    de forma consistente -- rechazar por ausencia de dato sería más
    agresivo que rechazar por evidencia real de que la cuota es vieja.
    """
    try:
        away = int(price["away_price"])
        home = int(price["home_price"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (100 <= abs(away) <= 10000) or not (100 <= abs(home) <= 10000):
        return None

    last_update = price.get("last_update")
    if not last_update:
        return GatedQuote(price.get("book", "?"), away, home, age=None, fresh=True)

    try:
        lu = datetime.fromisoformat(str(last_update).replace("Z", "+00:00"))
        age = (now or datetime.now(timezone.utc)) - lu
    except (TypeError, ValueError):
        return GatedQuote(price.get("book", "?"), away, home, age=None, fresh=True)

    return GatedQuote(price.get("book", "?"), away, home, age=age, fresh=age <= max_age)
