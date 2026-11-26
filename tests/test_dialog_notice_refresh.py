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

"""Issue #183: the stale-settings notice was refreshed only at
populate time, from the SAVED law, while its mode term read the
live radio -- a manual fix never cleared it. The notice now
compares the current UI state (what Run would send) against the
recommendation and refreshes on every settings change.
Run: python3 tests/test_dialog_notice_refresh.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))


class _Check:
    def __init__(self, v=False): self._v = v; self.enabled = True
    def GetValue(self): return self._v
    def SetValue(self, v): self._v = bool(v)
    def Enable(self, on=True): self.enabled = bool(on)


class _Choice:
    def __init__(self): self._items = ['off']; self._sel = 0
    def GetSelection(self): return self._sel
    def SetSelection(self, i): self._sel = i
    def GetCount(self): return len(self._items)
    def GetString(self, i): return self._items[i]
    def SetItems(self, items): self._items = list(items)
    def Enable(self, on=True): pass


class _Label:
    def __init__(self): self.text = ''; self.shown = False
    def SetLabel(self, t): self.text = t
    def Show(self): self.shown = True
    def Hide(self): self.shown = False


def _part():
    try:
        import wx  # noqa: F401
    except ImportError:
        import pytest
        pytest.importorskip('wx')
        return
    import importlib.machinery, importlib.util
    ld = importlib.machinery.SourceFileLoader('dlg_uut11', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'dialog.py'))
    sp = importlib.util.spec_from_loader('dlg_uut11', ld); dlg = importlib.util.module_from_spec(sp); sp.loader.exec_module(dlg)
    d = dlg.RouterDialog.__new__(dlg.RouterDialog)
    d._running = False; d._lamp_state = 'IDLE'
    d.set_status = lambda *a, **k: None
    d._relayout = lambda: None
    d.facts = {'copper_count': 4, 'layers': [{'canonical': c, 'name': c} for c in ('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu')]}
    d.classif = {'In1.Cu': {'kind': 'plane'}, 'In2.Cu': {'kind': 'signal'}}
    d.cfg = {'no_tracks_on': 'In1.Cu,In2.Cu', 'layer_cost': ''}
    d.aliases = {}
    d.extra_bans = []; d.extra_costs = {}
    d.mode_auto_rb = _Check(True); d.mode_llm_rb = _Check(False)
    d.law_auto_rb = _Check(False); d.law_custom_rb = _Check(True)
    d.law_caption = _Label()
    d.layer_rows = {c: {'ban': _Check(c in ('In1.Cu', 'In2.Cu')), 'cost': _Choice(), 'custom': None}
                    for c in ('In1.Cu', 'In2.Cu')}
    d.notice_label = _Label(); d.use_rec_btn = _Label()

    # saved law bans In2 as well as the plane: the notice shows
    d._refresh_notice()
    assert d.notice_label.shown, 'differing law must show the notice'
    # the user clicks Automatic law (the real handler applies the
    # recommendation to the rows and fires _on_settings_changed):
    # the notice must clear WITHOUT a reopen
    d.law_auto_rb.SetValue(True); d.law_custom_rb.SetValue(False)
    d._on_law()
    assert not d.notice_label.shown, 'manual fix must clear the notice'
    # mode term: LLM selected -> notice returns; back to Auto -> clears
    d.mode_llm_rb.SetValue(True); d.mode_auto_rb.SetValue(False)
    d._on_settings_changed()
    assert d.notice_label.shown and 'LLM mode' in d.notice_label.text, d.notice_label.text
    d.mode_llm_rb.SetValue(False); d.mode_auto_rb.SetValue(True)
    d._on_settings_changed()
    assert not d.notice_label.shown
    # basis consistency: the SAVED cfg still differs, but the UI is
    # at the recommendation -- the notice follows the UI, not cfg
    d._refresh_notice()
    assert not d.notice_label.shown


def test_notice_refresh():
    _part()


if __name__ == '__main__':
    _part()
    print('dialog notice refresh OK')
