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

"""Author the fixture sidecar THROUGH the editor's model layer --
the same form conversions and save path the Markings dialog uses
(no hand-written JSON).  Regenerates
fixture.ai-router-markings.json beside the fixture board.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from ai_router import board_index, markings_model as mm

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, 'fixture.kicad_pcb')


def main():
    index = board_index.load_index(BOARD)
    model = mm.MarkingsEditorModel({}, index)
    model.add_entry('impedance', mm.impedance_entry(
        {'net': 'RF_IN', 'z0': 50, 'role': 'generic', 'dk_basis': 'design'}))
    model.add_entry('impedance', mm.impedance_entry(
        {'net': 'RF_FAIL', 'z0': 30}))  # deliberate miss: routed width will not solve to 30 ohm
    model.add_entry('differential', mm.differential_entry(
        {'name': 'CLK', 'net_a': 'CLK_P', 'net_b': 'CLK_N', 'zdiff': 100}))
    model.add_entry('hv_domains', mm.hv_entry(
        {'name': 'HVPRI', 'nets': ['HV_A'], 'peak_v': 400, 'coating': 'uncoated',
         'pollution_degree': 2, 'materialise': True}))
    model.add_entry('hv_domains', mm.hv_entry(
        {'name': 'HVSEC', 'nets': ['HV_B'], 'peak_v': 0, 'materialise': False}))
    model.add_entry('length_groups', mm.length_entry(
        {'name': 'LENGRP', 'members_mode': 'nets', 'nets': ['LEN_A', 'LEN_B'],
         'target_mode': 'longest', 'tolerance': 10, 'tolerance_unit': 'mm'}))
    model.add_entry('quiet', mm.quiet_entry(
        {'mode': 'net', 'net': 'QUIET_X', 'profile': 'crystal',
         'isolation_mm': 1.5, 'materialise': True}))
    model.add_entry('power', mm.power_entry(
        {'net': 'VCC5', 'has_voltage': True, 'nominal_v': 5, 'min_v': 4.75, 'max_v': 5.25,
         'root_ref': 'U17', 'root_pin': '1',
         'drop_budget_v': 0.25,
         'loads': [{'ref': 'U18', 'kind': 'constant_current', 'peak_a': 0.4,
                    'sustained_a': 0.3, 'mpn': '', 'datasheet_rev': ''}],
         'regulators': [], 'conduits': [], 'protective': []}))
    model.set_power_corners({'ambient_c': 40.0})
    err = model.validate_working()
    assert err is None, err
    out = model.save(mm.sidecar_path(BOARD))
    print('sidecar written:', out)


if __name__ == '__main__':
    main()
