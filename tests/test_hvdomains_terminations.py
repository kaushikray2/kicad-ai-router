"""Regression tests for #127 (third review B1): IPC-2221B Table 6-1
termination columns.  Pads are terminations (openings in solder
mask): a pair whose closest approach involves a pad must satisfy
A6 (uncoated/polymer) or A7 (conformal), not just the conductor
column B2/B4.  A5/A6/A7 values cross-verified cell-for-cell across
three reproductions of Table 6-1 (Sierra Circuits, Altair PollEx,
protoflow); ANSI's IPC-2221B preview names the columns in
sec. 6.3.5-6.3.7."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import hvdomains as H
from ai_router import markings


def test_a_column_values_match_table_6_1():
    assert H.required_spacing_mm(24, 'A6') == 0.25     # 16-30 band
    assert H.required_spacing_mm(100, 'A6') == 0.50    # 51-100 band
    assert H.required_spacing_mm(300, 'A7') == 0.80    # 251-300 band
    assert H.required_spacing_mm(500, 'A5') == 0.80    # 301-500 band
    assert H.required_spacing_mm(10, 'A5') == 0.13
    # above 500 V: band value + per-volt rate (0.00305 for A cols)
    assert abs(H.required_spacing_mm(600, 'A6') - (1.50 + 100 * 0.00305)) < 1e-9
    # B columns unchanged
    assert H.required_spacing_mm(100, 'B4') == 0.13
    assert H.required_spacing_mm(100, 'B2') == 0.60


def test_conformal_conductor_column_is_a5():
    assert H.column_for(True, 'conformal') == 'A5'
    assert H.column_for(True, 'polymer') == 'B4'
    assert H.column_for(True, 'uncoated') == 'B2'
    assert H.column_for(False, 'conformal') == 'B1'


def test_review_reproduction_vectors_now_fail():
    # 24 V, pads 0.15 mm apart, uncoated: B2 asked 0.10 (pass);
    # A6 requires 0.25 -> the pair requirement must be 0.25.
    req, col, cond = H.pair_required_mm(24, True, 'uncoated', 0, termination=True)
    assert (req, col, cond) == (0.25, 'A6', 'B2'), (req, col, cond)
    assert 0.15 < req
    # 100 V, pads 0.2 mm apart, polymer: B4 asked 0.13 (pass);
    # A6 requires 0.50.
    req, col, cond = H.pair_required_mm(100, True, 'polymer', 0, termination=True)
    assert (req, col, cond) == (0.50, 'A6', 'B4'), (req, col, cond)
    assert 0.2 < req


def test_conductor_only_pairs_unchanged():
    req, col, cond = H.pair_required_mm(24, True, 'uncoated', 0, termination=False)
    assert (req, col) == (0.10, 'B2')
    req, col, cond = H.pair_required_mm(100, True, 'polymer', 0, termination=False)
    assert (req, col) == (0.13, 'B4')
    # internal pairs never take a termination column
    req, col, cond = H.pair_required_mm(100, False, 'uncoated', 0, termination=True)
    assert (req, col) == (0.10, 'B1')


def test_conformal_pairs_use_a5_and_a7():
    req, col, cond = H.pair_required_mm(100, True, 'conformal', 0, termination=False)
    assert (req, col, cond) == (0.13, 'A5', 'A5')
    req, col, cond = H.pair_required_mm(300, True, 'conformal', 0, termination=True)
    assert (req, col, cond) == (0.80, 'A7', 'A5'), (req, col, cond)


def test_conformal_coating_validates():
    out = markings.validate({'version': 1, 'hv_domains': [
        {'name': 'HV', 'nets': ['HV1'], 'voltage_v': 100,
         'coating': 'conformal'}]})
    assert out['hv_domains'][0]['coating'] == 'conformal'
