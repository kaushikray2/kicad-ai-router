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

"""#218: Report -> Locate called pcbnew.FocusOnLocation, which
does not exist in KiCad 10 (verified against the installed
10.0.8 bindings: FocusOnItem and Refresh exist, FocusOnLocation
does not).  The AttributeError was swallowed and the dialog
claimed 'Panned open board' anyway.  Locate now focuses the
nearest board item (or a transient shape) via FocusOnItem +
Refresh and claims the pan only on success.  The helper is
driven here with a fake pcbnew; the dialog handler with a fake
pcbnew injected into sys.modules.
Run: python3 tests/test_locate_pan.py"""
import os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


class _Pt:
    def __init__(self, x, y): self.x, self.y = x, y


class _Track:
    def __init__(self, x1, y1, x2, y2): self._s, self._e = _Pt(x1, y1), _Pt(x2, y2)
    def GetStart(self): return self._s
    def GetEnd(self): return self._e


class _Pad:
    def __init__(self, x, y): self._p = _Pt(x, y)
    def GetPosition(self): return self._p


class _Fp:
    def __init__(self, pads): self._pads = pads
    def Pads(self): return self._pads


class _Shape:
    def __init__(self, board): self.board = board; self.center = None
    def SetShape(self, s): self.shape = s
    def SetCenter(self, c): self.center = c
    def SetRadius(self, r): self.radius = r


class _Board:
    def __init__(self, tracks=(), fps=(), name=''):
        self._t, self._f, self._name = list(tracks), list(fps), name
    def GetTracks(self): return self._t
    def GetFootprints(self): return self._f
    def GetFileName(self): return self._name


def _fake_pcbnew(board=None, fail_focus=False):
    m = types.ModuleType('pcbnew')
    m.VECTOR2I = _Pt
    m.SHAPE_T_CIRCLE = 'circle'
    m.PCB_SHAPE = _Shape
    m.focused = []
    m.refreshed = []
    def _focus(item):
        if fail_focus:
            raise RuntimeError('no frame')
        m.focused.append(item)
    m.FocusOnItem = _focus
    m.Refresh = lambda: m.refreshed.append(True)
    m.GetBoard = lambda: board
    return m


def _load():
    try:
        import wx  # noqa: F401
    except ImportError:
        import pytest
        pytest.importorskip('wx')
        return None
    from ai_router import markings_dialog as md
    return md


def test_pan_focuses_nearest_item():
    md = _load()
    if md is None:
        return
    near_pad = _Pad(10_100_000, 20_000_000)          # 0.1 mm from target
    board = _Board(tracks=[_Track(0, 0, 5_000_000, 0)],
                   fps=[_Fp([_Pad(30_000_000, 20_000_000), near_pad])])
    fake = _fake_pcbnew(board)
    assert md._pan_board_to(fake, board, 10.0, 20.0) is True
    assert fake.focused == [near_pad], fake.focused
    assert fake.refreshed == [True]


def test_pan_falls_back_to_transient_shape():
    md = _load()
    if md is None:
        return
    board = _Board(tracks=[_Track(0, 0, 1_000_000, 0)])   # >2 mm away
    fake = _fake_pcbnew(board)
    assert md._pan_board_to(fake, board, 10.0, 20.0) is True
    assert len(fake.focused) == 1 and isinstance(fake.focused[0], _Shape)
    c = fake.focused[0].center
    assert (c.x, c.y) == (10_000_000, 20_000_000)


def test_pan_long_track_falls_back_to_shape_at_target():
    # #220: a 40 mm track whose end is 0.5 mm from the target must
    # NOT be focused -- FocusOnItem centres on the item, i.e. the
    # track midpoint ~20 mm away.  The point falls through to the
    # transient shape centred exactly on the target.
    md = _load()
    if md is None:
        return
    board = _Board(tracks=[_Track(10_000_000, 20_000_000, 50_000_000, 20_000_000)])
    fake = _fake_pcbnew(board)
    assert md._pan_board_to(fake, board, 10.5, 20.0) is True
    assert len(fake.focused) == 1 and isinstance(fake.focused[0], _Shape), fake.focused
    c = fake.focused[0].center
    assert (c.x, c.y) == (10_500_000, 20_000_000), (c.x, c.y)


def test_pan_via_and_short_segment_still_focused():
    # #220 guard legs: vias (start == end) and short segments stay
    # eligible -- the fix must not over-exclude point-like items.
    md = _load()
    if md is None:
        return
    via = _Track(10_400_000, 20_000_000, 10_400_000, 20_000_000)
    board = _Board(tracks=[via])
    fake = _fake_pcbnew(board)
    assert md._pan_board_to(fake, board, 10.5, 20.0) is True
    assert fake.focused == [via], fake.focused
    seg = _Track(9_000_000, 20_000_000, 12_000_000, 20_000_000)   # 3 mm
    board = _Board(tracks=[seg])
    fake = _fake_pcbnew(board)
    assert md._pan_board_to(fake, board, 10.5, 20.0) is True
    assert fake.focused == [seg], fake.focused


class _Note:
    def __init__(self): self.text = ''
    def SetLabel(self, t): self.text = t
    def Wrap(self, _w): pass


def _report_dialog(md, board_path, result_board):
    d = md.ReportDialog.__new__(md.ReportDialog)
    d.note = _Note()
    d.board_path = board_path
    d.result_board = result_board
    d._selected = lambda: {'at': (10.0, 20.0)}
    return d


def test_locate_claims_pan_only_on_success():
    md = _load()
    if md is None:
        return
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = os.path.join(tmp, 'board-result.kicad_pcb')
        board = _Board(tracks=[_Track(10_000_000, 20_000_000, 11_000_000, 20_000_000)], name=result)
        old = sys.modules.get('pcbnew')
        try:
            sys.modules['pcbnew'] = _fake_pcbnew(board)
            d = _report_dialog(md, os.path.join(tmp, 'board.kicad_pcb'), result)
            d._on_locate(None)
            assert d.note.text.startswith('Panned open board to (10.0, 20.0)'), d.note.text
            sys.modules['pcbnew'] = _fake_pcbnew(board, fail_focus=True)
            d2 = _report_dialog(md, os.path.join(tmp, 'board.kicad_pcb'), result)
            d2._on_locate(None)
            assert d2.note.text.startswith('Could not pan the open board'), d2.note.text
            sys.modules['pcbnew'] = _fake_pcbnew(None)
            d3 = _report_dialog(md, os.path.join(tmp, 'board.kicad_pcb'), result)
            d3._on_locate(None)
            assert d3.note.text.startswith('No matching board open'), d3.note.text
        finally:
            if old is None:
                sys.modules.pop('pcbnew', None)
            else:
                sys.modules['pcbnew'] = old


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
    print('test_locate_pan: %d tests passed' % len(fns))
