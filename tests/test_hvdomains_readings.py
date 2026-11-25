"""#128 (B2) regressions: both above-500 V readings are computed
(the reviewer's exact figures), the primary values are unchanged,
and the readings coincide at and below 500 V."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import hvdomains as H


def test_both_readings_at_600v():
    assert H.required_spacing_mm(600, 'B1') == 0.50
    assert H.required_spacing_alt_mm(600, 'B1') == 1.50
    assert abs(H.required_spacing_mm(600, 'B4') - 1.105) < 1e-9
    assert abs(H.required_spacing_alt_mm(600, 'B4') - 1.83) < 1e-9


def test_readings_coincide_below_500():
    for col in ('B1', 'B2', 'B3', 'B4', 'A5', 'A6', 'A7'):
        for v in (30, 100, 499, 500):
            assert H.required_spacing_alt_mm(v, col) == \
                H.required_spacing_mm(v, col), (v, col)


def test_pair_alt_reading():
    req, eff, cond = H.pair_required_alt_mm(600, False, 'uncoated', 0, False)
    assert (req, eff, cond) == (1.50, 'B1', 'B1')
    req2, _e, _c = H.pair_required_mm(600, False, 'uncoated', 0, False)
    assert req2 == 0.50  # primary unchanged
