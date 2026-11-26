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

"""Model-layer tests for the engineered-nets GUI: the editor model
round-trips through markings.validate/save_markings, unmatched /
rebind, radio exclusivity, dirty state, Screen 1 conditions, and
the verdict summariser's derivation rules.  Pure python -- the same
code paths the wx dialogs bind to."""
import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from ai_router import markings
from ai_router import markings_model as mm

INDEX = {'nets': ['RF_IN', 'RF_FAIL', 'CLK_P', 'CLK_N', 'HV_A', 'HV_B',
                   'LEN_A', 'LEN_B', 'QUIET_X', 'VCC5', 'GND'],
         'footprints': [
             {'ref': 'U17', 'value': 'SRC5', 'pads': {'1': 'VCC5', '2': 'GND'},
              'properties': {'AIRuleDecapMm': '3.0'}},
             {'ref': 'U18', 'value': 'LOAD', 'pads': {'1': 'VCC5', '2': 'GND'},
              'properties': {}}],
         'layers': ['F.Cu', 'B.Cu']}


def _full_model():
    m = mm.MarkingsEditorModel({}, copy.deepcopy(INDEX))
    m.add_entry('impedance', mm.impedance_entry({'net': 'RF_IN', 'z0': 50, 'role': 'generic'}))
    m.add_entry('impedance', mm.impedance_entry(
        {'net': 'RF_FAIL', 'z0': 30, 'model': 'coplanar', 'gap_mm': 0.2, 'fence': True}))
    m.add_entry('differential', mm.differential_entry(
        {'name': 'CLK', 'net_a': 'CLK_P', 'net_b': 'CLK_N', 'zdiff': 100}))
    m.add_entry('hv_domains', mm.hv_entry(
        {'name': 'HVPRI', 'nets': ['HV_A'], 'peak_v': 400, 'materialise': True}))
    m.add_entry('hv_domains', mm.hv_entry(
        {'name': 'HVSEC', 'nets': ['HV_B'], 'peak_v': 0}))
    m.add_entry('length_groups', mm.length_entry(
        {'name': 'LENGRP', 'members_mode': 'nets', 'nets': ['LEN_A', 'LEN_B'],
         'target_mode': 'longest', 'tolerance': 10, 'tolerance_unit': 'mm'}))
    m.add_entry('quiet', mm.quiet_entry(
        {'mode': 'net', 'net': 'QUIET_X', 'profile': 'crystal',
         'isolation_mm': 1.5, 'materialise': True}))
    m.add_entry('power', mm.power_entry(
        {'net': 'VCC5', 'has_voltage': True, 'nominal_v': 5, 'root_ref': 'U17',
         'root_pin': '1', 'drop_budget_v': 0.25,
         'loads': [{'ref': 'U18', 'kind': 'constant_current', 'peak_a': 0.4}],
         'regulators': [], 'conduits': [], 'protective': []}))
    return m


def test_roundtrip_all_sections(tmp_path):
    m = _full_model()
    assert m.validate_working() is None
    board = str(tmp_path / 'b.kicad_pcb')
    path = mm.sidecar_path(board)
    m.save(path)
    loaded = markings.load_markings(board)
    assert loaded is not None
    assert loaded.data == m.data
    # every section survived, in the pipeline's own shapes
    assert loaded.data['power'][0]['loads'] == {'U18': {'kind': 'constant_current', 'peak_a': 0.4, 'sustained_a': 0.4}}
    assert loaded.data['hv_domains'][0]['peak_v'] == 400.0
    assert loaded.data['impedance'][1]['gap_mm'] == 0.2
    assert loaded.data['impedance'][1]['fence'] is True


def test_dirty_state():
    m = mm.MarkingsEditorModel({}, copy.deepcopy(INDEX))
    assert not m.dirty
    m.add_entry('quiet', mm.quiet_entry({'mode': 'net', 'net': 'QUIET_X',
                                         'isolation_mm': 1.0}))
    assert m.dirty


