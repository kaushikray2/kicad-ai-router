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

"""xvfb smoke for #205: un-applied form edits in the markings
editor are tracked, block a selection switch when the user
cancels the Apply/Discard/Cancel prompt, and are applied by Save
before the model is written.  Standalone:

    xvfb-run -a python3 tests/test_dialog_pending_smoke.py
"""
import json, os, shutil, sys, tempfile
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
    d = tempfile.mkdtemp(prefix='pending-smoke-')
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
        _select(dlg, 'RF_IN')
        assert type(dlg.form).__name__ == 'ImpedanceForm', type(dlg.form)
        assert not dlg._form_pending()
        dlg.form.z0.SetValue('75')
        assert dlg._form_pending(), 'edit not tracked as pending'
        # A selection switch the user cancels keeps the form (and
        # its edits) exactly as they were.
        orig = dlg._confirm_pending
        dlg._confirm_pending = lambda: False
        try:
            _select(dlg, 'VCC5')
        finally:
            dlg._confirm_pending = orig
        assert type(dlg.form).__name__ == 'ImpedanceForm'
        assert dlg._form_pending()
        # Save applies the pending form first, then writes.
        assert dlg._on_save() is True
        saved = json.load(open(os.path.join(d, 'fixture.ai-router-markings.json')))
        ent = [e for e in saved['impedance'] if e['net'] == 'RF_IN'][0]
        assert ent['z0_ohm'] == 75.0, ent
        assert not dlg._form_pending()
        print('PENDING SMOKE PASS')
    finally:
        dlg.Destroy()


if __name__ == '__main__':
    main()
