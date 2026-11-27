# Copyright (C) 2026 SiliconBrane Inc.
#
# This file is part of KiCad AI Router.
#
# KiCad AI Router is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# KiCad AI Router is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KiCad AI Router. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""#68 closure corrections: (a) a declared {"unknown": true} load
is valid, mutually exclusive with peak_a/kb, and behaves in
powertree exactly like an undeclared load; (b) root.pin is
validated and honoured by powerplan (only the named pad sources
the rail; a pin not on the net is flagged, never silently
ignored); (c) tool_power's recorded-only early returns carry
marked_nets and the report names the marked rails."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import markings, markings_model as mm, powerplan, powertree


def _doc(load=None, root=None):
    e = {'net': '+5V'}
    if load is not None:
        e['loads'] = {'U1': load}
    if root is not None:
        e['root'] = root
    return {'version': 1, 'power': [e]}


def test_unknown_load_validation():
    out = markings.validate(_doc({'unknown': True}))
    assert out['power'][0]['loads']['U1']['unknown'] is True
    for bad in ({'unknown': True, 'peak_a': 1.0},
                {'unknown': True, 'kb': {'mpn': 'X', 'datasheet_rev': '1'}},
                {'unknown': False}, {'unknown': 'yes'}):
        try:
            markings.validate(_doc(bad))
        except markings.MarkingsError:
            pass
        else:
            raise AssertionError('load %r accepted' % (bad,))


def test_root_validation():
    markings.validate(_doc(root={'kind': 'external', 'ref': 'J1'}))
    markings.validate(_doc(root={'ref': 'J1', 'pin': '2'}))
    for bad in ({'pin': '2'}, {'ref': ''}, {'ref': 5},
                {'ref': 'J1', 'pin': 2}):
        try:
            markings.validate(_doc(root=bad))
        except markings.MarkingsError:
            pass
        else:
            raise AssertionError('root %r accepted' % (bad,))


_NETLIST = ('(export (version E) (components '
            '(comp (ref J1) (value X) (libsource (lib Fixture) (part SRC)) (sheetpath (names "/") (tstamps "x"))) '
            '(comp (ref U1) (value X) (libsource (lib Fixture) (part LOAD)) (sheetpath (names "/") (tstamps "x")))) '
            '(libparts '
            '(libpart (lib Fixture) (part SRC) (pins (pin (num "1") (name p1) (type power_out)) (pin (num "2") (name p2) (type power_in)))) '
            '(libpart (lib Fixture) (part LOAD) (pins (pin (num "1") (name p1) (type power_in)) (pin (num "2") (name p2) (type power_in))))) '
            '(nets '
            '(net (code 1) (name "+5V") (node (ref J1) (pin "1")) (node (ref U1) (pin "1"))) '
            '(net (code 2) (name "GND") (node (ref J1) (pin "2")) (node (ref U1) (pin "2")))))')


def test_powertree_unknown_is_undeclared():
    parsed = powertree.parse_netlist(_NETLIST)
    for entry in ({'net': '+5V', 'loads': {'U1': {'unknown': True}}},
                  {'net': '+5V'}):
        tree = powertree.build_tree(parsed, [entry])
        rail = tree['rails']['+5V']
        assert rail['unknown_loads'] == ['U1'], rail
        assert rail['loads'] == {} and rail['complete'] is False
    tree = powertree.build_tree(parsed, [
        {'net': '+5V', 'loads': {'U1': {'peak_a': 1.0, 'sustained_a': 1.0}}}])
    assert tree['rails']['+5V']['complete'] is True


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


