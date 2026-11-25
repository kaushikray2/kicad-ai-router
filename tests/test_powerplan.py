"""#68 Step 2: per-segment power planner on synthetic copper facts.

Hand-computed attribution for the main fixture (rail V5):
  T1 (trunk)  carries U2 (2 A, always) + U3 (1 A, TX only)
              -> BASE (2, 2), TX (3, 2.5); design (3, 2.5)
  T2          carries U2 only -> (2, 2)
  T3          carries U3 only -> design (1, 0.5)
At a 10 C budget the trunk and U2 branch need widening from
0.3 mm; the U3 twig (0.5 A sustained) does not.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import powerplan

LAYERS = {'F.Cu': {'type': 'copper', 'thickness_mm': 0.035},
          'core': {'type': 'dielectric', 'thickness_mm': 1.5},
          'B.Cu': {'type': 'copper', 'thickness_mm': 0.035}}
CORNERS = {'ambient_c': 40.0, 'delta_t_c': 10.0, 'drop_budget_v': 0.5,
           'width_increment_mm': 0.05, 'etch_tol_mm': 0.02,
           'foil_tol_frac': 0.10, 'plating_um': 25.0,
           'decoupling_budget_mm': 5.0}


def _load(pk, su, modes=None):
    return {'peak_a': pk, 'sustained_a': su, 'kind': 'constant_current',
            'modes': modes, 'peak_cornered_a': pk, 'sustained_cornered_a': su}


def _rail(loads, load_pins, complete=True, unknown=None):
    return {'net': 'V5', 'complete': complete, 'unknown_loads': unknown or [],
            'loads': loads, 'load_pins': load_pins,
            'source_pins': {'U1': [('U1', '2')]},
            'sources': [{'ref': 'U1', 'pin': '2'}], 'candidate_sources': [],
            'root': None, 'voltage': {'nominal_v': 5.0, 'min_v': 4.75, 'max_v': 5.25},
            'design': {}}


def _tree(rail):
    return {'modes': ['BASE', 'TX'], 'return_net': 'GND', 'flags': [],
            'edges': [], 'rails': {'V5': rail},
            'return_pins': {'U1': [('U1', '3')], 'U2': [('U2', '2')],
                            'U3': [('U3', '2')], 'C1': [('C1', '2')]},
            'components': {'C1': {'value': '100n', 'footprint': ''},
                           'U1': {'value': 'REG'}, 'U2': {'value': 'MCU'},
                           'U3': {'value': 'RADIO'}}}


def _parsed():
    return {'components': _tree(None)['components'],
            'nets': {'V5': [('U1', '2'), ('U2', '1'), ('U3', '1'), ('C1', '1')],
                     'GND': [('U1', '3'), ('U2', '2'), ('U3', '2'), ('C1', '2')]}}


def _facts():
    v5 = {'tracks': [
              {'uuid': 't1', 'layer': 'F.Cu', 'x1': 0, 'y1': 0, 'x2': 10, 'y2': 0, 'w': 0.3},
              {'uuid': 't2', 'layer': 'F.Cu', 'x1': 10, 'y1': 0, 'x2': 20, 'y2': 0, 'w': 0.3},
              {'uuid': 't3', 'layer': 'F.Cu', 'x1': 10, 'y1': 0, 'x2': 10, 'y2': 10, 'w': 0.3}],
          'vias': [],
          'pads': [{'ref': 'U1', 'pin': '2', 'x': 0, 'y': 0, 'layers': ['F.Cu']},
                   {'ref': 'U2', 'pin': '1', 'x': 20, 'y': 0, 'layers': ['F.Cu']},
                   {'ref': 'U3', 'pin': '1', 'x': 10, 'y': 10, 'layers': ['F.Cu']},
                   {'ref': 'C1', 'pin': '1', 'x': 19, 'y': 1, 'layers': ['F.Cu']}],
          'zones': []}
    gnd = {'tracks': [], 'vias': [{'x': 19.5, 'y': 2.0, 'drill': 0.3}],
           'pads': [{'ref': 'U1', 'pin': '3', 'x': 0, 'y': 1, 'layers': ['F.Cu']},
                    {'ref': 'U2', 'pin': '2', 'x': 19.8, 'y': 1.6, 'layers': ['F.Cu']},
                    {'ref': 'U3', 'pin': '2', 'x': 10, 'y': 11, 'layers': ['F.Cu']},
                    {'ref': 'C1', 'pin': '2', 'x': 19, 'y': 2, 'layers': ['F.Cu']}],
           'zones': [{'layer': 'F.Cu', 'polys': [[(-5, -5), (30, -5), (30, 20), (-5, 20)]]}]}
    return {'V5': v5, 'GND': gnd}


def test_attribution_sizing_and_report():
    rail = _rail({'U2': _load(2.0, 2.0), 'U3': _load(1.0, 0.5, ['TX'])},
                 {'U2': [('U2', '1')], 'U3': [('U3', '1')]})
    plan = powerplan.plan_power(_tree(rail), _parsed(), _facts(), LAYERS,
                                CORNERS, {'V5': {}})
    rep = plan['rails']['V5']
    segs = {s['uuid']: s for s in rep['segments']}
    assert (segs['t1']['i_peak_a'], segs['t1']['i_sustained_a']) == (3.0, 2.5)
    assert (segs['t2']['i_peak_a'], segs['t2']['i_sustained_a']) == (2.0, 2.0)
    assert (segs['t3']['i_peak_a'], segs['t3']['i_sustained_a']) == (1.0, 0.5)
    widen = plan['widen']['V5']
    assert 't1' in widen and 't2' in widen and 't3' not in widen
    assert widen['t1'] > 0.3 and segs['t1']['ok'] is False
    drops = {d['ref']: d for d in rep['loop_drops']}
    assert drops['U2']['ok'] is True and drops['U2']['return'] == 'plane'
    assert any('solid-plane return' in a for a in plan['assumptions'])
    dec = {d['ref']: d for d in rep['decoupling']}
    assert dec['U2']['cap_present'] is True and dec['U2']['nearest_cap'] == 'C1'
    assert dec['U2']['distance_mm'] == 1.41 and dec['U2']['within_budget'] is True
    assert dec['U2']['cap_ground_via'] is True and dec['U2']['via_shared_with_load'] is True
    assert dec['U3']['within_budget'] is False


def test_incomplete_rail_resistances_only():
    rail = _rail({'U2': _load(2.0, 2.0)}, {'U2': [('U2', '1')]},
                 complete=False, unknown=['U9'])
    plan = powerplan.plan_power(_tree(rail), _parsed(), _facts(), LAYERS,
                                CORNERS, {'V5': {}})
    rep = plan['rails']['V5']
    assert rep['resistances_only'] is True
    assert 'V5' not in plan['widen']
    assert all('resistance_ohm' in s for s in rep['segments'])


def test_via_array_sizing():
    rail = _rail({'U2': _load(5.0, 5.0)}, {'U2': [('U2', '1')]})
    facts = {'V5': {'tracks': [
                 {'uuid': 'a', 'layer': 'F.Cu', 'x1': 0, 'y1': 0, 'x2': 10, 'y2': 0, 'w': 2.0},
                 {'uuid': 'b', 'layer': 'B.Cu', 'x1': 10, 'y1': 0, 'x2': 20, 'y2': 0, 'w': 2.0}],
             'vias': [{'x': 10, 'y': 0, 'drill': 0.3}],
             'pads': [{'ref': 'U1', 'pin': '2', 'x': 0, 'y': 0, 'layers': ['F.Cu']},
                      {'ref': 'U2', 'pin': '1', 'x': 20, 'y': 0, 'layers': ['B.Cu']}],
             'zones': []},
             'GND': {'tracks': [], 'vias': [], 'pads': [], 'zones': [
                 {'layer': 'F.Cu', 'polys': [[(-5, -5), (30, -5), (30, 20), (-5, 20)]]}]}}
    plan = powerplan.plan_power(_tree(rail), _parsed(), facts, LAYERS, CORNERS, {'V5': {}})
    assert plan['via_adds'] == [{'net': 'V5', 'x': 10, 'y': 0, 'add': 2, 'needed': 3, 'existing': 1}]


def test_pour_carried_load():
    rail = _rail({'U2': _load(1.0, 1.0)}, {'U2': [('U2', '1')]})
    facts = {'V5': {'tracks': [], 'vias': [],
                    'pads': [{'ref': 'U1', 'pin': '2', 'x': 0, 'y': 0, 'layers': ['F.Cu']},
                             {'ref': 'U2', 'pin': '1', 'x': 5, 'y': 5, 'layers': ['F.Cu']}],
                    'zones': [{'layer': 'F.Cu', 'polys': [[(-2, -2), (8, -2), (8, 8), (-2, 8)]]}]},
             'GND': {'tracks': [], 'vias': [], 'pads': [], 'zones': []}}
    plan = powerplan.plan_power(_tree(rail), _parsed(), facts, LAYERS, CORNERS, {'V5': {}})
    d = plan['rails']['V5']['loop_drops'][0]
    assert d['pour_carried'] is True and d['unrouted'] is False


def test_unreachable_stub_load_is_unreachable():
    # #82: a load on a disconnected island WITH a stub track is in
    # g.adj but in no Dijkstra dist map.  It must come back as
    # paths None (unreachable) -- the old prev/adj test returned [],
    # which the loop-drop reporter read as drop_v 0.0 / ok True.
    class G:
        adj = {5: [(0, 9)], 9: [(0, 5)]}
        edges = [{'kind': 'track', 'length': 1.0}]
    ec, paths = powerplan._attribute(G(), {}, {}, [
        {'id': 'U7', 'node': 5, 'currents': {'BASE': (1.0, 0.5)}}])
    assert paths['U7'] is None, paths
    # a load sitting ON a source node (in dist, no prev entry) is a
    # genuine zero-length path, not an unreachable one
    ec, paths = powerplan._attribute(G(), {5: 0.0}, {}, [
        {'id': 'U8', 'node': 5, 'currents': {'BASE': (1.0, 0.5)}}])
    assert paths['U8'] == [], paths
