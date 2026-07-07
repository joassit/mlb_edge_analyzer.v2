"""
Verifica main._mu_family_agrees_internally() contra las funciones REALES
(skellam_win_prob/negbin_win_prob), no probabilidades inventadas a mano
como en tests/test_main_pipeline.py -- esas prueban que la lógica
booleana es correcta, esta prueba que la situación que detecta (Skellam
y NB2 discrepando en dirección) es alcanzable -- o no -- con mu/k reales.

Nota sobre tipos: skellam_win_prob/negbin_win_prob devuelven np.float64,
así que _mu_family_agrees_internally puede devolver np.True_/np.False_
(numpy bool), no el singleton Python `True`/`False` -- comparar con
`is True`/`is False` falla aunque el valor sea correcto (son objetos
distintos). Las aserciones de aquí usan verdad/falsedad (`assert x` /
`assert not x`), no identidad.

Conclusión verificada (ver docstring de _mu_family_agrees_internally en
main.py para el detalle completo, incluida la corrección de una
investigación previa que comparaba mal el lado "away"): con k=7.0, o
cualquier k razonable para carreras de MLB, la discrepancia de dirección
es prácticamente INALCANZABLE para mu_home != mu_away -- un barrido de
202,500 combinaciones (rango 1.0-10.0, paso 0.02) con el lado away
calculado correctamente (away_prob = 1 - home_prob, igual que
model/predictor.py) dio CERO discrepancias, incluso forzando k hasta 0.01.

El único disparador real es el empate EXACTO (mu_home == mu_away bit a
bit) -- y ni siquiera ahí es universal: de 450 valores de mu probados en
empate exacto, solo ~133 (≈30%) dispararon el artefacto. La razón es que
la renormalización de scipy.stats.skellam.cdf dentro de skellam_win_prob
deja un residuo de punto flotante (~2e-16) cuya DIRECCIÓN (positiva o
negativa) depende del valor específico de mu -- no es un sesgo
consistente hacia un lado. negbin_win_prob, en cambio, da 0.5 exacto en
todo empate (su suma truncada no arrastra ese residuo). Cuando el residuo
de Skellam cae del lado que cruza el 0.5 tras el complemento (1-home), se
ve como discrepancia; cuando cae del otro lado, no. mu=1.0 es un ejemplo
reproducible y determinista de un empate que sí la dispara.
"""

import config
from main import _mu_family_agrees_internally
from model.skellam_model import skellam_win_prob
from model.negbin_model import negbin_win_prob

# Muestra representativa de (mu_home, mu_away): favoritos claros, juegos
# cerrados, blowouts, y diferencias mínimas (hasta una millonésima) -- todo
# el rango realista de carreras proyectadas por equipo en un juego de MLB.
# Ninguno de estos pares es un empate exacto (eso se prueba aparte).
_REPRESENTATIVE_MU_PAIRS = [
    (4.5, 3.8), (3.8, 4.5),          # favorito moderado, cada lado
    (6.0, 3.0), (3.0, 6.0),          # blowout proyectado
    (4.2, 4.1), (4.1, 4.2),          # juego muy cerrado
    (4.15, 4.10), (4.10, 4.15),      # diferencia de 0.05 carreras
    (4.101, 4.100), (4.100, 4.101),  # diferencia de 0.001 carreras
    (2.5, 2.4), (2.4, 2.5),          # equipos de poco pitcheo ofensivo
    (7.5, 7.4), (7.4, 7.5),          # ambos ofensivos, parque hitter-friendly
    (5.0, 5.0 - 1e-6),               # diferencia de una millonésima
    (9.0, 1.5), (1.5, 9.0),          # desajuste extremo
    (3.33, 3.34), (3.34, 3.33),
    (4.0, 3.99), (3.99, 4.0),
    (5.5, 5.49), (5.49, 5.5),
    (2.0, 2.01), (2.01, 2.0),
]


def _away_probs(mu_home: float, mu_away: float, k: float) -> tuple[float, float]:
    """Replica exactamente cómo model/predictor.py deriva las probabilidades
    del visitante: 1 - home_prob, nunca la llamada cruda a la función."""
    away_skellam_prob = 1.0 - skellam_win_prob(mu_home, mu_away)
    away_negbin_prob = 1.0 - negbin_win_prob(mu_home, mu_away, k)
    return away_skellam_prob, away_negbin_prob


def test_mu_family_agrees_internally_on_representative_real_pairs_with_default_k():
    for mu_home, mu_away in _REPRESENTATIVE_MU_PAIRS:
        away_skellam_prob, away_negbin_prob = _away_probs(mu_home, mu_away, config.NEGBIN_DISPERSION)
        assert _mu_family_agrees_internally(away_skellam_prob, away_negbin_prob), (
            f"Discrepancia real e inesperada en mu_home={mu_home}, mu_away={mu_away} "
            f"con k={config.NEGBIN_DISPERSION} -- si esto falla, el barrido de 202,500 "
            f"pares documentado ya no es válido y hay que re-investigar."
        )


def test_mu_family_agrees_internally_holds_even_with_extreme_dispersion():
    # k mucho más chico que cualquier valor con sentido para MLB (0.01,
    # sabermétricamente absurdo) -- ni con dispersión extrema se cruza la
    # dirección para mu_home != mu_away.
    extreme_k = 0.01
    for mu_home, mu_away in _REPRESENTATIVE_MU_PAIRS:
        away_skellam_prob, away_negbin_prob = _away_probs(mu_home, mu_away, extreme_k)
        assert _mu_family_agrees_internally(away_skellam_prob, away_negbin_prob)


def test_exact_tie_can_trigger_the_known_floating_point_artifact():
    """
    Documenta el único disparador real conocido: un empate EXACTO
    (mu_home == mu_away bit a bit) donde el residuo de punto flotante de
    scipy.stats.skellam.cdf cae del lado que cruza 0.5 tras el complemento
    away=1-home. mu=1.0 es reproducible y determinista para esto (no
    todos los empates lo disparan -- ver docstring del módulo). No es una
    discrepancia real de modelo: negbin_win_prob da 0.5 exacto en el mismo
    empate, sin ningún residuo.

    Si esta prueba empieza a fallar (deja de haber discrepancia), no es
    una regresión de este proyecto: significa que una versión distinta de
    scipy/numpy cambió el redondeo interno de skellam.cdf para mu=1.0, y
    el docstring de _mu_family_agrees_internally ya no describe con
    precisión el comportamiento real -- habría que buscar otro mu de
    empate que sí lo dispare y actualizar ambos.
    """
    mu = 1.0
    away_skellam_prob, away_negbin_prob = _away_probs(mu, mu, config.NEGBIN_DISPERSION)

    assert away_skellam_prob != away_negbin_prob  # el residuo de punto flotante existe
    assert not _mu_family_agrees_internally(away_skellam_prob, away_negbin_prob)


def test_not_every_exact_tie_triggers_the_artifact():
    """Complemento del test anterior: mu=4.5 es un empate exacto que NO
    dispara el artefacto (el residuo de scipy cae del otro lado) -- prueba
    que esto no es "todo empate exacto falla", sino específico al mu."""
    mu = 4.5
    away_skellam_prob, away_negbin_prob = _away_probs(mu, mu, config.NEGBIN_DISPERSION)

    assert _mu_family_agrees_internally(away_skellam_prob, away_negbin_prob)