def test_unmatched_and_rebind():
    m = mm.MarkingsEditorModel({}, copy.deepcopy(INDEX))
    m.add_entry('impedance', mm.impedance_entry({'net': 'GHOST', 'z0': 50}))
    issues = m.issues()
    assert len(issues) == 1 and issues[0]['missing'] == ['GHOST']
    m.rebind('impedance', 0, 'GHOST', 'RF_IN')
    assert m.issues() == []
    assert m.entries('impedance')[0]['net'] == 'RF_IN'


def test_marked_nets_multi_marking_once():
    m = _full_model()
    marked = m.marked_nets()
    assert set(marked) == {'RF_IN', 'RF_FAIL', 'CLK_P', 'CLK_N', 'HV_A', 'HV_B',
                           'LEN_A', 'LEN_B', 'QUIET_X', 'VCC5'}
    assert len(marked['CLK_P']) == 1  # via the differential entry only


def test_radio_exclusivity():
    e = mm.length_entry({'name': 'G', 'members_mode': 'pair', 'pair': 'CLK',
                         'target_mode': 'longest', 'tolerance': 1})
    assert 'pair' in e and 'nets' not in e
    e2 = mm.length_entry({'name': 'G', 'members_mode': 'nets', 'nets': ['LEN_A', 'LEN_B'],
                          'target_mode': 'longest', 'tolerance': 1})
    assert 'nets' in e2 and 'pair' not in e2
    q = mm.quiet_entry({'mode': 'ref', 'ref': 'U17', 'isolation_mm': 1})
    assert 'ref' in q and 'net' not in q
    q2 = mm.quiet_entry({'mode': 'net', 'net': 'QUIET_X', 'isolation_mm': 1})
    assert 'net' in q2 and 'ref' not in q2


def test_length_tolerance_unit_follows_mode():
    e = mm.length_entry({'name': 'G', 'members_mode': 'nets', 'nets': ['LEN_A', 'LEN_B'],
                         'target_mode': 'delay', 'target_value': 900, 'tolerance': 40,
                         'tolerance_unit': 'mm'})
    assert 'tolerance_ps' in e and 'tolerance_mm' not in e
    e2 = mm.length_entry({'name': 'G', 'members_mode': 'nets', 'nets': ['LEN_A', 'LEN_B'],
                          'target_mode': 'length', 'target_value': 30, 'tolerance': 2,
                          'tolerance_unit': 'ps'})
    assert 'tolerance_mm' in e2 and 'tolerance_ps' not in e2


def test_power_unknown_load_blocks_validation():
    m = mm.MarkingsEditorModel({}, copy.deepcopy(INDEX))
    e = mm.power_entry({'net': 'VCC5', 'loads': [{'ref': 'U18', 'kind': ''}],
                        'regulators': [], 'conduits': [], 'protective': []})
    assert mm.load_is_unknown(e['loads']['U18'])
    m.add_entry('power', e)
    assert m.validate_working() is not None  # the pipeline would reject it too


