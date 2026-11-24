"""#69: length-group schema, delay math, target evaluation."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import lengthmatch, markings, tlcalc

LAYERS = {'F.Cu': {'class': 'surface_top', 'er': 4.5, 'h_mm': 0.2, 'thickness_mm': 0.035},
          'In1.Cu': {'class': 'inner', 'er': 4.5, 'h_mm': 0.2, 'thickness_mm': 0.035}}


def test_schema():
    ok = markings.validate({'version': 1, 'length_groups': [
        {'name': 'USB', 'pair': 'USB', 'tolerance_ps': 5}]})
    assert ok['length_groups'][0]['target_mode'] == 'longest'
    for bad in ({'name': 'X', 'nets': ['A']},
                {'name': 'X', 'nets': ['A', 'B']},
                {'name': 'X', 'nets': ['A', 'B'], 'target_mode': 'length', 'tolerance_mm': 1},
                {'name': 'X', 'nets': ['A', 'B'], 'target_mode': 'bogus', 'tolerance_mm': 1}):
        try:
            markings.validate({'version': 1, 'length_groups': [bad]})
        except markings.MarkingsError:
            continue
        raise AssertionError('accepted bad group: %r' % (bad,))


def test_delay_hand_values():
    # stripline in homogeneous er 4.5: 100 mm -> 100*sqrt(4.5)/0.299792 ps
    st = lengthmatch.member_stats(
        [{'layer': 'In1.Cu', 'length_mm': 100.0, 'width_mm': 0.2}], LAYERS)
    assert abs(st['delay_ps'] - 707.60) < 0.05
    # surface layer uses the calculator's microstrip eps_eff
    eps = tlcalc.microstrip(0.2, 0.2, 0.035, 4.5).eps_eff
    st2 = lengthmatch.member_stats(
        [{'layer': 'F.Cu', 'length_mm': 50.0, 'width_mm': 0.2},
         {'layer': 'In1.Cu', 'length_mm': 50.0, 'width_mm': 0.2}], LAYERS)
    expect = 50 * (eps ** 0.5) / lengthmatch.MM_PER_PS + 707.60 / 2
    assert abs(st2['delay_ps'] - expect) < 0.05
    assert st2['length_mm'] == 100.0


def test_evaluate_targets():
    mem = {'A': {'length_mm': 50.0, 'delay_ps': 300.0},
           'B': {'length_mm': 40.0, 'delay_ps': 240.0}}
    ev = lengthmatch.evaluate_group(
        {'name': 'G', 'target_mode': 'longest', 'tolerance_mm': 0.5}, mem)
    assert ev['target']['from_net'] == 'A'
    assert ev['members']['B']['delta_mm'] == 10.0
    assert ev['members']['B']['within_tolerance'] is False
    assert ev['members']['A']['within_tolerance'] is True
    ev2 = lengthmatch.evaluate_group(
        {'name': 'G', 'target_mode': 'delay', 'target_value': 250.0, 'tolerance_ps': 12.0}, mem)
    assert ev2['members']['A']['within_tolerance'] is False  # 50 ps over, tol 12
    assert ev2['members']['B']['within_tolerance'] is True   # 10 ps under
