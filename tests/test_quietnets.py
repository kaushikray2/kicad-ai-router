import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import quietnets as Q
from ai_router import markings as _mk
from ai_router.markings import MarkingsError
import pytest

LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']


def _board(dig_y=35.0, guard_ties=2, cap_via=True):
    items = {}
    def put(net, layer, it):
        items.setdefault((net, layer), []).append(it)
    put('XTAL1', 'F.Cu', ('seg', 14.0, 20.0, 18.0, 20.0, 0.15))
    put('XTAL2', 'F.Cu', ('seg', 22.0, 20.0, 26.0, 20.0, 0.15))
    put('DIG', 'F.Cu', ('seg', 5.0, dig_y, 55.0, dig_y, 0.15))
    put('DIG', 'In2.Cu', ('seg', 5.0, dig_y, 55.0, dig_y, 0.15))
    pads = [
        {'ref': 'X1', 'net': 'XTAL1', 'x': 18.0, 'y': 20.0, 'w': 1.6, 'h': 1.6, 'layers': ['F.Cu']},
        {'ref': 'X1', 'net': 'XTAL2', 'x': 22.0, 'y': 20.0, 'w': 1.6, 'h': 1.6, 'layers': ['F.Cu']},
        {'ref': 'X1', 'net': 'GND', 'x': 20.0, 'y': 23.0, 'w': 1.6, 'h': 1.6, 'layers': ['F.Cu']},
        {'ref': 'C1', 'net': 'XTAL1', 'x': 14.0, 'y': 19.0, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
        {'ref': 'C1', 'net': 'GND', 'x': 14.0, 'y': 21.5, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
        {'ref': 'C2', 'net': 'XTAL2', 'x': 26.0, 'y': 19.0, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
        {'ref': 'C2', 'net': 'GND', 'x': 26.0, 'y': 21.5, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']},
    ]
    vias = []
    if cap_via:
        vias += [{'net': 'GND', 'x': 14.0, 'y': 23.0, 'd': 0.6},
                 {'net': 'GND', 'x': 26.0, 'y': 23.0, 'd': 0.6}]
    for i in range(guard_ties):
        pads.append({'ref': 'NT%d' % i, 'net': 'GUARD', 'x': 10.0 + 20.0 * i,
                     'y': 12.0, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']})
        pads.append({'ref': 'NT%d' % i, 'net': 'GND', 'x': 10.0 + 20.0 * i,
                     'y': 13.2, 'w': 1.0, 'h': 1.0, 'layers': ['F.Cu']})
    zones = {('GND', 'In1.Cu'): [[(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)]]}
    return {'layers': LAYERS, 'items': items, 'pads': pads,
            'vias': vias, 'zones': zones}


ENTRY = {'ref': 'X1', 'profile': 'crystal', 'guard_net': 'GUARD'}


def test_schema():
    ok = _mk.validate({'quiet': [{'ref': 'X1', 'profile': 'crystal'}]})
    assert ok['quiet'][0]['ref'] == 'X1'
    with pytest.raises(MarkingsError):
        _mk.validate({'quiet': [{'net': 'A', 'ref': 'X1'}]})
    with pytest.raises(MarkingsError):
        _mk.validate({'quiet': [{'net': 'A', 'profile': 'loud'}]})


def test_profiles_and_override():
    r = Q.resolve({'net': 'X', 'profile': 'reference'})
    assert r['isolation_mm'] == 2.0 and r['isolation_source'] == 'profile'
    r = Q.resolve({'net': 'X', 'profile': 'reference', 'isolation_mm': 0.7})
    assert r['isolation_mm'] == 0.7 and r['isolation_source'] == 'user'


def test_clean_crystal_passes():
    rep = Q.evaluate(ENTRY, _board())
    assert rep['pass'], rep
    assert rep['intrusions'] == []
    assert {c['ref'] for c in rep['load_caps']} == {'C1', 'C2'}
    assert all(c['ground_return'] for c in rep['load_caps'])
    assert rep['guard']['ok'] and rep['guard']['ties'] == 2


def test_corridor_intrusion_located():
    rep = Q.evaluate(ENTRY, _board(dig_y=20.8))
    assert not rep['pass']
    kinds = {(i['kind'], i['net']) for i in rep['intrusions']}
    assert ('corridor', 'DIG') in kinds
    v = [i for i in rep['intrusions'] if i['kind'] == 'corridor'][0]
    assert abs(v['distance_mm'] - 0.5) < 0.01


def test_adjacent_layer_crossing():
    b = _board()
    b['items'][('DIG', 'In2.Cu')] = [('seg', 5.0, 20.8, 55.0, 20.8, 0.15)]
    del b['items'][('DIG', 'F.Cu')]
    b['items'][('DIG', 'F.Cu')] = [('seg', 5.0, 35.0, 55.0, 35.0, 0.15)]
    rep = Q.evaluate(ENTRY, b)
    # In2.Cu is two layers from F.Cu in a 4-layer stack: NOT adjacent
    assert rep['intrusions'] == []
    b['items'][('DIG', 'In1.Cu')] = [('seg', 5.0, 20.8, 55.0, 20.8, 0.15)]
    rep = Q.evaluate(ENTRY, b)
    assert any(i['kind'] == 'adjacent_layer' for i in rep['intrusions'])


def test_guard_single_tie_fails_and_cap_return():
    rep = Q.evaluate(ENTRY, _board(guard_ties=1))
    assert not rep['pass'] and rep['guard']['ties'] == 1
    rep = Q.evaluate(ENTRY, _board(cap_via=False))
    assert not rep['pass']
    assert any(not c['ground_return'] for c in rep['load_caps'])


def test_seg_hits_rect_corner_crossing():
    # #96: a segment clipping the rect's corner (in through one
    # edge, out the adjacent one) with endpoints AND midpoint
    # outside must still register as a hit.
    rect = (0.0, 0.0, 10.0, 10.0)
    item = ('seg', -0.05, 0.15, 5.0, -4.9, 0.0)   # crosses (0,0.1)-(0.1,0)
    assert Q._seg_hits_rect(item, rect, 0.0) is True
    far = ('seg', -5.0, -5.0, -1.0, -1.0, 0.0)
    assert Q._seg_hits_rect(far, rect, 0.0) is False