def test_conditions(tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('(kicad_pcb (setup (stackup (layer "F.Cu"))))')
    c = mm.evaluate_conditions(str(board), {})
    assert c['stackup'] and not c['power_ready']  # no netlist, no schematic
    (tmp_path / 'b.net').write_text('(export)')
    c = mm.evaluate_conditions(str(board), {})
    assert c['netlist'] and c['power_ready']
    (tmp_path / 'b.net').unlink()
    (tmp_path / 'b.kicad_sch').write_text('(kicad_sch)')
    c = mm.evaluate_conditions(str(board), {})
    assert c['schematic'] and c['power_ready']
    c = mm.evaluate_conditions(str(board), {'quiet': [{'materialise': True}, {}]})
    assert c['quiet_materialise'] == (1, 2)


def test_sidecar_states(tmp_path):
    board = str(tmp_path / 'b.kicad_pcb')
    assert mm.load_sidecar_state(board)['state'] == 'none'
    path = mm.sidecar_path(board)
    with open(path, 'w') as fh:
        fh.write('{not json')
    st = mm.load_sidecar_state(board)
    assert st['state'] == 'invalid' and st['error']
    with open(path, 'w') as fh:
        json.dump({'impedance': [{'net': 'RF_IN', 'z0_ohm': 50}]}, fh)
    st = mm.load_sidecar_state(board)
    assert st['state'] == 'valid' and st['data']['impedance'][0]['net'] == 'RF_IN'
    with open(path, 'w') as fh:
        json.dump({'impedance': [{'net': 'RF_IN'}]}, fh)  # missing z0_ohm
    assert mm.load_sidecar_state(board)['state'] == 'invalid'


def test_hv_pairwise_includes_rest_of_board():
    m = _full_model()
    rows = mm.hv_pairwise_rows(m)
    pairs = {(r['a'], r['b']) for r in rows}
    assert ('HVPRI', 'HVSEC') in pairs
    assert any(b == 'Rest of board / unmarked nets' for _a, b in pairs)
    assert [r for r in rows if r['a'] == 'HVPRI' and 'Rest' in r['b']][0]['delta_v'] == 400.0


def test_summarize_report_rules():
    rep = {'nets': {'RF': {'computed_width_mm': 0.36, 'primary_layer': 'F.Cu',
                           'applied_width_mm': 0.28, 'width_verified': False,
                           'check_summary': {'fail': 1, 'warn': 0},
                           'checks': [{'rule': 'z0', 'severity': 'fail',
                                       'detail': 'routed 41 ohm vs 50', 'at': [10.0, 20.0]}]}},
           'differential': [{'name': 'CLK', 'verified': True, 'applied_width_mm': 0.2,
                             'applied_gap_mm': 0.15, 'primary_layer': 'F.Cu',
                             'leg_lengths_mm': {'CLK_P': 22.0, 'CLK_N': 22.1}, 'skew_mm': 0.1}],
           'length_groups': {'groups': [{'name': 'L', 'members': {
               'LEN_A': {'within_tolerance': False, 'delta_mm': 8.0},
               'LEN_B': {'within_tolerance': True, 'delta_mm': 0.0}}}]},
           'hv_domains': {'status': 'violations',
                          'assumptions': [{'domain': 'HVPRI', 'peak_v': 400, 'implicit': False},
                                          {'domain': 'rest', 'peak_v': 0, 'implicit': True}],
                          'pairs': [{'a': 'HVPRI', 'b': 'rest', 'delta_v': 400,
                                     'violations': [{'kind': 'track', 'layer': 'F.Cu',
                                                     'required_mm': 2.5, 'actual_mm': 1.0,
                                                     'at': [5.0, 6.0]}]}]},
           'power': {'status': 'netlist_unavailable', 'net': 'VCC5'},
           'quiet_nets': {'results': [{'name': 'QUIET_X', 'profile': 'crystal',
                                       'isolation_mm': 1.5, 'intrusions': [], 'pass': True}]}}
    audit = {'gaps': ['U17'], 'footprints': {}}
    s = mm.summarize_report(rep, audit)
    assert s['counts'] == {'PASS': 1, 'FAIL': 3, 'WARN': 0, 'LIB GAP': 1, 'CONDITIONAL': 2}
    assert mm.verdict_bar_text(s['counts']) == '1 PASS \u00b7 3 FAIL \u00b7 0 WARN \u00b7 1 LIB GAP \u00b7 2 CONDITIONAL'
    fail_rows = [r for r in s['rows'] if r['verdict'] == 'FAIL']
    assert fail_rows[0]['at'] == (10.0, 20.0)
    assert 'Power \u2014 VCC5' in [r['marking'] for r in s['rows']]
    # unmarked run: no rows, bar hidden
    assert mm.summarize_report(None, None)['rows'] == []
    assert mm.summarize_report({'error': 'boom'}, None)['rows'] == []


def test_units_roundtrip():
    assert abs(mm.ps_to_mm(mm.mm_to_ps(25.4, 3.9), 3.9) - 25.4) < 1e-9


def test_rule_footprints_and_connected_parts():
    m = mm.MarkingsEditorModel({}, copy.deepcopy(INDEX))
    assert m.rule_footprints() == [{'ref': 'U17', 'properties': {'AIRuleDecapMm': '3.0'}}]
    parts = m.connected_parts('VCC5')
    assert [p['ref'] for p in parts] == ['U17', 'U18']
    assert m.nets_for_ref('U17') == ['GND', 'VCC5']
