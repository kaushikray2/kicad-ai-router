"""Regression tests for #135 (third review B10): a declared regulator
that touches a third net (FB/EN/SW...) used to get NO edge -- the
closure pulled the third net into the power set, the regulator then
spanned 3+ 'rails', and its input rail still reported complete with
the regulator's whole input current missing.  Edges are now built
from the regulator's power_in / power_out rails only, and a declared
regulator that yields no edge fails closed (flag + incomplete rail)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import markings, powertree


def _comp(ref, part):
    return ('(comp (ref %s) (value X) (libsource (lib Fixture) (part %s)) '
            '(sheetpath (names "/") (tstamps "x")))' % (ref, part))


def _lp(part, pins):
    ps = ''.join('(pin (num "%s") (name p%s) (type %s))' % (n, n, t) for (n, t) in pins)
    return '(libpart (lib Fixture) (part %s) (pins %s))' % (part, ps)


def _net(name, code, nodes):
    ns = ''.join('(node (ref %s) (pin "%s"))' % (r, p) for (r, p) in nodes)
    return '(net (code %d) (name "%s") %s)' % (code, name, ns)


def _nl(comps, lps, nets):
    return ('(export (version E) (components %s) (libparts %s) (nets %s))'
            % (' '.join(comps), ' '.join(lps), ' '.join(nets)))


def _check(raw):
    return [markings._check_power_entry(e, i) for i, e in enumerate(raw)]


def _buck_tree():
    # 6-pin buck: VIN=power_in, VOUT=power_out (the model's pin
    # assumption, as in test_powertree), SW=output via an inductor,
    # FB=input, EN=input, GND=power_in.
    comps = [_comp('J1', 'CONN'), _comp('U1', 'BUCK5'), _comp('U3', 'LOAD'),
             _comp('R1', 'RES'), _comp('R2', 'RES'), _comp('L1', 'IND')]
    lps = [_lp('CONN', [(1, 'passive'), (2, 'power_in')]),
           _lp('BUCK5', [(1, 'power_in'), (2, 'power_out'), (3, 'input'),
                         (4, 'input'), (5, 'power_in'), (6, 'output')]),
           _lp('LOAD', [(1, 'power_in'), (2, 'power_in')]),
           _lp('RES', [(1, 'passive'), (2, 'passive')]),
           _lp('IND', [(1, 'passive'), (2, 'passive')])]
    nets = [_net('+5V', 1, [('J1', '1'), ('U1', '1')]),
            _net('SW', 2, [('U1', '6'), ('L1', '1')]),
            _net('3V3', 3, [('U1', '2'), ('L1', '2'), ('U3', '1'), ('R1', '1')]),
            _net('FB', 4, [('U1', '3'), ('R1', '2'), ('R2', '1')]),
            _net('EN', 5, [('U1', '4'), ('R2', '2')]),
            _net('GND', 6, [('J1', '2'), ('U1', '5'), ('U3', '2')])]
    power = _check([
        {'net': '+5V', 'root': {'kind': 'external', 'ref': 'J1'},
         'voltage': {'nominal_v': 5, 'min_v': 4.75, 'max_v': 5.25}},
        {'net': '3V3', 'voltage': {'nominal_v': 3.3, 'min_v': 3.135, 'max_v': 3.465},
         'regulators': {'U1': {'type': 'buck', 'eta_min': 0.85}},
         'loads': {'U3': {'peak_a': 2.0, 'sustained_a': 2.0}}}])
    parsed = powertree.parse_netlist(_nl(comps, lps, nets))
    return powertree.compute_currents(powertree.build_tree(parsed, power))


def test_buck_with_fb_en_sw_gets_its_edge():
    tree = _buck_tree()
    edges = [e for e in tree['edges'] if e['ref'] == 'U1']
    assert len(edges) == 1, tree['edges']
    e = edges[0]
    assert e['kind'] == 'buck' and e['from'] == '+5V' and e['to'] == '3V3', e
    # the buck's input current is IN the +5V rail's design current:
    # 3.3 V x 2 A / (0.85 x 4.75 V) = 1.63 A (nominal-Vout corner;
    # a later corner change may raise it, never remove it)
    d = tree['rails']['+5V']['design']['peak_a']['design_a']
    assert 1.5 < d < 1.9, d
    assert tree['rails']['+5V']['complete'] is True


def test_non_power_nets_do_not_become_rails():
    tree = _buck_tree()
    for bogus in ('FB', 'EN', 'SW'):
        assert bogus not in tree['rails'], (bogus, sorted(tree['rails']))


def test_five_pin_ldo_with_en_and_bypass():
    comps = [_comp('J1', 'CONN'), _comp('U2', 'LDO5'), _comp('U4', 'LOAD'),
             _comp('C1', 'CAP')]
    lps = [_lp('CONN', [(1, 'passive'), (2, 'power_in')]),
           _lp('LDO5', [(1, 'power_in'), (2, 'power_out'), (3, 'power_in'),
                        (4, 'input'), (5, 'input')]),
           _lp('LOAD', [(1, 'power_in'), (2, 'power_in')]),
           _lp('CAP', [(1, 'passive'), (2, 'passive')])]
    nets = [_net('VIN2', 1, [('J1', '1'), ('U2', '1')]),
            _net('VOUT2', 2, [('U2', '2'), ('U4', '1')]),
            _net('EN2', 3, [('U2', '4')]),
            _net('BYP', 4, [('U2', '5'), ('C1', '1')]),
            _net('GND', 5, [('J1', '2'), ('U2', '3'), ('U4', '2'), ('C1', '2')])]
    power = _check([
        {'net': 'VIN2', 'root': {'kind': 'external', 'ref': 'J1'},
         'voltage': {'nominal_v': 5, 'min_v': 4.75, 'max_v': 5.25}},
        {'net': 'VOUT2', 'voltage': {'nominal_v': 1.8, 'min_v': 1.746, 'max_v': 1.854},
         'regulators': {'U2': {'type': 'ldo', 'iq_a': 0.001}},
         'loads': {'U4': {'peak_a': 0.5, 'sustained_a': 0.5}}}])
    parsed = powertree.parse_netlist(_nl(comps, lps, nets))
    tree = powertree.compute_currents(powertree.build_tree(parsed, power))
    edges = [e for e in tree['edges'] if e['ref'] == 'U2']
    assert len(edges) == 1 and edges[0]['kind'] == 'ldo', tree['edges']
    assert 'EN2' not in tree['rails'] and 'BYP' not in tree['rails']
    d = tree['rails']['VIN2']['design']['peak_a']['design_a']
    assert abs(d - 0.501) < 0.01, d


def test_declared_regulator_without_edge_fails_closed():
    # declared buck whose power_out pin lands on the RETURN net only:
    # no output rail exists, so no edge can be built.
    comps = [_comp('J1', 'CONN'), _comp('U1', 'BUCKX'), _comp('U3', 'LOAD')]
    lps = [_lp('CONN', [(1, 'passive'), (2, 'power_in')]),
           _lp('BUCKX', [(1, 'power_in'), (2, 'power_out'), (3, 'power_in')]),
           _lp('LOAD', [(1, 'power_in'), (2, 'power_in')])]
    nets = [_net('VIN', 1, [('J1', '1'), ('U1', '1'), ('U3', '1')]),
            _net('GND', 2, [('J1', '2'), ('U1', '2'), ('U1', '3'), ('U3', '2')])]
    power = _check([
        {'net': 'VIN', 'root': {'kind': 'external', 'ref': 'J1'},
         'voltage': {'nominal_v': 5, 'min_v': 4.75, 'max_v': 5.25},
         'regulators': {'U1': {'type': 'buck', 'eta_min': 0.85}},
         'loads': {'U3': {'peak_a': 0.1, 'sustained_a': 0.1}}}])
    parsed = powertree.parse_netlist(_nl(comps, lps, nets))
    tree = powertree.build_tree(parsed, power)
    assert not [e for e in tree['edges'] if e['ref'] == 'U1'], tree['edges']
    assert any(f['kind'] == 'regulator_without_edge' and f['ref'] == 'U1'
               for f in tree['flags']), tree['flags']
    assert tree['rails']['VIN']['complete'] is False
