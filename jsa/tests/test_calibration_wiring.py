"""`engine/orchestrator.py::_build_calibration_info()` -- Fase 4 (Seccion
8.4.1): unica fuente permitida de "calibrado" es una fila real y
validada de `calibration_registry`. Nunca se inventa un numero ni se
declara calibrado sin esa fila."""

from __future__ import annotations

from jsa import config
from jsa.engine.orchestrator import _build_calibration_info


def test_no_calibration_entry_stays_uncalibrated():
    info = _build_calibration_info(0.5, {})
    assert info.calibration_status == "uncalibrated"
    assert info.calibrated_probability is None
    assert info.raw_probability == 0.5


def test_entry_present_but_not_validated_stays_uncalibrated():
    rows = {config.PRODUCTION_CALIBRATION_ID: {"status": "under_validation", "x_knots": [-1, 1], "y_knots": [0.2, 0.8]}}
    info = _build_calibration_info(0.5, rows)
    assert info.calibration_status == "uncalibrated"
    assert info.calibrated_probability is None


def test_entry_validated_with_empty_knots_stays_uncalibrated():
    rows = {config.PRODUCTION_CALIBRATION_ID: {"status": "validated", "x_knots": [], "y_knots": []}}
    info = _build_calibration_info(0.5, rows)
    assert info.calibration_status == "uncalibrated"
    assert info.calibrated_probability is None


def test_validated_entry_applies_the_real_curve():
    rows = {
        config.PRODUCTION_CALIBRATION_ID: {
            "status": "validated", "x_knots": [-2.0, -1.0, 0.0, 1.0, 2.0], "y_knots": [0.1, 0.3, 0.5, 0.7, 0.9],
        }
    }
    info = _build_calibration_info(0.0, rows)
    assert info.calibration_status == "calibrated"
    assert info.raw_probability == 0.0
    assert info.calibrated_probability == 0.5

    info_mid = _build_calibration_info(0.5, rows)
    assert info_mid.calibrated_probability == 0.6  # interpolacion lineal entre (0.0, 0.5) y (1.0, 0.7)


def test_validated_entry_clips_out_of_range_values():
    rows = {
        config.PRODUCTION_CALIBRATION_ID: {
            "status": "validated", "x_knots": [-2.0, -1.0, 0.0, 1.0, 2.0], "y_knots": [0.1, 0.3, 0.5, 0.7, 0.9],
        }
    }
    info_low = _build_calibration_info(-5.0, rows)
    assert info_low.calibrated_probability == 0.1

    info_high = _build_calibration_info(5.0, rows)
    assert info_high.calibrated_probability == 0.9


def test_wrong_calibration_id_is_ignored():
    """Solo config.PRODUCTION_CALIBRATION_ID se lee -- una curva ajustada
    bajo otro id nunca entra en produccion por accidente."""
    rows = {"otra-curva-cualquiera": {"status": "validated", "x_knots": [-1, 1], "y_knots": [0.2, 0.8]}}
    info = _build_calibration_info(0.5, rows)
    assert info.calibration_status == "uncalibrated"
