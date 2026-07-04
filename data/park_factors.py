"""
Park factors y coordenadas de estadios de MLB.

IMPORTANTE: estos valores son aproximados y deben actualizarse 1-2 veces por
temporada desde una fuente publicada (ej. FanGraphs Park Factors,
baseballsavant). No cambian partido a partido, así que no vale la pena
consultarlos por API cada vez — se mantienen aquí como tabla estática.

park_factor: 1.00 = neutral. >1.00 favorece a los bateadores (más runs de lo
normal). <1.00 favorece a los pitchers.

Las coordenadas se usan para consultar el clima del estadio (ver weather.py).
"""

# team_id (MLB Stats API) -> datos del estadio local de ese equipo
BALLPARKS = {
    108: {"name": "Angel Stadium", "park_factor": 0.98, "lat": 33.8003, "lon": -117.8827},
    109: {"name": "Chase Field", "park_factor": 1.02, "lat": 33.4453, "lon": -112.0667},
    110: {"name": "Oriole Park at Camden Yards", "park_factor": 1.00, "lat": 39.2838, "lon": -76.6217},
    111: {"name": "Fenway Park", "park_factor": 1.06, "lat": 42.3467, "lon": -71.0972},
    112: {"name": "Wrigley Field", "park_factor": 1.02, "lat": 41.9484, "lon": -87.6553},
    113: {"name": "Great American Ball Park", "park_factor": 1.08, "lat": 39.0975, "lon": -84.5074},
    114: {"name": "Progressive Field", "park_factor": 0.97, "lat": 41.4962, "lon": -81.6852},
    115: {"name": "Coors Field", "park_factor": 1.18, "lat": 39.7559, "lon": -104.9942},
    116: {"name": "Comerica Park", "park_factor": 0.96, "lat": 42.3390, "lon": -83.0485},
    117: {"name": "Daikin Park", "park_factor": 0.97, "lat": 29.7573, "lon": -95.3555},
    118: {"name": "Kauffman Stadium", "park_factor": 0.98, "lat": 39.0517, "lon": -94.4803},
    119: {"name": "Dodger Stadium", "park_factor": 0.95, "lat": 34.0739, "lon": -118.2400},
    120: {"name": "Nationals Park", "park_factor": 1.00, "lat": 38.8730, "lon": -77.0074},
    121: {"name": "Citi Field", "park_factor": 0.96, "lat": 40.7571, "lon": -73.8458},
    133: {"name": "Sutter Health Park", "park_factor": 1.00, "lat": 38.5802, "lon": -121.5142},
    134: {"name": "PNC Park", "park_factor": 0.97, "lat": 40.4469, "lon": -80.0057},
    135: {"name": "Petco Park", "park_factor": 0.92, "lat": 32.7073, "lon": -117.1566},
    136: {"name": "T-Mobile Park", "park_factor": 0.93, "lat": 47.5914, "lon": -122.3325},
    137: {"name": "Oracle Park", "park_factor": 0.90, "lat": 37.7786, "lon": -122.3893},
    138: {"name": "Busch Stadium", "park_factor": 0.97, "lat": 38.6226, "lon": -90.1928},
    139: {"name": "George M. Steinbrenner Field", "park_factor": 1.00, "lat": 27.9800, "lon": -82.5066},
    140: {"name": "Globe Life Field", "park_factor": 1.01, "lat": 32.7473, "lon": -97.0842},
    141: {"name": "Rogers Centre", "park_factor": 1.02, "lat": 43.6414, "lon": -79.3894},
    142: {"name": "Target Field", "park_factor": 0.98, "lat": 44.9817, "lon": -93.2776},
    143: {"name": "Citizens Bank Park", "park_factor": 1.07, "lat": 39.9061, "lon": -75.1665},
    144: {"name": "Truist Park", "park_factor": 1.01, "lat": 33.8908, "lon": -84.4678},
    145: {"name": "Rate Field", "park_factor": 1.00, "lat": 41.8299, "lon": -87.6338},
    146: {"name": "loanDepot park", "park_factor": 0.93, "lat": 25.7781, "lon": -80.2196},
    147: {"name": "Yankee Stadium", "park_factor": 1.05, "lat": 40.8296, "lon": -73.9262},
    158: {"name": "American Family Field", "park_factor": 1.02, "lat": 43.0280, "lon": -87.9712},
}

LEAGUE_AVG_PARK_FACTOR = 1.00


def get_park_info(home_team_id: int) -> dict:
    """Devuelve park_factor + coordenadas del estadio local. Cae a valores
    neutrales si el equipo no está en la tabla (ej. IDs no vigentes)."""
    return BALLPARKS.get(home_team_id, {
        "name": "Desconocido",
        "park_factor": LEAGUE_AVG_PARK_FACTOR,
        "lat": None,
        "lon": None,
    })
