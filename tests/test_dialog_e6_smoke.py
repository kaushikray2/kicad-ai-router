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

"""xvfb smoke for #206: unmatched net names survive form loads in
all three net-picker forms; the differential form pre-fills Net B
from the auto-pair candidate and shows a live solved preview; the
HV pairwise preview carries the Required spacing column and
reflects live form edits.  Standalone:

    xvfb-run -a python3 tests/test_dialog_e6_smoke.py
"""
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wx
from ai_router import board_index, markings, markings_dialog
from ai_router import markings_model as mm

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eng_gui_fixture')


def _select(dlg, net):
    for i in range(dlg.list.GetItemCount()):
        if dlg.list.GetItemText(i, 0) == net:
            evt = wx.ListEvent(wx.EVT_LIST_ITEM_SELECTED.typeId, dlg.list.GetId())
            evt.SetIndex(i)
            dlg.list.GetEventHandler().ProcessEvent(evt)
            wx.Yield()
            return
    raise AssertionError('net row not found: %s' % net)


def main():
    app = wx.App(False)
    d = tempfile.mkdtemp(prefix='e6-smoke-')
    for f in ('fixture.kicad_pcb', 'fixture.kicad_pro', 'fixture.net',
              'fixture.ai-router-markings.json'):
        shutil.copy(os.path.join(FIX, f), d)
    board = os.path.join(d, 'fixture.kicad_pcb')
    index = board_index.load_index(board)
    data = markings.load_markings(board).data
    dlg = markings_dialog.MarkingsEditorDialog(
        None, board, data, index, banned_layers=set(),
        conditions=mm.evaluate_conditions(board, data))
    try:
        dlg.Show()
        wx.Yield()
        # Unmatched preservation in all three net-picker forms.
        _select(dlg, 'RF_IN')
        dlg.form.set_entry({'net': 'GHOST_RF', 'z0_ohm': 50.0})
        dlg._pending.shown(dlg.form.form_dict())  # model the show-path snapshot
        assert dlg.form.form_dict()['net'] == 'GHOST_RF', dlg.form.form_dict()
        _select(dlg, 'CLK_P')
        assert type(dlg.form).__name__ == 'DifferentialForm'
        dlg.form.set_entry({'name': 'CLK', 'nets': ['CLK_P', 'GHOST_N'],
                            'zdiff_ohm': 100.0})
        fd = dlg.form.form_dict()
        assert fd['net_b'] == 'GHOST_N', fd
        dlg._pending.shown(fd)
        # Differential solved preview solves on the fixture stackup.
        label = dlg.form.preview.GetLabel()
        assert label.startswith('Solved:'), label
        _select(dlg, 'QUIET_X')
        dlg.form.set_entry({'net': 'GHOST_Q', 'profile': 'crystal',
                            'isolation_mm': 1.0})
        assert dlg.form.form_dict()['net'] == 'GHOST_Q', dlg.form.form_dict()
        dlg._pending.shown(dlg.form.form_dict())
        # Auto-pair pre-fill on a fresh differential form.
        dlg._new_entry('differential')
        assert type(dlg.form).__name__ == 'DifferentialForm'
        markings_dialog._set_net_choice(dlg.form.net_a, dlg.form.nets(), 'CLK_P')
        dlg.form._on_a()
        assert dlg.form.form_dict()['net_b'] == 'CLK_N', dlg.form.form_dict()
        dlg._pending.cleared()  # discard the fresh form before switching
        # HV pairwise: Required column + live reflection.
        _select(dlg, 'HV_A')
        assert type(dlg.form).__name__ == 'HVForm'
        assert dlg.form.pairs.GetColumnCount() == 4
        texts = []
        for i in range(dlg.form.pairs.GetItemCount()):
            texts.append([dlg.form.pairs.GetItemText(i, c) for c in range(4)])
        assert any(r[3] == '2.50' for r in texts), texts
        dlg.form.peak.SetValue('100')
        dlg.form.refresh_pairs()
        texts = []
        for i in range(dlg.form.pairs.GetItemCount()):
            texts.append([dlg.form.pairs.GetItemText(i, c) for c in range(4)])
        assert any(r[3] == '0.60' for r in texts), texts
        print('E6 SMOKE PASS')
    finally:
        dlg.Destroy()


if __name__ == '__main__':
    main()
