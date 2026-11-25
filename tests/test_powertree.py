"""#68 Step 1: power-tree WCCA core against a hand-worked fixture.

The fixture schematic (a hand-written kicadsexpr netlist export)
encodes the ticket's acceptance set: one fused input, one buck,
one LDO, one FET-switched branch with mutually exclusive loads,
one hierarchical repeated load (4 instances), one constant-power
load, one resistive load, and one dangling supply pin.

Hand-worked design currents (EVA per mode; modes BASE/TX/HEAT/LAMP):
  VSW   heater (resistive, HEAT) 0.8 A at Vmax 3.465/3.3 -> 0.84;
        lamp (LAMP) 0.50 -> edge Q1 design = max = 0.84 / 0.84
  V3    BASE 0.20+0.05 = 0.25 pk (0.15 sust); TX adds radio 1.20 pk
        (0.30 sust) -> 1.45 pk; HEAT 0.25+0.84 = 1.09; LAMP 0.75
        -> design peak 1.45, sustained max(TX 0.45, HEAT 0.99) 0.99
  LDO   in = out + Iq 0.001 -> 1.451 pk / 0.991 sust
  V5    channels 4 x 0.10 = 0.40; CP load 0.5 A at 5 V = 2.5 W at
        Vmin 4.75 -> 0.526316; own 0.926316; total 2.377316 pk /
        1.917316 sust
  buck  in = 5.25 (Vout MAX, #136) * Iout / (0.85 * 10.8): pk 1.359576 / sust 1.096504
  VIN   = buck input;  VIN_RAW = fuse floor: max(1.359576, 2.0) = 2.0
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import markings, powertree


def _comp(ref, part, sheet='/'):
    return ('(comp (ref %s) (value X) (libsource (lib Fixture) (part %s)) '
            '(sheetpath (names "%s") (tstamps "x")))' % (ref, part, sheet))


def _lp(part, pins):
    ps = ''.join('(pin (num "%s") (name p%s) (type %s))' % (n, n, t) for (n, t) in pins)
    return '(libpart (lib Fixture) (part %s) (pins %s))' % (part, ps)


def _net(name, code, nodes):
    ns = ''.join('(node (ref %s) (pin "%s"))' % (r, p) for (r, p) in nodes)
    return '(net (code %d) (name "%s") %s)' % (code, name, ns)


def _fixture_netlist():
    comps = [_comp('J1', 'CONN'), _comp('F1', 'FUSE'), _comp('U1', 'BUCK'),
             _comp('U2', 'LDO'), _comp('U3', 'MCU'), _comp('U4', 'RADIO'),
             _comp('U5', 'SENSOR'), _comp('Q1', 'FET'), _comp('U6', 'HEATER'),
             _comp('U7', 'LAMP'), _comp('U8', 'CPLOAD'), _comp('U9', 'DAC')]
    comps += [_comp('U1%d' % i, 'CHAN', '/CH%d/' % i) for i in range(4)]
    lps = [_lp('CONN', [(1, 'passive'), (2, 'passive')]),
           _lp('FUSE', [(1, 'passive'), (2, 'passive')]),
           _lp('BUCK', [(1, 'power_in'), (2, 'power_out'), (3, 'power_in')]),
           _lp('LDO', [(1, 'power_in'), (2, 'power_out'), (3, 'power_in')]),
           _lp('MCU', [(1, 'power_in'), (2, 'power_in')]),
           _lp('RADIO', [(1, 'power_in'), (2, 'power_in')]),
           _lp('SENSOR', [(1, 'power_in'), (2, 'power_in')]),
           _lp('FET', [(1, 'passive'), (2, 'passive'), (3, 'passive')]),
           _lp('HEATER', [(1, 'power_in'), (2, 'power_in')]),
           _lp('LAMP', [(1, 'power_in'), (2, 'power_in')]),
           _lp('CPLOAD', [(1, 'power_in'), (2, 'power_in')]),
           _lp('DAC', [(1, 'power_in')]),
           _lp('CHAN', [(1, 'power_in'), (2, 'power_in')])]
    nets = [_net('VIN_RAW', 1, [('J1', '1'), ('F1', '1')]),
            _net('VIN', 2, [('F1', '2'), ('U1', '1')]),
            _net('V5', 3, [('U1', '2'), ('U2', '1'), ('U8', '1')]
                 + [('U1%d' % i, '1') for i in range(4)]),
            _net('V3', 4, [('U2', '2'), ('U3', '1'), ('U4', '1'), ('U5', '1'), ('Q1', '1')]),
            _net('VSW', 5, [('Q1', '2'), ('U6', '1'), ('U7', '1')]),
            _net('GND', 6, [('U1', '3'), ('U2', '3'), ('U3', '2'), ('U4', '2'),
                            ('U5', '2'), ('Q1', '3'), ('U6', '2'), ('U7', '2'), ('U8', '2')]
                 + [('U1%d' % i, '2') for i in range(4)]),
            _net('Net-(U9-VDD)', 7, [('U9', '1')])]
    return ('(export (version E) (components %s) (libparts %s) (nets %s))'
            % (' '.join(comps), ' '.join(lps), ' '.join(nets)))


def _fixture_power():
    raw = [
        {'net': 'VIN_RAW', 'root': {'kind': 'external', 'ref': 'J1'},
         'protective': {'F1': {'rating_a': 2.0}}},
        {'net': 'VIN', 'voltage': {'nominal_v': 12, 'min_v': 10.8, 'max_v': 13.2},
         'regulators': {'U1': {'type': 'buck', 'eta_min': 0.85}}},
        {'net': 'V5', 'voltage': {'nominal_v': 5, 'min_v': 4.75, 'max_v': 5.25},
         'regulators': {'U2': {'type': 'ldo', 'iq_a': 0.001}},
         'loads': dict({'U8': {'peak_a': 0.5, 'sustained_a': 0.5, 'kind': 'constant_power'}},
                       **{'U1%d' % i: {'peak_a': 0.1, 'sustained_a': 0.1} for i in range(4)})},
        {'net': 'V3', 'voltage': {'nominal_v': 3.3, 'min_v': 3.135, 'max_v': 3.465},
         'loads': {'U3': {'peak_a': 0.2, 'sustained_a': 0.1},
                   'U4': {'peak_a': 1.2, 'sustained_a': 0.3, 'modes': ['TX']},
                   'U5': {'peak_a': 0.05, 'sustained_a': 0.05}},
         'conduits': {'Q1': {'r_mohm': 25, 'rating_a': 3}}},
        {'net': 'VSW', 'voltage': {'nominal_v': 3.3, 'min_v': 3.135, 'max_v': 3.465},
         'loads': {'U6': {'peak_a': 0.8, 'sustained_a': 0.8, 'kind': 'resistive', 'modes': ['HEAT']},
                   'U7': {'peak_a': 0.5, 'sustained_a': 0.5, 'modes': ['LAMP']}}},
    ]
    return [markings._check_power_entry(e, i) for i, e in enumerate(raw)]


def _tree():
    parsed = powertree.parse_netlist(_fixture_netlist())
    return powertree.compute_currents(powertree.build_tree(parsed, _fixture_power()))


def test_hand_worked_wcca_table_exact():
    t = _tree()
    d = {n: (r['design']['peak_a']['design_a'], r['design']['sustained_a']['design_a'])
         for n, r in t['rails'].items()}
    assert d['VSW'] == (0.84, 0.84), d['VSW']
    assert d['V3'] == (1.45, 0.99), d['V3']
    assert abs(d['V5'][0] - 2.377316) < 1e-6 and abs(d['V5'][1] - 1.917316) < 1e-6, d['V5']
    assert abs(d['VIN'][0] - 1.359576) < 1e-6 and abs(d['VIN'][1] - 1.096504) < 1e-6, d['VIN']
    assert d['VIN_RAW'] == (2.0, 2.0), d['VIN_RAW']  # fuse floor, not the load sum
    e = {x['ref']: x for x in t['edges']}
    assert e['F1']['kind'] == 'protective' and e['F1']['design'] == {'peak_a': 2.0, 'sustained_a': 2.0}
    assert e['U1']['kind'] == 'buck' and abs(e['U1']['design']['peak_a'] - 1.359576) < 1e-6
    assert e['U2']['kind'] == 'ldo' and e['U2']['design'] == {'peak_a': 1.451, 'sustained_a': 0.991}
    assert e['Q1']['kind'] == 'conduit' and e['Q1']['design'] == {'peak_a': 0.84, 'sustained_a': 0.84}
    # mutually exclusive branches are never summed; modes are visible
    assert t['rails']['V3']['design']['peak_a']['by_mode'] == {
        'BASE': 0.25, 'HEAT': 1.09, 'LAMP': 0.75, 'TX': 1.45}
    # cornered load values
    assert t['rails']['V5']['loads']['U8']['peak_cornered_a'] == 0.526316
    assert t['rails']['VSW']['loads']['U6']['peak_cornered_a'] == 0.84
    # hierarchy: four channel instances counted, sheetpaths recorded
    chan = [r for r in t['rails']['V5']['loads'] if r.startswith('U1')]
    assert len(chan) == 4
    assert t['rails']['V5']['loads']['U10']['sheetpath'] == '/CH0/'


def test_flags_and_completeness():
    t = _tree()
    kinds = {(f['kind'], f.get('net')) for f in t['flags']}
    assert ('dangling_power_in', 'Net-(U9-VDD)') in kinds
    assert ('rail_without_source', 'Net-(U9-VDD)') in kinds
    assert not any(f['kind'] == 'rail_without_source' and f.get('net') != 'Net-(U9-VDD)'
                   for f in t['flags'])
    assert t['rails']['V3']['complete'] is True
    assert t['rails']['Net-(U9-VDD)']['complete'] is False
    assert t['rails']['Net-(U9-VDD)']['unknown_loads'] == ['U9']
    assert 'GND' not in t['rails']


def test_unknown_currents_never_invented():
    power = _fixture_power()
    for e in power:
        if e['net'] == 'V3':
            del e['loads']['U4']  # radio current undeclared
    parsed = powertree.parse_netlist(_fixture_netlist())
    t = powertree.compute_currents(powertree.build_tree(parsed, power))
    v3 = t['rails']['V3']
    assert v3['complete'] is False and v3['unknown_loads'] == ['U4']
    # the design value is the KNOWN sum only (0.25 base / HEAT 1.09)
    assert v3['design']['peak_a']['design_a'] == 1.09
