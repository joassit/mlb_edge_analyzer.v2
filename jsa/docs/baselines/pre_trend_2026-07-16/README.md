# Baseline pre-Trend (2026-07-16)

Resultados reales, congelados ANTES de la re-ingesta que agrega los
candidatos de forma reciente para Trend (schema 3.3 -> 3.4, PR #25).
Objetivo: poder comparar "antes vs despues" con exactamente el mismo
punto de referencia, sin depender de la retencion de 30 dias de los
artifacts de GitHub Actions ni de ningun estado de sesion efimero.

- **Commit SHA de la corrida**: `d7c0d6c4215ad10256c74c03f4e2f01de32afbb3`
  (`discriminative_audit_result.json`) / `fa1a7ae4afab49895c6babbbdfd30bf902c870a0`
  (`resolution_audit_result.json`) -- ver el campo `run_metadata.commit_sha`
  dentro de `discriminative_audit_result.json` para el primero.
- **Temporadas**: 2022-2026, 13,099 juegos con resultado valido.
- **Schema version**: 3.3 (antes de los 8 campos rolling de Trend).
- **Calibracion vigente**: `calibration-evidence_score_raw-v1`,
  `status="validated"`, `loso_brier=0.24523`, `loso_ece=0.00298`
  (persistida en `calibration_registry`, JSA_DATABASE_URL -- esa fila NO
  se borra ni se sobreescribe por la re-ingesta, append-only).

## Archivos

- `discriminative_audit_result.json` -- salida completa de
  `jsa_historical_discriminative_audit.yml` (run 29488448243): pilares
  individuales, correlaciones, ablacion, optimizacion de pesos (single-pass
  y nested), distribucion, separabilidad, curvas, sensibilidad de shrinkage.
- `resolution_audit_result.json` -- salida completa de
  `jsa_historical_resolution_audit.yml` (run 29521778649): sweep de
  discretizacion (6 configuraciones), alternativas de team_quality
  (Elo, Pythagorean Expectation).

## Como comparar despues de re-ingerir

1. Re-correr `jsa_historical_discriminative_audit.yml` y
   `jsa_historical_resolution_audit.yml` sobre las 5 temporadas ya
   re-ingeridas (con Trend todavia como stub -- el "despues" de esta
   comparacion es "mismos datos, mas campos disponibles pero sin usar
   todavia", no "Trend ya wireado").
2. Diff directo de `baseline["loso_brier"]`/`loso_ece"]`/etc. contra
   estos archivos -- deberian ser IDENTICOS o casi identicos (la unica
   diferencia legitima séria variacion en juegos de 2026, la temporada en
   curso, si se jugaron mas partidos entre una corrida y otra).
   Cualquier diferencia inesperada en starter/bullpen/offense/team_quality/
   context (los 5 pilares que NO cambiaron) indicaria un problema real en
   la re-ingesta, no una mejora.
3. Recien despues de confirmar que el resto no cambio, construir el
   modulo de analisis de los 4 candidatos de Trend (Fase siguiente, ver
   ROADMAP.md).
