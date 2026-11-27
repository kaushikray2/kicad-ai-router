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

"""#215: the markings editor's view/filter toggles rebuilt the
list (resetting _list_sel) without the pending-edits guard every
other exit path from the active form carries -- a later Cancel
in _confirm_pending could no longer restore the list selection
to the form's row, leaving the highlight and the shown form
desynced.  The toggles now confirm first and restore the radio
buttons when the user cancels.  Drives the real handler methods
on a stubbed dialog (the notice-refresh pattern); needs wx
importable, runs under the pytest env and xvfb standalone.
Run: python3 tests/test_editor_toggle_guard.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


class _Radio:
    def __init__(self, v=False): self.val = v
    def SetValue(self, v): self.val = bool(v)
    def GetValue(self): return self.val


def _dialog(md, confirm):
    d = md.MarkingsEditorDialog.__new__(md.MarkingsEditorDialog)
    d.view = 'net'
    d.filter = 'marked'
    d.view_net = _Radio(True); d.view_group = _Radio(False)
    d.pill_marked = _Radio(True); d.pill_all = _Radio(False); d.pill_issues = _Radio(False)
    d.refreshed = 0
    d.refresh_list = lambda: setattr(d, 'refreshed', d.refreshed + 1)
    d._confirm_pending = lambda: confirm
    return d


def _load():
    try:
        import wx  # noqa: F401
    except ImportError:
        import pytest
        pytest.importorskip('wx')
        return None
    from ai_router import markings_dialog as md
    return md


def test_view_toggle_cancel_keeps_view_and_radios():
    md = _load()
    if md is None:
        return
    d = _dialog(md, confirm=False)      # user cancels the prompt
    d.view_group.SetValue(True)         # the click already landed
    d._set_view('group')
    assert d.view == 'net', d.view
    assert d.view_net.GetValue() is True and d.view_group.GetValue() is False
    assert d.refreshed == 0


def test_view_toggle_confirmed_switches():
    md = _load()
    if md is None:
        return
    d = _dialog(md, confirm=True)
    d._set_view('group')
    assert d.view == 'group' and d.refreshed == 1


def test_filter_toggle_cancel_keeps_filter_and_pills():
    md = _load()
    if md is None:
        return
    d = _dialog(md, confirm=False)
    d.pill_all.SetValue(True)
    d._set_filter('all')
    assert d.filter == 'marked', d.filter
    assert d.pill_marked.GetValue() is True and d.pill_all.GetValue() is False
    assert d.refreshed == 0


def test_filter_toggle_confirmed_switches():
    md = _load()
    if md is None:
        return
    d = _dialog(md, confirm=True)
    d._set_filter('issues')
    assert d.filter == 'issues' and d.refreshed == 1


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
    print('test_editor_toggle_guard: %d tests passed' % len(fns))
