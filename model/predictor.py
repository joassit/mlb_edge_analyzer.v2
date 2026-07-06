"""
Punto único de cálculo de probabilidades a partir de insumos crudos.

`main.py::analyze_today()` lo usa en vivo, y cualquier recálculo histórico
que lea de `FeatureSnapshot` (ver tracking/backtest.py) lo usa exactamente
igual — así el pipeline en vivo y cualquier backtest futuro nunca pueden
desincronizarse en CÓMO se calcula una probabilidad a partir de los mismos
insumos. `raw_inputs` es el mismo dict que `db.database.save_feature_snapshot`
congela, así que un snapshot de hace meses se puede recalcular con esto sin
volver a golpear ninguna API.
"""

from config import PARK_FACTOR_WEIGHT, WEATHER_CORRECTION, NEGBIN_DISPERSION
from model.runs_projection import project_team_runs, LEAGUE_AVG_ERA
from model.probability import model_prob, normalize_matchup
from model.skellam_model import skellam_win_prob
from model.negbin_model import negbin_win_prob
from model.markets import run_line_prob, fair_total_line
from model.adjustments import shrunk_era


def predict_from_raw_inputs(raw: dict) -> dict:
    league_era = raw.get("league_era", LEAGUE_AVG_ERA)

    # PARK_FACTOR_WEIGHT/WEATHER_CORRECTION congelados en el snapshot en vez
    # de leídos en vivo de config.py -- si esas constantes cambian en el
    # futuro, un recálculo histórico de este juego debe seguir dando el
    # mismo resultado que dio el día que se generó la predicción real. Un
    # snapshot de antes de esta corrección no trae estas claves -- cae a
    # los valores actuales de config.py, igual que el shrinkage de ERA de
    # arriba cae al ERA crudo si el snapshot no trae innings_pitched.
    park_factor_weight = raw.get("park_factor_weight", PARK_FACTOR_WEIGHT)
    weather_correction = raw.get("weather_correction", WEATHER_CORRECTION)
    negbin_dispersion = raw.get("negbin_dispersion", NEGBIN_DISPERSION)

    # Shrinkage del ERA del abridor hacia el promedio de liga, en proporción
    # a las entradas lanzadas -- sin esto, un abridor con 15 IP de muestra
    # (ERA muy ruidoso) pesa igual que uno con 150 IP. Si el snapshot no
    # trae innings pitched (congelado antes de esta corrección), no se
    # aplica shrinkage y se usa el ERA crudo tal cual -- nunca debe romper
    # el recálculo de un snapshot viejo.
    away_ip = raw.get("away_innings_pitched")
    home_ip = raw.get("home_innings_pitched")
    away_era = shrunk_era(raw["away_era"], away_ip, league_era) if away_ip is not None else raw["away_era"]
    home_era = shrunk_era(raw["home_era"], home_ip, league_era) if home_ip is not None else raw["home_era"]

    away_mu = project_team_runs(
        raw["away_ops"], home_era, raw["away_bullpen_era"],
        raw["league_ops"], league_era, raw["park_factor"], raw["starter_weight"],
        is_home=False, temp_f=raw.get("temp_f"),
        park_factor_weight=park_factor_weight, weather_correction=weather_correction,
    )
    home_mu = project_team_runs(
        raw["home_ops"], away_era, raw["home_bullpen_era"],
        raw["league_ops"], league_era, raw["park_factor"], raw["starter_weight"],
        is_home=True, temp_f=raw.get("temp_f"),
        park_factor_weight=park_factor_weight, weather_correction=weather_correction,
    )

    away_p_raw = model_prob(
        away_era, raw["away_ops"], raw["league_ops"],
        bullpen_era=raw["away_bullpen_era"], starter_weight=raw["starter_weight"],
        k_pct=raw.get("away_k_pct"), bb_pct=raw.get("away_bb_pct"),
        days_rest=raw.get("away_days_rest"), last_outing_pitches=raw.get("away_last_outing_pitches"),
        park_factor=raw["park_factor"], temp_f=raw.get("temp_f"),
    )
    home_p_raw = model_prob(
        home_era, raw["home_ops"], raw["league_ops"],
        bullpen_era=raw["home_bullpen_era"], starter_weight=raw["starter_weight"],
        k_pct=raw.get("home_k_pct"), bb_pct=raw.get("home_bb_pct"),
        days_rest=raw.get("home_days_rest"), last_outing_pitches=raw.get("home_last_outing_pitches"),
        park_factor=raw["park_factor"], temp_f=raw.get("temp_f"),
    )
    away_model_prob, home_model_prob = normalize_matchup(
        away_p_raw, home_p_raw, raw.get("home_field_advantage", 0.0)
    )

    home_skellam_prob = skellam_win_prob(home_mu, away_mu)
    away_skellam_prob = 1.0 - home_skellam_prob

    home_negbin_prob = negbin_win_prob(home_mu, away_mu, negbin_dispersion)
    away_negbin_prob = 1.0 - home_negbin_prob

    home_covers_rl_prob, away_covers_rl_prob = run_line_prob(home_mu, away_mu)
    fair_total_runs = fair_total_line(home_mu, away_mu)

    return {
        "away_proj_runs": away_mu,
        "home_proj_runs": home_mu,
        "away_model_prob": away_model_prob,
        "home_model_prob": home_model_prob,
        "away_skellam_prob": away_skellam_prob,
        "home_skellam_prob": home_skellam_prob,
        "away_negbin_prob": away_negbin_prob,
        "home_negbin_prob": home_negbin_prob,
        "home_covers_rl_prob": home_covers_rl_prob,
        "away_covers_rl_prob": away_covers_rl_prob,
        "fair_total_runs": fair_total_runs,
    }
