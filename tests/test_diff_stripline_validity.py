"""#144 (B18, proven part) regression: Cohn thick-strip validity
w/(b-t) >= 0.35 is flagged out of range instead of silently
trusted.  (The offset-model rewrite is a design stage and is
NOT part of this fix.)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import tlcalc


def test_narrow_strip_flagged_out_of_range():
    # w/(b-t) = 0.2 / 0.965 = 0.207 < 0.35
    r = tlcalc.diff_stripline(0.2, 0.2, 1.0, 0.035, 4.2)
    assert r.in_range is False, r
    assert 'cohn_validity_w_over_b_minus_t_below_0.35' in r.flags, r.flags


def test_wide_strip_stays_in_range():
    # w/(b-t) = 0.5 / 0.965 = 0.518 >= 0.35; t/b well under 0.25
    r = tlcalc.diff_stripline(0.5, 0.2, 1.0, 0.035, 4.2)
    assert r.in_range is True, r
    assert 'cohn_validity_w_over_b_minus_t_below_0.35' not in r.flags


def test_thin_strip_path_unaffected():
    r = tlcalc.diff_stripline(0.2, 0.2, 1.0, 0.0, 4.2)
    assert 'cohn_validity_w_over_b_minus_t_below_0.35' not in r.flags
