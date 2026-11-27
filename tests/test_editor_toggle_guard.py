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
#222: the dialog module is loaded from its file
(SourceFileLoader), never via the ai_router package, whose
__init__ runs plugin registration -- see _load().
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
    # #222: load markings_dialog.py from its file, never via the
    # ai_router package -- the package __init__ runs plugin
    # registration whenever wx+pcbnew are both importable, which
    # hangs under Windows KiCad standalone python.exe (outside
    # pcbnew.exe).  The module's own dual-import guard resolves
    # its siblings as top-level modules with the ai_router dir
    # on sys.path (the dlg_uut pattern used for dialog.py).
    import importlib.machinery, importlib.util
    pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', 'ai_router')
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    ld = importlib.machinery.SourceFileLoader(
        'markings_dialog_uut_r9', os.path.join(pkg_dir, 'markings_dialog.py'))
    sp = importlib.util.spec_from_loader('markings_dialog_uut_r9', ld)
    md = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(md)
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


# ------------------------------------------------- #221: search restore
class _List:
    def __init__(self):
        self.items = []; self.selected = []; self.focused = []
    def DeleteAllItems(self): self.items = []
    def GetItemCount(self): return len(self.items)
    def InsertItem(self, i, label):
        self.items.append(label); return len(self.items) - 1
    def SetItem(self, i, col, text): pass
    def SetColumn(self, col, item): pass
    def Select(self, i): self.selected.append(i)
    def Focus(self, i): self.focused.append(i)


class _Pill(_Radio):
    def SetLabel(self, t): self.label = t


class _Search:
    def __init__(self): self.q = ''
    def GetValue(self): return self.q


class _Model:
    def marked_nets(self): return {'RF_IN': [('impedance', 0)]}
    def issues(self): return []
    def board_nets(self): return ['CLK', 'GND', 'RF_IN']
    def entries(self, s):
        return [{'net': 'RF_IN', 'z0_ohm': 50}] if s == 'impedance' else []
    def entry_label(self, s, e): return e.get('net', '?')
    def entry_nets(self, s, e): return [e.get('net')]
    def power_corners(self): return []
    def rule_footprints(self): return []


def _search_dialog(md):
    # Real refresh_list / _on_search on stubbed controls + model.
    d = md.MarkingsEditorDialog.__new__(md.MarkingsEditorDialog)
    d.view = 'net'; d.filter = 'all'
    d.view_net = _Radio(True); d.view_group = _Radio(False)
    d.pill_marked = _Pill(False); d.pill_all = _Pill(True); d.pill_issues = _Pill(False)
    d.search = _Search()
    d.list = _List()
    d.model = _Model()
    d._rows = []; d._list_sel = -1; d._sel_guard = False
    d.selection = {'kind': 'entry', 'section': 'impedance', 'index': 0, 'missing': None}
    d.form = None
    return d


def test_search_keeps_visible_selection():
    md = _load()
    if md is None:
        return
    d = _search_dialog(md)
    d.refresh_list()
    assert [r.get('net') for r in d._rows] == ['CLK', 'GND', 'RF_IN']
    d._list_sel = 2                     # RF_IN row, as _on_select left it
    d.list.selected = []; d.list.focused = []
    d.search.q = 'RF'
    d._on_search(None)
    assert [r.get('net') for r in d._rows] == ['RF_IN']
    assert d._list_sel == 0, d._list_sel
    assert d.list.selected == [0] and d.list.focused == [0]


def test_search_filtered_out_selection_not_resurrected():
    md = _load()
    if md is None:
        return
    d = _search_dialog(md)
    d.refresh_list()
    d._list_sel = 2
    d.list.selected = []; d.list.focused = []
    d.search.q = 'CLK'
    d._on_search(None)
    assert [r.get('net') for r in d._rows] == ['CLK']
    assert d._list_sel == -1, d._list_sel
    assert d.list.selected == [] and d.list.focused == []


def test_refresh_restores_corners_row_in_group_view():
    md = _load()
    if md is None:
        return
    d = _search_dialog(md)
    d.view = 'group'
    d.selection = {'kind': 'corners'}
    d.refresh_list()
    assert [r['kind'] for r in d._rows] == ['corners', 'rules']
    assert d._list_sel == 0, d._list_sel


def test_loader_never_executes_package_init():
    # #222 proof: in a fresh interpreter, an import blocker that
    # raises on ANY ai_router package import must not trip when
    # this file _load() runs -- the dialog module comes from its
    # file, so ai_router/__init__.py (plugin registration, the
    # Windows KiCad standalone-python hang) never executes.
    # Prints SKIP instead of OK only where wx is absent, where
    # the registration could not run anyway.
    import subprocess, textwrap
    child = textwrap.dedent("""
        import sys
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == 'ai_router' or name.startswith('ai_router.'):
                    raise AssertionError('package import attempted: %s' % name)
                return None
        sys.meta_path.insert(0, _Block())
        sys.path.insert(0, __TESTDIR__)
        import test_editor_toggle_guard as t
        md = t._load()
        print('SKIP' if md is None else 'OK')
        """).replace('__TESTDIR__',
                     repr(os.path.dirname(os.path.abspath(__file__))))
    r = subprocess.run([sys.executable, '-c', child],
                       capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr[-500:])
    assert r.stdout.strip().endswith(('OK', 'SKIP')), r.stdout


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
    print('test_editor_toggle_guard: %d tests passed' % len(fns))
