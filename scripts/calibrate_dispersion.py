"""
Calibra el parámetro de dispersión k de model/negbin_model.py (varianza =
mu + mu^2/k) por máxima verosimilitud contra resultados reales guardados en
la base de datos.

Compara, juego por juego, las carreras proyectadas por equipo
(GameAnalysis.away_proj_runs / home_proj_runs -- el mismo mu que ya
alimenta al Skellam) contra las carreras reales anotadas
(ActualResult.away_score / home_score). Cada equipo-juego es una
observación independiente con su propio mu y el mismo k compartido -- así
se calibra un solo k global, no uno por equipo (igual que lo usa
negbin_win_prob).

Con pocos juegos, la estimación de k es ruidosa (k mide sobre-dispersión, y
eso requiere varianza observada confiable, que a su vez requiere muestra
grande). Por debajo de ~100 observaciones el script se niega a proponer un
valor calibrado y en vez de eso reporta el prior de literatura sabermétrica
ya fijado en config.NEGBIN_DISPERSION (k entre 5 y 10 para carreras por
equipo por juego en MLB).

Este script NO escribe en config.py automáticamente -- solo imprime el k
recalibrado y cuántas observaciones lo respaldan. Actualizar
NEGBIN_DISPERSION es una decisión manual: revisa el resultado, y si tiene
sentido, pega el valor nuevo en config.py junto con la fecha y el número de
juegos usados (mismo criterio que PARK_FACTOR_WEIGHT/WEATHER_CORRECTION,
que tampoco se auto-ajustan).

Uso:
    python scripts/calibrate_dispersion.py
"""

from datetime import date

from scipy.optimize import minimize_scalar
from scipy.stats import nbinom

from config import NEGBIN_DISPERSION
from db.database import SessionLocal, GameAnalysis, ActualResult

MIN_OBSERVATIONS_FOR_CALIBRATION = 100
K_SEARCH_BOUNDS = (0.5, 200.0)


def _collect_observations() -> list[tuple[float, int]]:
    """
    Una tupla (mu, carreras_reales) por lado (visitante y local) de cada
    juego con resultado -- dos observaciones por juego, cada una con su
    propio mu, para no promediar away/home entre sí antes de ajustar.
    """
    session = SessionLocal()
    try:
        rows = (
            session.query(GameAnalysis, ActualResult)
            .join(ActualResult, GameAnalysis.game_pk == ActualResult.game_pk)
            .filter(GameAnalysis.away_proj_runs.isnot(None), GameAnalysis.home_proj_runs.isnot(None))
            .all()
        )
    finally:
        session.close()

    observations = []
    for pred, result in rows:
        observations.append((pred.away_proj_runs, result.away_score))
        observations.append((pred.home_proj_runs, result.home_score))
    return observations


def _neg_log_likelihood(k: float, observations: list[tuple[float, int]]) -> float:
    total = 0.0
    for mu, runs in observations:
        mu = max(mu, 1e-6)
        p = k / (k + mu)
        total -= nbinom.logpmf(runs, k, p)
    return total


def calibrate() -> dict:
    observations = _collect_observations()
    n = len(observations)

    if n < MIN_OBSERVATIONS_FOR_CALIBRATION:
        print(f"Solo {n} observación(es) equipo-juego con resultado real "
              f"(se necesitan >= {MIN_OBSERVATIONS_FOR_CALIBRATION} para una estimación de k confiable).")
        print(f"Se mantiene el prior de literatura sabermétrica ya fijado en config.py: "
              f"NEGBIN_DISPERSION = {NEGBIN_DISPERSION}")
        return {"k": NEGBIN_DISPERSION, "n_observations": n, "calibrated": False}

    result = minimize_scalar(
        _neg_log_likelihood, args=(observations,), bounds=K_SEARCH_BOUNDS, method="bounded",
    )
    k_hat = result.x

    print(f"Calibración por máxima verosimilitud sobre {n} observación(es) equipo-juego "
          f"({n // 2} juego(s) con resultado).")
    print(f"k estimado: {k_hat:.2f}  (valor anterior en config.py: {NEGBIN_DISPERSION})")
    print(f"\nPara aplicarlo, actualiza NEGBIN_DISPERSION en config.py a {k_hat:.2f} y anota "
          f"la fecha ({date.today().strftime('%Y-%m-%d')}) y n={n} observaciones en el comentario.")

    return {"k": k_hat, "n_observations": n, "calibrated": True}


if __name__ == "__main__":
    calibrate()
