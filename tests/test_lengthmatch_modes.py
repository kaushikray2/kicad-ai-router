"""Regression tests for #152 (third review B23):
(1) a length target with only tolerance_ps (or a delay target with
    only tolerance_mm) applied NO check -- members of 60 mm and
    100 mm both 'passed' a 100 mm target.  Validation now rejects
    the mismatch and evaluate_group fails closed.
(2) _eng_layer_delay_info keyed the stackup by canonical layer
    name while segments carry board names (the #84 bug, unfixed
    here) -- renamed layers fell back to er 4.5 stripline.
(3) meander need aimed at the tolerance EDGE (shortfall minus
    tolerance); it now aims at the band CENTRE."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from ai_router import lengthmatch, markings
from ai_router.markings import MarkingsError


def _validate(entry):
    return markings.validate({'version': 1, 'length_groups': [entry]})


def test_validation_rejects_length_mode_with_only_ps_tolerance():
    with pytest.raises(MarkingsError):
        _validate({'name': 'G', 'nets': ['A', 'B'], 'target_mode': 'length',
                   'target_value': 100, 'tolerance_ps': 10})


def test_validation_rejects_delay_mode_with_only_mm_tolerance():
    with pytest.raises(MarkingsError):
        _validate({'name': 'G', 'nets': ['A', 'B'], 'target_mode': 'delay',
                   'target_value': 600, 'tolerance_mm': 0.5})


def test_validation_accepts_matched_modes_and_longest():
    _validate({'name': 'G', 'nets': ['A', 'B'], 'target_mode': 'length',
               'target_value': 100, 'tolerance_mm': 0.5})
    _validate({'name': 'G', 'nets': ['A', 'B'], 'target_mode': 'delay',
               'target_value': 600, 'tolerance_ps': 10})
    _validate({'name': 'G', 'nets': ['A', 'B'], 'target_mode': 'longest',
               'tolerance_ps': 10})


def test_evaluate_group_fails_closed_on_mismatch():
    # the review's reproduction, unvalidated entry reaching evaluate
    ev = lengthmatch.evaluate_group(
        {'name': 'G', 'target_mode': 'length', 'target_value': 100,
         'tolerance_ps': 10},
        {'A': {'length_mm': 60.0, 'delay_ps': 400.0},
         'B': {'length_mm': 100.0, 'delay_ps': 660.0}})
    assert 'error' in ev, ev
    assert not any(m['within_tolerance'] for m in ev['members'].values()), ev
    ev = lengthmatch.evaluate_group(
        {'name': 'G', 'target_mode': 'delay', 'target_value': 600,
         'tolerance_mm': 0.5},
        {'A': {'length_mm': 90.0, 'delay_ps': 300.0},
         'B': {'length_mm': 100.0, 'delay_ps': 600.0}})
    assert 'error' in ev
    assert not any(m['within_tolerance'] for m in ev['members'].values())


def test_meander_need_aims_at_centre():
    entry = {'tolerance_mm': 0.5, 'tolerance_ps': None}
    need = lengthmatch.meander_need({'delta_mm': 2.0, 'delta_ps': None}, entry, 6.0)
    assert abs(need - (2.0 - lengthmatch.MEANDER_EPS_MM)) < 1e-9, need
    # inside the band: nothing to do
    assert lengthmatch.meander_need({'delta_mm': 0.3, 'delta_ps': None}, entry, 6.0) is None
    # delay-only failure converts at the member's own rate
    entry2 = {'tolerance_mm': None, 'tolerance_ps': 5.0}
    need = lengthmatch.meander_need({'delta_mm': None, 'delta_ps': 65.0}, entry2, 6.5)
    assert abs(need - (65.0 / 6.5 - lengthmatch.MEANDER_EPS_MM)) < 1e-9, need


def test_layer_delay_info_keys_board_names():
    from ai_router.pipeline import Pipeline
    pipe = Pipeline.__new__(Pipeline)
    pipe.current = 'board.kicad_pcb'
    su = {'copper': [
        {'name': 'F.Cu', 'board_name': 'SIG_TOP', 'class': 'surface_top',
         'thickness_mm': 0.035,
         'diel_below': {'er': 4.0, 'thickness_mm': 0.2}},
        {'name': 'In1.Cu', 'board_name': 'PLANE', 'class': 'inner',
         'thickness_mm': 0.035,
         'diel_below': {'er': 4.2, 'thickness_mm': 0.3}}]}
    pipe._worker = lambda *a, **k: su
    layers = pipe._eng_layer_delay_info()
    assert 'SIG_TOP' in layers and 'PLANE' in layers, sorted(layers)
    assert layers['SIG_TOP']['er'] == 4.0
    # and member_stats on the renamed surface layer uses its real
    # microstrip eps_eff, not the er-4.5 stripline fallback
    st = lengthmatch.member_stats(
        [{'layer': 'SIG_TOP', 'length_mm': 10.0, 'width_mm': 0.2}], layers)
    fallback = lengthmatch.member_stats(
        [{'layer': 'SIG_TOP', 'length_mm': 10.0, 'width_mm': 0.2}], {})
    assert abs(st['delay_ps'] - fallback['delay_ps']) > 1.0, (st, fallback)
