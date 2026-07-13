"""Context Detector -- Seccion 5 del spec JSA v3.0.

Detecta unicamente hechos observables a partir de un `GameSnapshot`. Nunca
genera conclusiones, pesos ni advantages -- esa separacion es la Seccion 2
del spec ("Separacion critica, no negociable") y se hace cumplir aqui
devolviendo solo un `ContextSignals` con booleanos + explicaciones en
lenguaje natural, nada mas.
"""

from __future__ import annotations

from jsa import config
from jsa.domain.models import ContextSignals, GameSnapshot


def detect_context(snapshot: GameSnapshot) -> ContextSignals:
    explanations: list[str] = []

    home_ip = snapshot.home_starter_projected_ip
    away_ip = snapshot.away_starter_projected_ip
    avg_projected_ip = _avg(home_ip, away_ip)

    long_outing = avg_projected_ip is not None and avg_projected_ip >= config.LONG_OUTING_IP
    if long_outing:
        explanations.append(
            f"IP proyectada promedio de ambos abridores ({avg_projected_ip:.1f}) >= "
            f"{config.LONG_OUTING_IP} -- long outing esperado."
        )

    short_outing = avg_projected_ip is not None and avg_projected_ip <= config.SHORT_OUTING_IP
    if short_outing:
        explanations.append(
            f"IP proyectada promedio de ambos abridores ({avg_projected_ip:.1f}) <= "
            f"{config.SHORT_OUTING_IP} -- posible bullpen game."
        )

    bullpen_fatigue = any(
        v is not None and v > config.BULLPEN_FATIGUE_IP_3D
        for v in (snapshot.home_bullpen_ip_last_3_days, snapshot.away_bullpen_ip_last_3_days)
    )
    if bullpen_fatigue:
        explanations.append(f"Bullpen con mas de {config.BULLPEN_FATIGUE_IP_3D} IP en los ultimos 3 dias.")

    total_injuries = len(snapshot.home_key_injuries) + len(snapshot.away_key_injuries)
    key_offensive_injuries = total_injuries >= config.KEY_INJURIES_THRESHOLD
    if key_offensive_injuries:
        explanations.append(f"{total_injuries} lesiones ofensivas clave combinadas entre ambos equipos.")

    if snapshot.is_double_header:
        explanations.append("Doble cartelera.")

    extreme_travel = snapshot.travel_distance is not None and snapshot.travel_distance > config.EXTREME_TRAVEL_MILES
    if extreme_travel:
        explanations.append(f"Viaje > {config.EXTREME_TRAVEL_MILES} millas.")

    extreme_weather = _is_extreme_weather(snapshot)
    if extreme_weather:
        explanations.append("Clima extremo (temperatura o viento fuera de rango normal).")

    small_sample_offense = any(
        v is not None and v < config.SMALL_SAMPLE_OFFENSE_PA
        for v in (snapshot.home_ops_pa_sample, snapshot.away_ops_pa_sample)
    )
    if small_sample_offense:
        explanations.append(f"Muestra ofensiva < {config.SMALL_SAMPLE_OFFENSE_PA} PA para al menos un equipo.")

    return ContextSignals(
        long_outing=long_outing,
        short_outing_bullpen_game=short_outing,
        bullpen_fatigue=bullpen_fatigue,
        key_offensive_injuries=key_offensive_injuries,
        double_header=snapshot.is_double_header,
        extreme_travel=extreme_travel,
        extreme_weather=extreme_weather,
        small_sample_offense=small_sample_offense,
        explanations=explanations,
    )


def _avg(a: float | None, b: float | None) -> float | None:
    values = [v for v in (a, b) if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _is_extreme_weather(snapshot: GameSnapshot) -> bool:
    temp = snapshot.weather_temp_f
    wind = snapshot.weather_wind_speed
    if temp is not None and (temp < config.EXTREME_WEATHER_COLD_F or temp > config.EXTREME_WEATHER_HOT_F):
        return True
    if wind is not None and wind > config.EXTREME_WEATHER_WIND_MPH:
        return True
    return False
