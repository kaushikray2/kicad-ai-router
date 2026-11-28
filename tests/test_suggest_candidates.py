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

"""Pre-Exam Polish (plan.md Section C): Suggest-candidates
detector unit tests over synthetic board data. The >= 90%
precision exit on 5 real boards is a measurement, recorded with
the lane's results -- these tests pin the detection rules and
the never-invents invariant (no current anywhere, peak_v always
blank). Run: python3 tests/test_suggest_candidates.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import suggest


def _data(nets, footprints=None):
    return {'nets': nets, 'footprints': footprints or {}}


def test_diff_pair_needs_two_shared_endpoints():
    nets = {'USB_DP': [['U1', '1'], ['J1', '1'], ['R1', '1']],
            'USB_DN': [['U1', '2'], ['J1', '2'], ['R1', '2']]}
    got = suggest.detect_diff_pairs(_data(nets))
    assert len(got) == 1 and got[0]['nets'] == ['USB_DP', 'USB_DN'], got
    assert set(got[0]['shared_endpoints']) == {'U1', 'J1', 'R1'}
    thin = {'A_P': [['U1', '1']], 'A_N': [['U1', '2'], ['J1', '1']]}
    assert suggest.detect_diff_pairs(_data(thin)) == []  # only U1 shared


def test_power_rail_never_invents_a_current():
    nets = {'+3V3': [['U1', '1'], ['U2', '1'], ['C1', '1'], ['R1', '1']],
            'GND': [['U1', '2'], ['U2', '2']],
            'VDD': [['U3', '1']]}
    got = suggest.detect_power_rails(_data(nets))
    assert [c['net'] for c in got] == ['+3V3'], got  # VDD stub too small, GND excluded
    assert got[0]['loads'] == 'unknown'
    assert not any('current' in k or 'amps' in k for k in got[0])


def test_crystal_with_pf_load_caps():
    nets = {'XTAL_IN': [['U1', '1'], ['Y1', '1'], ['C1', '1']],
            'XTAL_OUT': [['U1', '2'], ['Y1', '2'], ['C2', '1']],
            'GND': [['C1', '2'], ['C2', '2']]}
    fps = {'Y1': {'value': '16MHz', 'name': 'Crystal'}, 'U1': {'value': 'MCU', 'name': ''},
           'C1': {'value': '18pF', 'name': ''}, 'C2': {'value': '18pF', 'name': ''}}
    got = suggest.detect_crystals(_data(nets, fps))
    assert len(got) == 1 and got[0]['ref'] == 'Y1', got
    assert got[0]['caps'] == ['C1', 'C2'] and got[0]['confidence'] == 0.90
    fps['C1'] = {'value': '100nF', 'name': ''}  # not a pF load cap any more
    got = suggest.detect_crystals(_data(nets, fps))
    assert len(got) == 1 and got[0]['caps'] == ['C2'] and got[0]['confidence'] == 0.80


def test_rf_chain_and_pi_refs():
    nets = {'ANT': [['J1', '1'], ['L1', '1'], ['C1', '1']],
            'RF_MID': [['L1', '2'], ['R1', '1']],
            'RF_IN': [['R1', '2'], ['U1', '5']],
            'GND': [['C1', '2'], ['C9', '1']]}
    got = suggest.detect_rf_chains(_data(nets))
    assert len(got) == 1 and got[0]['antenna_net'] == 'ANT', got
    assert got[0]['path_nets'] == ['ANT', 'RF_MID', 'RF_IN']
    assert set(got[0]['pi_candidate_refs']) == {'L1', 'C1', 'R1'}  # series + shunt C1
    assert suggest.detect_rf_chains(_data({'RF_IN': [['U1', '5']]})) == []


def test_crystal_ignores_caps_sharing_only_ground():
    # Regression (STM32-RF measurement): the crystal's own GND pads
    # made a VDD_PA decoupler (C7) and an RF shunt (C9) look like
    # load caps. Only caps on the crystal's SIGNAL nets count.
    nets = {'XTAL1': [['Y1', '1'], ['C5', '2']],
            'XTAL2': [['Y1', '3'], ['C4', '2']],
            'GND': [['Y1', '2'], ['Y1', '4'], ['C4', '1'], ['C5', '1'], ['C7', '1'], ['C9', '1']],
            'VDD_PA': [['C7', '2'], ['U4', '1']],
            'RF_MID': [['C9', '2'], ['L1', '1']]}
    fps = {'Y1': {'value': '16MHz', 'name': ''}, 'U4': {'value': 'RF IC', 'name': ''},
           'C4': {'value': '22pF', 'name': ''}, 'C5': {'value': '22pF', 'name': ''},
           'C7': {'value': '4.7pF', 'name': ''}, 'C9': {'value': '1pF', 'name': ''}}
    got = suggest.detect_crystals(_data(nets, fps))
    assert len(got) == 1 and got[0]['caps'] == ['C4', 'C5'], got


def test_rf_chain_needs_a_real_feed_path():
    # Regression (STM32-RF measurement): the radio sat directly on
    # both ANT nets, L2 was the PA feed choke to VDD_PA, and L3
    # linked the two antenna nodes -- no antenna-to-radio chain.
    nets = {'ANT1': [['U4', '1'], ['L2', '1'], ['L3', '2']],
            'ANT2': [['U4', '2'], ['L3', '1'], ['L1', '1']],
            'VDD_PA': [['L2', '2'], ['U4', '3'], ['C7', '2']],
            'GND': [['C7', '1']]}
    assert suggest.detect_rf_chains(_data(nets)) == []
    # ...while a genuine connector -> series L -> radio-pin chain fires.
    nets2 = {'ANT': [['J1', '1'], ['L1', '1']],
             'RF_IN': [['L1', '2'], ['U1', '5']]}
    got = suggest.detect_rf_chains(_data(nets2))
    assert len(got) == 1 and got[0]['path_nets'] == ['ANT', 'RF_IN'], got


def test_bus_group_needs_three_bits():
    nets = {'ADDR%d' % i: [['U1', str(i)]] for i in range(4)}
    nets['DQ0'] = [['U1', '9']]; nets['DQ1'] = [['U1', '10']]
    got = suggest.detect_bus_groups(_data(nets))
    assert len(got) == 1 and got[0]['base'] == 'ADDR', got
    assert got[0]['nets'] == ['ADDR0', 'ADDR1', 'ADDR2', 'ADDR3']


def test_hv_candidates_are_conservative_and_blank():
    nets = {'HV_BUS': [['J1', '1']], 'MAINS_L': [['J1', '2']],
            '+48V': [['U1', '1']], 'VBUS': [['U1', '2']], '12V': [['U1', '3']]}
    got = suggest.detect_hv_candidates(_data(nets))
    names = sorted(c['net'] for c in got)
    assert names == ['HV_BUS', 'MAINS_L'], names  # 48 V and below stay out
    assert all(c['peak_v'] == '' for c in got)


def test_aggregator_sorts_and_handles_empty():
    assert suggest.suggest_candidates(None) == []
    assert suggest.suggest_candidates({'nets': {}, 'footprints': {}}) == []
    assert suggest.load_board_data('/nonexistent/board.kicad_pcb') is None


if __name__ == '__main__':
    test_diff_pair_needs_two_shared_endpoints()
    test_power_rail_never_invents_a_current()
    test_crystal_with_pf_load_caps()
    test_rf_chain_and_pi_refs()
    test_crystal_ignores_caps_sharing_only_ground()
    test_rf_chain_needs_a_real_feed_path()
    test_bus_group_needs_three_bits()
    test_hv_candidates_are_conservative_and_blank()
    test_aggregator_sorts_and_handles_empty()
    print('suggest candidates OK')
