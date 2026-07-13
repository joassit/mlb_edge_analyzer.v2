# jsa/legacy — rama secundaria de comparacion, NUNCA el motor primario

Este paquete existe por un solo motivo: **preservar, dentro de JSA, los
modelos ya calibrados de `mlb_edge_analyzer.v2`** (heuristico ERA/OPS,
Skellam, Binomial Negativo NB2) para usarlos como baselines de
benchmarking obligatorio (Seccion 12.3 del spec JSA v3.0) — nunca para
generar el pick real de un juego.

## Regla dura

**Nada en `jsa/main.py`, `jsa/engine/orchestrator.py` ni ningun otro
modulo de produccion en vivo puede importar de `jsa/legacy/`.** El motor
primario de JSA es, y sigue siendo, el Evidence Engine + los 7 pilares
(`jsa/engine/`). `jsa/legacy/` solo se usa desde
`jsa/historical/validation.py`, para responder una pregunta especifica:
*¿el Evidence Score de JSA supera a estos modelos ya calibrados, con
significancia estadistica real, sobre las mismas temporadas?* — exactamente
lo que exige la Seccion 12.3 ("todo experimento se compara contra...
favorece al equipo con mejor ERA de abridor", etc.).

`tests/test_production_isolation.py` hace cumplir esta regla en CI.

## Procedencia de cada constante

Todas las constantes de `calibration_constants.py` fueron calibradas en
`mlb_edge_analyzer.v2` contra un barrido real de 4 temporadas (2022-2025,
8,852 juegos con resultado real, corrida del 2026-07-12 con
`historical_engine`) — ver los comentarios originales en
`mlb_edge_analyzer.v2/config.py` para el detalle completo por temporada.
Se copian aqui **tal cual**, sin recalibrar: recalibrarlas con datos
propios de JSA es trabajo de `jsa/historical/validation.py`, una vez que
la ingesta de 2022-2026 corra dentro de JSA mismo.

## Que se porto y por que

- `heuristic_model.py`: modelo ERA/OPS heuristico
  (`mlb_edge_analyzer.v2/model/probability.py`) — el unico de los tres
  que el barrido de calibracion encontro que YA estaba bien calibrado de
  fabrica (`alpha=1.0` optimo en las 4 temporadas, sin contraccion).
- `skellam_model.py`: probabilidad de victoria via distribucion de
  Skellam (`mlb_edge_analyzer.v2/model/skellam_model.py`) — el barrido
  encontro que esta estructuralmente sobreconfiado, corregido con
  `SKELLAM_SHRINKAGE_ALPHA=0.5` en `calibration_constants.py`.
- `negbin_model.py`: probabilidad via Binomial Negativo NB2
  (`mlb_edge_analyzer.v2/model/negbin_model.py`) — captura sobredispersion
  de carreras que Skellam/Poisson subestima, con `NEGBIN_DISPERSION=3.0`.
