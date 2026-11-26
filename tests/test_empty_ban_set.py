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

"""v1.11.2/#104: an explicitly empty ban set means 'ban nothing' at
every layer of the stack. Before the fix, the pipeline legs passed
the raw config string (not the board-resolved effective set), and
the worker parsed it under a truthiness test -- '' was falsy, so
BANNED_LAYER_IDS stayed None and Grid fell back to its default
{In1_Cu} ban. Reachable whenever layer_cost prices every configured
ban: the engine is told the layer is merely priced while a leg's
Grid silently re-bans it.
Run: python3 tests/test_empty_ban_set.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
GATE = {'violations_total': 0, 'unconnected_total': 1, 'classes': {},
        'split_nets': [], 'banned_layer_tracks': 0}

# ------------------------------------------------------- pipeline side
def _bare_pipe(cfg):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = cfg; pipe.log = lambda *a: None
    pipe._copper_layers = list(LAYERS)
    pipe.last_gate = dict(GATE)
    seen = []
    def recording_worker(cmd, *a, **k):
        seen.append((cmd, list(a)))
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        return {}
    pipe._worker = recording_worker
    return pipe, seen

def test_leg_ban_args_are_the_effective_set():
    pipe, _ = _bare_pipe({'no_tracks_on': 'In1.Cu', 'layer_cost': 'In1.Cu=10,In2.Cu=10'})
    assert pipe._leg_ban_args() == ['--no-tracks-on', '']  # everything priced -> ban nothing, explicitly
    pipe, _ = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    assert pipe._leg_ban_args() == ['--no-tracks-on', 'In1.Cu']
    pipe, _ = _bare_pipe({'no_tracks_on': ''})
    assert pipe._leg_ban_args() == ['--no-tracks-on', '']
    pipe, _ = _bare_pipe({'no_tracks_on': 'In1.Cu,In2.Cu', 'layer_cost': 'In2.Cu=10'})
    assert pipe._leg_ban_args() == ['--no-tracks-on', 'In1.Cu']

def test_stitch_argv_carries_explicit_empty_bans():
    pipe, seen = _bare_pipe({'no_tracks_on': 'In1.Cu', 'layer_cost': 'In1.Cu=10,In2.Cu=10'})
    pipe.tool_stitch()
    args = [a for c, a in seen if c == 'stitch'][0]
    assert args[args.index('--no-tracks-on') + 1] == '', args
    assert args[args.index('--layer-cost') + 1] == 'In1.Cu=10,In2.Cu=10', args

# --------------------------------------------------------- worker side
def test_worker_parse_and_grid_semantics():
    try:
        import pcbnew
        import worker as w
    except Exception:
        print('SKIP worker half: no pcbnew'); return
    assert w._parse_no_tracks_raw('') == (set(), [])
    assert w._parse_no_tracks_raw(',,') == (set(), [])
    assert w._parse_no_tracks_raw('In1.Cu,') == ({pcbnew.In1_Cu}, [])  # trailing comma: crashed Grid before #104
    assert w._parse_no_tracks_raw(' In1.Cu , In2.Cu ') == ({pcbnew.In1_Cu, pcbnew.In2_Cu}, [])
    assert w._parse_no_tracks_raw('GND') == (set(), ['GND'])  # board-alias names stay unresolved for Grid to resolve
    b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
    saved = (w.BANNED_LAYER_IDS, list(w.NO_TRACKS_ON_UNRESOLVED))
    try:
        w.BANNED_LAYER_IDS = set(); w.NO_TRACKS_ON_UNRESOLVED = []
        g = w.Grid(b, (0.0, 10.0, 0.0, 10.0))
        assert 1 in g.routable and g.banned_idx == set(), (g.routable, g.banned_idx)  # explicit empty: In1 routable
        w.BANNED_LAYER_IDS = None
        g2 = w.Grid(b, (0.0, 10.0, 0.0, 10.0))
        assert 1 not in g2.routable and g2.banned_idx == {1}  # unset: the standing default ban is intact
    finally:
        w.BANNED_LAYER_IDS, w.NO_TRACKS_ON_UNRESOLVED = saved

if __name__ == '__main__':
    test_leg_ban_args_are_the_effective_set()
    test_stitch_argv_carries_explicit_empty_bans()
    test_worker_parse_and_grid_semantics()
    print('empty ban set OK')
