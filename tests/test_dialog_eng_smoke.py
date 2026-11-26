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

"""xvfb smoke for the engineered-nets GUI: the main dialog's
Engineered nets group in all three sidecar states, the Markings
editor on the fixture sidecar (one form per type), and the verdict
bar + Report dialog.  Standalone (needs wx + pcbnew + scrot):

    xvfb-run -a python3 tests/test_dialog_eng_smoke.py

Screenshots land in $ENG_SMOKE_OUT (default /tmp/eng-smoke).
Set $ENG_LEG1_RESULT to a run_leg.py result.json to also drive the
verdict bar + Report dialog from that real run's data and assert
the bar text equals the run's summary counts.

Capture note: wx ScreenDC blits return unpainted surfaces under a
window-manager-less xvfb, so shots are taken with scrot over the
window's screen rect after event pumping; the main dialog's group
sits in the scrolled stack (#169 layout), so the stack is scrolled
to it first -- exactly what a user does.
"""
import json, os, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wx
from ai_router import dialog as dlgmod
from ai_router import markings_dialog, markings_model as mm

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eng_gui_fixture')
OUT = os.environ.get('ENG_SMOKE_OUT', '/tmp/eng-smoke')


def _pump(n=12):
    for _ in range(n):
        wx.Yield(); time.sleep(0.08)


