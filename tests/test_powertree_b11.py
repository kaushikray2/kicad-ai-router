"""#136 (B11) regressions: duty corners thermally (I*sqrt(D)),
conduit inferred only for two-pin passives, protective rating
floors the DOWNSTREAM conductor too, buck input at max Vout
(the last is pinned by the updated hand-worked table in
test_powertree.py)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import powerplan, powertree


def test_duty_corners_thermally_not_by_average():
    load = {'kind': 'constant_current', 'peak_a': 4.0,
            'sustained_a': 4.0, 'duty': 0.25}
    # heating goes with I^2 R: 4 A at 25% duty heats like 2 A
    assert powertree._cornered(load, None, 'sustained_a') == 2.0
    assert powertree._cornered(load, None, 'peak_a') == 4.0
    noduty = {'kind': 'constant_current', 'peak_a': 3.0, 'sustained_a': 3.0}
    assert powertree._cornered(noduty, None, 'sustained_a') == 3.0


def _comp(ref, part):
    return ('(comp (ref %s) (value X) (libsource (lib Fixture) (part %s)) '
            '(sheetpath (names "/") (tstamps "x")))' % (ref, part))


def _lp(part, pins):
    ps = ''.join('(pin (num "%s") (name p%s) (type %s))' % (n, n, t) for (n, t) in pins)
    return '(libpart (lib Fixture) (part %s) (pins %s))' % (part, ps)


def _net(name, code, nodes):
    ns = ''.join('(node (ref %s) (pin "%s"))' % (r, p) for (r, p) in nodes)
    return '(net (code %d) (name "%s") %s)' % (code, name, ns)


NET = ('(export (version E) (components %s) (libparts %s) (nets %s))'
       % (' '.join([_comp('J1', 'CONN'), _comp('U9', 'DUAL3'),
                    _comp('L1', 'FERR'), _comp('U2', 'LOAD')]),
          ' '.join([_lp('CONN', [(1, 'power_out'), (2, 'power_in')]),
                    _lp('DUAL3', [(1, 'power_in'), (2, 'power_in'),
                                  (3, 'power_in')]),
                    _lp('FERR', [(1, 'passive'), (2, 'passive')]),
                    _lp('LOAD', [(1, 'power_in'), (2, 'power_in')])]),
          ' '.join([_net('VIN', 1, [('J1', '1'), ('U9', '1'), ('L1', '1')]),
                    _net('VOUT', 2, [('U9', '2'), ('L1', '2'), ('U2', '1')]),
                    _net('GND', 3, [('J1', '2'), ('U9', '3'), ('U2', '2')])])))

ENTRIES = [
    {'net': 'VIN', 'root': {'kind': 'external', 'ref': 'J1'},
     'voltage': {'nominal_v': 12.0, 'min_v': 10.8, 'max_v': 13.2}},
    {'net': 'VOUT', 'voltage': {'nominal_v': 5.0, 'min_v': 4.75, 'max_v': 5.25},
     'loads': {'U2': {'kind': 'constant_current', 'peak_a': 0.5,
                      'sustained_a': 0.5}}}]


def test_conduit_only_for_two_pin_passives():
    t = powertree.compute_currents(
        powertree.build_tree(powertree.parse_netlist(NET), ENTRIES))
    e = {x['ref']: x for x in t['edges']}
    # the two-pin ferrite really is a pass-through
    assert e['L1']['kind'] == 'conduit', e
    # the undeclared 3-pin dual-supply part is NOT: no edge, flagged
    assert 'U9' not in e, e
    assert any(f['kind'] == 'undeclared_multi_rail_part' and f['ref'] == 'U9'
               for f in t['flags']), t['flags']


LAYERS = {'F.Cu': {'type': 'copper', 'thickness_mm': 0.035},
          'core': {'type': 'dielectric', 'thickness_mm': 1.5},
          'B.Cu': {'type': 'copper', 'thickness_mm': 0.035}}
CORNERS = {'ambient_c': 40.0, 'delta_t_c': 10.0, 'drop_budget_v': 0.5,
           'width_increment_mm': 0.05, 'etch_tol_mm': 0.02,
           'foil_tol_frac': 0.10, 'plating_um': 25.0,
           'decoupling_budget_mm': 5.0}


def _load(pk, su):
    return {'peak_a': pk, 'sustained_a': su, 'kind': 'constant_current',
            'modes': None, 'peak_cornered_a': pk, 'sustained_cornered_a': su}


def _fuse_tree():
    vin = {'net': 'VIN', 'complete': True, 'unknown_loads': [],
           'loads': {'U2': _load(0.5, 0.5)}, 'load_pins': {'U2': [('U2', '1')]},
           'source_pins': {'F1': [('F1', '2')]}, 'sources': [{'ref': 'F1', 'pin': '2'}],
           'candidate_sources': [], 'root': None,
           'voltage': {'nominal_v': 12.0, 'min_v': 10.8, 'max_v': 13.2}, 'design': {}}
    raw = {'net': 'VIN_RAW', 'complete': True, 'unknown_loads': [],
           'loads': {}, 'load_pins': {},
           'source_pins': {'J1': [('J1', '1')]}, 'sources': [{'ref': 'J1', 'pin': '1'}],
           'candidate_sources': [], 'root': None,
           'voltage': {'nominal_v': 12.0, 'min_v': 10.8, 'max_v': 13.2}, 'design': {}}
    edge = {'ref': 'F1', 'kind': 'protective', 'spec': {'rating_a': 2.0},
            'from': 'VIN_RAW', 'to': 'VIN', 'between': ['VIN', 'VIN_RAW'],
            'in_pins': [('F1', '1')], 'out_pins': [('F1', '2')],
            'by_mode': {'BASE': {'peak_a': 2.0, 'sustained_a': 2.0}},
            'design': {'peak_a': 2.0, 'sustained_a': 2.0}}
    return {'modes': ['BASE'], 'return_net': 'GND', 'flags': [],
            'edges': [edge], 'rails': {'VIN': vin, 'VIN_RAW': raw},
            'return_pins': {'U2': [('U2', '2')], 'J1': [('J1', '2')]},
            'components': {'F1': {'value': 'FUSE'}, 'U2': {'value': 'LOAD'},
                           'J1': {'value': 'IN'}}}


def _fuse_facts():
    vin = {'tracks': [{'uuid': 'tv', 'layer': 'F.Cu', 'x1': 0, 'y1': 0,
                       'x2': 10, 'y2': 0, 'w': 0.3}],
           'vias': [],
           'pads': [{'ref': 'F1', 'pin': '2', 'x': 0, 'y': 0, 'layers': ['F.Cu']},
                    {'ref': 'U2', 'pin': '1', 'x': 10, 'y': 0, 'layers': ['F.Cu']}],
           'zones': []}
    raw = {'tracks': [], 'vias': [],
           'pads': [{'ref': 'J1', 'pin': '1', 'x': -5, 'y': 0, 'layers': ['F.Cu']},
                    {'ref': 'F1', 'pin': '1', 'x': -4, 'y': 0, 'layers': ['F.Cu']}],
           'zones': []}
    gnd = {'tracks': [], 'vias': [],
           'pads': [{'ref': 'U2', 'pin': '2', 'x': 10, 'y': 1, 'layers': ['F.Cu']},
                    {'ref': 'J1', 'pin': '2', 'x': -5, 'y': 1, 'layers': ['F.Cu']}],
           'zones': [{'layer': 'F.Cu',
                      'polys': [[(-8, -5), (15, -5), (15, 5), (-8, 5)]]}]}
    return {'VIN': vin, 'VIN_RAW': raw, 'GND': gnd}


def _fuse_parsed():
    return {'components': _fuse_tree()['components'],
            'nets': {'VIN_RAW': [('J1', '1'), ('F1', '1')],
                     'VIN': [('F1', '2'), ('U2', '1')],
                     'GND': [('U2', '2'), ('J1', '2')]}}


def test_protective_rating_floors_downstream_conductor():
    plan = powerplan.plan_power(_fuse_tree(), _fuse_parsed(), _fuse_facts(),
                                LAYERS, CORNERS, {'VIN': {}, 'VIN_RAW': {}})
    segs = {s['uuid']: s for s in plan['rails']['VIN']['segments']}
    # the only load is 0.5 A, but the segment out of the fuse must
    # be sized for the 2 A rating -- it is the conductor the fuse
    # protects
    assert segs['tv']['i_sustained_a'] == 2.0, segs['tv']
    assert segs['tv']['i_peak_a'] == 2.0, segs['tv']
    assert any('floors the segments' in a
               for a in plan['rails']['VIN']['assumptions'])
