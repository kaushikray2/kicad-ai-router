import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import hvdomains as H
from ai_router import markings as _mk
from ai_router.markings import Markings, MarkingsError
import pytest


def test_table_matches_published_ipc2221b():
    rows = {15: (0.05, 0.1, 0.1, 0.05), 30: (0.05, 0.1, 0.1, 0.05),
            50: (0.1, 0.6, 0.6, 0.13), 100: (0.1, 0.6, 1.5, 0.13),
            150: (0.2, 0.6, 3.2, 0.4), 170: (0.2, 1.25, 3.2, 0.4),
            250: (0.2, 1.25, 6.4, 0.4), 300: (0.2, 1.25, 12.5, 0.4),
            500: (0.25, 2.5, 12.5, 0.8)}
    for v, exp in rows.items():
        got = tuple(H.required_spacing_mm(v, c)
                    for c in ('B1', 'B2', 'B3', 'B4'))
        assert got == exp, (v, got, exp)
    # IPC-A-610 App. A worked example (IPC-2221 para 6.3): 600 V B1
    assert H.required_spacing_mm(600, 'B1') == 0.5
    assert abs(H.required_spacing_mm(1000, 'B4') - 2.325) < 1e-9
    assert H.required_spacing_mm(37, 'B2') == 0.6  # band, not interp


def test_column_selection():
    assert H.column_for(False) == 'B1'
    assert H.column_for(True) == 'B2'
    assert H.column_for(True, 'polymer') == 'B4'
    assert H.column_for(True, 'uncoated', 4000) == 'B3'
    assert H.column_for(True, 'polymer', 4000) == 'B4'


def test_clearance_exact():
    a = [('disc', 0.0, 0.0, 1.0)]
    b = [('disc', 10.0, 0.0, 1.5)]
    gap, pa, pb = H.min_clearance(a, b)
    assert abs(gap - 7.5) < 1e-9
    a = [('seg', 0.0, 0.0, 10.0, 0.0, 0.25)]
    b = [('seg', 0.0, 3.0, 10.0, 3.0, 0.25)]
    gap, pa, pb = H.min_clearance(a, b)
    assert abs(gap - 2.5) < 1e-9 and abs(pa[1]) < 1e-9


def test_creepage_detours_around_slot():
    a = [('disc', 15.0, 20.0, 1.0)]
    b = [('disc', 45.0, 20.0, 1.0)]
    outline = [(5.0, 5.0), (55.0, 5.0), (55.0, 35.0), (5.0, 35.0)]
    slot = [(28.0, 12.0), (32.0, 12.0), (32.0, 28.0), (28.0, 28.0)]
    straight = H.creepage_path(a, b, a + b, outline, [], pitch=0.25)
    slotted = H.creepage_path(a, b, a + b, outline, [slot], pitch=0.25)
    assert straight is not None and 26.5 < straight < 29.5
    # hand detour via the slot end: ~14.4 + 4 + 14.4 = 32.8
    assert slotted is not None and 30.5 < slotted < 35.5
    assert slotted > straight + 2.0


def test_markings_hv_schema():
    mk = Markings(_mk.validate({'hv_domains': [
        {'name': 'MAINS', 'nets': ['L', 'N'], 'voltage_v': 340,
         'coating': 'polymer', 'materialise': True}]}))
    assert mk.hv_domains[0]['voltage_v'] == 340
    with pytest.raises(MarkingsError):
        _mk.validate({'hv_domains': [{'name': 'X', 'nets': ['A']}]})
    with pytest.raises(MarkingsError):
        _mk.validate({'hv_domains': [{'name': 'X', 'nets': ['A'],
                                      'voltage_v': 10,
                                      'coating': 'mask'}]})


def test_seg_seg_crossing_witness_is_intersection():
    # #89: crossing segments must report the intersection point,
    # not the segments' start points.
    d, pa, pb = H._seg_seg((0.0, 0.0, 10.0, 0.0), (5.0, -5.0, 5.0, 5.0))
    assert d == 0.0
    assert abs(pa[0] - 5.0) < 1e-9 and abs(pa[1]) < 1e-9, pa
    assert abs(pb[0] - 5.0) < 1e-9 and abs(pb[1]) < 1e-9, pb