def shoot(win, name, scroll_to=None):
    os.makedirs(OUT, exist_ok=True)
    win.Show(); _pump()
    if scroll_to is not None:
        lab = scroll_to.GetScreenPosition()
        scr = win.scroll.GetScreenPosition()
        vs = win.scroll.GetViewStart()
        virtual_y = (lab.y - scr.y) + vs[1] * 10
        win.scroll.Scroll(0, max(0, virtual_y // 10 - 2))
        _pump(8)
    win.Raise(); _pump(4)
    rect = win.GetScreenRect()
    path = os.path.join(OUT, name)
    subprocess.run(['scrot', '-a', '%d,%d,%d,%d' % (rect.x, rect.y, rect.width, rect.height), path],
                   check=True)
    assert os.path.getsize(path) > 4000, path
    return path


def _fixture_dir(sidecar):
    d = tempfile.mkdtemp(prefix='eng-smoke-')
    for f in ('fixture.kicad_pcb', 'fixture.kicad_pro', 'fixture.net'):
        shutil.copy(os.path.join(FIX, f), d)
    if sidecar == 'valid':
        shutil.copy(os.path.join(FIX, 'fixture.ai-router-markings.json'), d)
    elif sidecar == 'invalid':
        with open(os.path.join(d, 'fixture.ai-router-markings.json'), 'w') as fh:
            fh.write('{"impedance": [{"net": "RF_IN"}]}')  # missing z0_ohm
    return os.path.join(d, 'fixture.kicad_pcb')


def smoke_dialog_states(app):
    shots = []
    for state, expect in (('valid', '8 markings'), ('none', 'none yet'), ('invalid', 'INVALID')):
        dlg = dlgmod.RouterDialog(None, _fixture_dir(state))
        try:
            assert dlg.eng_file_label.GetLabel().find(expect) >= 0, (state, dlg.eng_file_label.GetLabel())
            banner_shown = dlg.eng_banner.IsShown()
            assert banner_shown == (state == 'invalid'), state
            if state == 'invalid':
                assert 'route unmarked until repaired' in dlg.eng_banner.GetLabel()
                assert dlg.eng_repair_btn.IsShown() and dlg.eng_open_btn.IsShown()
            chip = dlg.eng_chips['impedance'].GetLabel()
            assert 'ENFORCED' in chip, chip
            assert 'CONDITIONAL' in dlg.eng_chips['power'].GetLabel()
            assert 'POST-CHECK' in dlg.eng_chips['length_groups'].GetLabel()
            assert not dlg.verdict_bar.IsShown()
            shots.append(shoot(dlg, 'dialog-%s.png' % state, scroll_to=dlg.eng_file_label))
        finally:
            dlg.Destroy()
    return shots


def smoke_editor_forms(app):
    board = _fixture_dir('valid')
    from ai_router import board_index, markings
    index = board_index.load_index(board)
    data = markings.load_markings(board).data
    dlg = markings_dialog.MarkingsEditorDialog(None, board, data, index,
                                               banned_layers=set(),
                                               conditions=mm.evaluate_conditions(board, data))
    shots = []
    try:
        assert dlg.pill_marked.GetLabel() == 'Marked nets (10)', dlg.pill_marked.GetLabel()
        assert dlg.pill_issues.GetLabel() == 'Issues (0)'
        want = {'RF_IN': 'ImpedanceForm', 'CLK_P': 'DifferentialForm',
                'HV_A': 'HVForm', 'LEN_A': 'LengthForm',
                'QUIET_X': 'QuietForm', 'VCC5': 'PowerForm'}
        seen = {}
        for i in range(dlg.list.GetItemCount()):
            net = dlg.list.GetItemText(i, 0)
            if net in want:
                evt = wx.ListEvent(wx.EVT_LIST_ITEM_SELECTED.typeId, dlg.list.GetId())
                evt.SetIndex(i)
                dlg.list.GetEventHandler().ProcessEvent(evt)
                wx.Yield()
                seen[net] = type(dlg.form).__name__
        assert seen == want, seen
        shots.append(shoot(dlg, 'editor-power.png'))
        dlg._set_view('group')
        labels = [dlg.list.GetItemText(i, 0) for i in range(dlg.list.GetItemCount())]
        assert any('Power corners' in x for x in labels), labels
        assert any('Library rules' in x for x in labels), labels
        shots.append(shoot(dlg, 'editor-group.png'))
    finally:
        dlg.Destroy()
    return shots


def smoke_verdict_and_report(app):
    dlg = dlgmod.RouterDialog(None, _fixture_dir('valid'))
    shots = []
    try:
        summary = {'counts': {'PASS': 4, 'FAIL': 1, 'WARN': 1, 'LIB GAP': 1, 'CONDITIONAL': 2},
                   'rows': [{'section': 'impedance', 'marking': 'Impedance \u2014 RF_ANT',
                             'verdict': 'FAIL', 'detail': 'routed 41 ohm vs 50', 'at': (84.2, 51.7)},
                            {'section': 'power', 'marking': 'Power \u2014 VCC5',
                             'verdict': 'CONDITIONAL', 'detail': 'recorded only', 'at': None}]}
        dlg._show_verdict(summary, {'final_board': '/tmp/result.kicad_pcb', 'ledger_path': None})
        assert dlg.verdict_bar.IsShown()
        assert dlg.verdict_text.GetLabel() == '4 PASS \u00b7 1 FAIL \u00b7 1 WARN \u00b7 1 LIB GAP \u00b7 2 CONDITIONAL'
        dlg.set_status_done_gate_fail(1, 'detail')
        assert dlg.lamp_state_ctrl.GetLabel() == 'DONE (1 GATE FAIL)'
        assert dlg._lamp_state == 'DONE'
        shots.append(shoot(dlg, 'dialog-verdict.png'))
        rep = markings_dialog.ReportDialog(dlg, summary, dlg.board_ctrl.GetValue(),
                                           '/tmp/result.kicad_pcb', None)
        try:
            assert rep.list.GetItemCount() == 2
            shots.append(shoot(rep, 'report.png'))
        finally:
            rep.Destroy()
    finally:
        dlg.Destroy()
    return shots


def smoke_real_leg_verdict(app):
    """Drive the bar + report from a REAL run's result.json (owner
    acceptance leg 3): counts on the bar must equal the run's own
    summary, and the amber gate-fail lamp must show its FAIL n."""
    result = json.load(open(os.environ['ENG_LEG1_RESULT']))
    summary = mm.summarize_report(result['engineered_nets'], result['placement_audit'])
    assert summary['counts'] == result['summary']['counts'], (summary['counts'], result['summary']['counts'])
    board = os.path.join(FIX, 'fixture.kicad_pcb')
    dlg = dlgmod.RouterDialog(None, board)
    shots = []
    try:
        final_board = result['gate']['board']
        workdir = os.path.dirname(final_board)
        res = {'final_board': final_board, 'ledger_path': os.path.join(workdir, 'ledger.json')}
        dlg._show_verdict(summary, res)
        n_fail = summary['counts']['FAIL']
        assert n_fail > 0, 'the acceptance fixture must produce gate fails'
        dlg.set_status_done_gate_fail(n_fail, 'engineered report')
        assert dlg.lamp_state_ctrl.GetLabel() == 'DONE (%d GATE FAIL)' % n_fail
        assert dlg.verdict_text.GetLabel() == mm.verdict_bar_text(summary['counts'])
        print('real bar:', dlg.verdict_text.GetLabel())
        shots.append(shoot(dlg, 'dialog-verdict-real.png'))
        rep = markings_dialog.ReportDialog(dlg, summary, board, final_board, res['ledger_path'])
        try:
            assert rep.list.GetItemCount() == len(summary['rows'])
            assert rep.ledger_b.IsEnabled()
            shots.append(shoot(rep, 'report-real.png'))
        finally:
            rep.Destroy()
    finally:
        dlg.Destroy()
    return shots


if __name__ == '__main__':
    app = wx.App(False)
    shots = []
    shots += smoke_dialog_states(app)
    shots += smoke_editor_forms(app)
    shots += smoke_verdict_and_report(app)
    if os.environ.get('ENG_LEG1_RESULT'):
        shots += smoke_real_leg_verdict(app)
    print('test_dialog_eng_smoke: OK')
    for s in shots:
        print('shot:', s)