def _plan(root):
    rail = {'net': 'V5', 'complete': True, 'unknown_loads': [],
            'loads': {'U2': _load(2.0, 2.0)},
            'load_pins': {'U2': [('U2', '1')]},
            'source_pins': {}, 'sources': [], 'candidate_sources': [],
            'root': root,
            'voltage': {'nominal_v': 5.0, 'min_v': 4.75, 'max_v': 5.25},
            'design': {}}
    tree = {'modes': ['BASE'], 'return_net': 'GND', 'flags': [],
            'edges': [], 'rails': {'V5': rail},
            'return_pins': {'U2': [('U2', '2')], 'C1': [('C1', '2')]},
            'components': {'U2': {'value': 'MCU'}, 'C1': {'value': '100n', 'footprint': ''}}}
    parsed = {'components': tree['components'],
              'nets': {'V5': [('J1', '1'), ('J1', '2'), ('U2', '1'), ('C1', '1')],
                       'GND': [('U2', '2'), ('C1', '2')]}}
    v5 = {'tracks': [
              {'uuid': 't1', 'layer': 'F.Cu', 'x1': 0, 'y1': 0, 'x2': 10, 'y2': 0, 'w': 0.3},
              {'uuid': 't2', 'layer': 'F.Cu', 'x1': 10, 'y1': 0, 'x2': 20, 'y2': 0, 'w': 0.3}],
          'vias': [],
          'pads': [{'ref': 'J1', 'pin': '1', 'x': 0, 'y': 0, 'layers': ['F.Cu']},
                   {'ref': 'J1', 'pin': '2', 'x': 10, 'y': 0, 'layers': ['F.Cu']},
                   {'ref': 'U2', 'pin': '1', 'x': 20, 'y': 0, 'layers': ['F.Cu']},
                   {'ref': 'C1', 'pin': '1', 'x': 19, 'y': 1, 'layers': ['F.Cu']}],
          'zones': []}
    gnd = {'tracks': [], 'vias': [{'x': 19.5, 'y': 2.0, 'drill': 0.3}],
           'pads': [{'ref': 'U2', 'pin': '2', 'x': 19.8, 'y': 1.6, 'layers': ['F.Cu']},
                    {'ref': 'C1', 'pin': '2', 'x': 19, 'y': 2, 'layers': ['F.Cu']}],
           'zones': [{'layer': 'F.Cu', 'polys': [[(-5, -5), (30, -5), (30, 20), (-5, 20)]]}]}
    return powerplan.plan_power(tree, parsed, {'V5': v5, 'GND': gnd},
                                LAYERS, CORNERS, {'V5': {}})


def test_root_pin_filters_source_pads():
    # pin 1 at the far end: the whole trunk carries the load
    segs = {s['uuid']: s for s in _plan({'ref': 'J1', 'pin': '1'})['rails']['V5']['segments']}
    assert segs['t1']['i_peak_a'] == 2.0 and segs['t2']['i_peak_a'] == 2.0, segs
    # pin 2 at the midpoint: only t2 carries it
    segs = {s['uuid']: s for s in _plan({'ref': 'J1', 'pin': '2'})['rails']['V5']['segments']}
    assert segs['t1']['i_peak_a'] == 0.0 and segs['t2']['i_peak_a'] == 2.0, segs


def test_root_pin_not_on_net_is_flagged():
    rep = _plan({'ref': 'J1', 'pin': '9'})['rails']['V5']
    kinds = [f['kind'] for f in rep['flags']]
    assert 'declared_root_pin_not_on_net' in kinds, rep['flags']
    flag = [f for f in rep['flags'] if f['kind'] == 'declared_root_pin_not_on_net'][0]
    assert flag['ref'] == 'J1' and flag['pin'] == '9'


def test_summarize_names_marked_nets():
    rep = {'power': {'status': 'needs_stackup',
                     'marked_nets': ['VIN', '+3V3']}}
    rows = mm.summarize_report(rep)['rows']
    assert rows and rows[0]['marking'] == 'Power \u2014 +3V3, VIN', rows


def test_tool_power_early_return_carries_marked_nets(tmp_path):
    import pcbnew
    V = pcbnew.VECTOR2I
    b = pcbnew.BOARD()
    n = pcbnew.NETINFO_ITEM(b, '+3V3')
    b.Add(n)
    path = str(tmp_path / 'p.kicad_pcb')
    with open(path.replace('.kicad_pcb', '.kicad_pro'), 'w') as fh:
        fh.write('{}\n')
    pcbnew.SaveBoard(path, b)
    with open(path.replace('.kicad_pcb', '.ai-router-markings.json'), 'w') as fh:
        json.dump({'version': 1, 'power': [
            {'net': '+3V3', 'loads': {'U1': {'unknown': True}}}]}, fh)
    from ai_router.pipeline import Pipeline
    pipe = Pipeline({'strip_existing': False,
                     'tracemaker_path': '/nonexistent'},
                    path, lambda m: None)
    out = pipe.tool_power()
    assert out['status'] == 'netlist_unavailable', out
    assert out['marked_nets'] == ['+3V3'], out


if __name__ == '__main__':
    test_unknown_load_validation()
    test_root_validation()
    test_powertree_unknown_is_undeclared()
    test_root_pin_filters_source_pads()
    test_root_pin_not_on_net_is_flagged()
    test_summarize_names_marked_nets()
    print('power unknown/root.pin OK')
