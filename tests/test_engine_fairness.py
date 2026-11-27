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

"""Issues #45 + #46: the portfolio's external engines were not
running the same race as TraceMaker, and nothing checked that a
candidate was still the same design.

#46 (confounded A/B): Freerouting ran -mt 2 (its maintainers
flag multi-threaded optimisation for clearance violations,
freerouting#445) and was never told the layer law -- KiCad's
DSN export conveys the board's stackup types, not the run's
bans, so banned-layer tracks counted as gate faults and the leg
lost by construction. KRT stepped the board's rules toward its
fab floor while the gate DRC'd the board's own rules.
#45: KRT's chain can move parts (its decap step) and pour its
own planes, and no design-invariance check stood between a
candidate like that and delivery.

Pinned here: freerouting runs -mt 1 and its DSN copy carries
banned layers as (type power), verified before the run; KRT
runs with --keep-placement + --board-rules and the law stamped
as rule-area keepouts on its input copy (removed from its
output); and the worker's design fingerprint detects a moved
footprint, an added zone and a rewired pad, while the
pipeline's _run_leg rejects such a candidate before gating.
Run: python3 tests/test_engine_fairness.py  (pcbnew parts skip
outside KiCad)"""
import importlib.machinery, importlib.util, json, os, shutil, stat, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import dsn_law
import pipeline as pl

try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')  # report a skip, not a collection error, outside KiCad
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

SELFTEST = os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest')

NOMAD_SHA = 'c2926c5221a84b9f707b2d3d73baf50a56713224a2485ea28ee3c722c99343d3'


def _nomad():
    # The Nomad fixture is not tracked in git (it lives in the dev
    # tree's selftest dir); resolve it sha-verified, exactly as
    # test_zone_census_rule_areas.py does, and skip without it.
    import glob, hashlib
    cands = [os.path.join(SELFTEST, 'nomad-revi-unrouted.kicad_pcb')]
    cands += sorted(glob.glob(os.path.expanduser(
        '~/Documents/workspace/agent-runs/*/docker/selftest/nomad-revi-unrouted.kicad_pcb')))
    for c in cands:
        if os.path.exists(c) and hashlib.sha256(open(c, 'rb').read()).hexdigest() == NOMAD_SHA:
            return c
    import pytest
    pytest.skip('Nomad fixture not present on this machine')
    return None


def test_copper_stack_index():
    b = pcbnew.LoadBoard(_nomad())
    assert w._copper_stack_index(b, 'F.Cu') == 0
    assert w._copper_stack_index(b, 'In1.Cu') == 1
    assert w._copper_stack_index(b, 'In2.Cu') == 2
    assert w._copper_stack_index(b, 'B.Cu') == 3
    assert w._copper_stack_index(b, 'F.SilkS') is None


# ------------------------------------------------------- freerouting leg
def _fake_freerouting(d):
    cap = os.path.join(d, 'capture')
    os.makedirs(cap)
    script = os.path.join(d, 'freerouting')
    body = ('#!/bin/sh\n'
            'echo "$@" > %s/argv.txt\n'
            'prev=\n'
            'for a in "$@"; do if [ "$prev" = "-de" ]; then cp "$a" %s/board.dsn; fi; prev="$a"; done\n'
            'exit 0\n') % (cap, cap)
    open(script, 'w').write(body); os.chmod(script, stat.S_IRWXU)
    return script, cap


def test_freeroute_mt1_and_dsn_law():
    d = tempfile.mkdtemp()
    script, cap = _fake_freerouting(d)
    os.environ['FREEROUTING_BIN'] = script
    try:
        src = os.path.join(d, 'mapone.kicad_pcb')
        shutil.copy(os.path.join(SELFTEST, 'mapone.kicad_pcb'), src)
        # the fake writes no SES: the leg reports no_ses, but only
        # after the export + law rewrite ran -- both are captured
        res = w.cmd_freeroute(src, os.path.join(d, 'out.kicad_pcb'), banned=['F.Cu'])
        assert res['status'] == 'no_ses', res
        argv = open(os.path.join(cap, 'argv.txt')).read().split()
        i = argv.index('-mt')
        assert argv[i + 1] == '1', argv
        blocks = dsn_law.layer_blocks(open(os.path.join(cap, 'board.dsn')).read())
        assert blocks[0] == ('F.Cu', 'power'), blocks   # the ban rode the DSN
        assert blocks[1][1] == 'signal', blocks          # B.Cu still routable
        assert res.get('dsn_law', {}).get('F.Cu', {}).get('after') == 'power', res
    finally:
        del os.environ['FREEROUTING_BIN']


# --------------------------------------------------------------- KRT leg
def _fake_krt(d):
    root = os.path.join(d, 'krt')
    os.makedirs(os.path.join(root, 'py_router'))
    cap = os.path.join(d, 'krt-argv.json')
    body = ('import json, os, shutil, sys\n'
            'json.dump(sys.argv, open(%r, "w"))\n'
            'shutil.copy(sys.argv[1], sys.argv[2])\n') % cap
    open(os.path.join(root, 'py_router', 'autoroute.py'), 'w').write(body)
    return root, cap


def test_krt_flags_and_law_zones():
    d = tempfile.mkdtemp()
    root, cap = _fake_krt(d)
    os.environ['AI_ROUTER_KRT'] = root
    try:
        src = os.path.join(d, 'nomad.kicad_pcb')
        shutil.copy(_nomad(), src)
        out = os.path.join(d, 'out.kicad_pcb')
        res = w.cmd_krt_route(src, out, banned=['In1.Cu', 'In2.Cu'])
        assert res['status'] == 'routed', res
        argv = json.load(open(cap))
        assert '--keep-placement' in argv and '--board-rules' in argv, argv
        assert res['law_zones'] == ['In1.Cu', 'In2.Cu'], res
        b = pcbnew.LoadBoard(out)
        assert not [z for z in b.Zones() if z.GetZoneName() == w.LAW_ZONE_NAME]
        # and the design that comes back is the design that went in
        assert w.cmd_design_fingerprint(out) == w.cmd_design_fingerprint(src)
    finally:
        del os.environ['AI_ROUTER_KRT']


# ------------------------------------------------------------ fingerprint
def test_fingerprint_detects_design_changes():
    d = tempfile.mkdtemp()
    src = os.path.join(d, 'mapone.kicad_pcb')
    shutil.copy(os.path.join(SELFTEST, 'mapone.kicad_pcb'), src)
    base = w.cmd_design_fingerprint(src)
    # a save/load round trip is not a design change
    b = pcbnew.LoadBoard(src); pcbnew.SaveBoard(src, b)
    assert w.cmd_design_fingerprint(src) == base
    # a moved footprint is
    fp = list(b.GetFootprints())[0]
    pos = fp.GetPosition()
    fp.SetPosition(pcbnew.VECTOR2I(pos.x + 1000000, pos.y))
    moved = os.path.join(d, 'moved.kicad_pcb'); pcbnew.SaveBoard(moved, b)
    fm = w.cmd_design_fingerprint(moved)
    assert fm['footprints'] != base['footprints'] and fm['zones'] == base['zones']
    # an added zone is
    b = pcbnew.LoadBoard(src)
    z = pcbnew.ZONE(b); z.SetLayer(b.GetLayerID('F.Cu'))
    o = z.Outline(); o.NewOutline()
    for (x, y) in ((0, 0), (1000000, 0), (1000000, 1000000), (0, 1000000)):
        o.Append(x, y)
    b.Add(z)
    zoned = os.path.join(d, 'zoned.kicad_pcb'); pcbnew.SaveBoard(zoned, b)
    assert w.cmd_design_fingerprint(zoned)['zones'] != base['zones']
    # a rewired pad is
    b = pcbnew.LoadBoard(src)
    fp = list(b.GetFootprints())[0]
    pad = list(fp.Pads())[0]
    other = [n for n in ('GND', '+5V', '3V3') if n != pad.GetNetname()]
    net = b.FindNet(other[0]) if hasattr(b, 'FindNet') else None
    assert net is not None
    pad.SetNet(net)
    wired = os.path.join(d, 'wired.kicad_pcb'); pcbnew.SaveBoard(wired, b)
    assert w.cmd_design_fingerprint(wired)['pads'] != base['pads']


def test_law_zone_roundtrip_is_invisible():
    d = tempfile.mkdtemp()
    src = os.path.join(d, 'nomad.kicad_pcb')
    shutil.copy(_nomad(), src)
    base = w.cmd_design_fingerprint(src)
    stamped = w._stamp_law_zones(src, ['In1.Cu', 'In2.Cu'])
    assert stamped == ['In1.Cu', 'In2.Cu']
    assert len(w.cmd_design_fingerprint(src)['zones']) == len(base['zones']) + 2
    b = pcbnew.LoadBoard(src); w._remove_law_zones(b); pcbnew.SaveBoard(src, b)
    assert w.cmd_design_fingerprint(src) == base


# ------------------------------------------- pipeline: leg-level behaviour
ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
_GATE = {'unconnected_total': 0, 'copper_violations': 0,
         'banned_layer_tracks': 0, 'split_nets': [], 'classes': {}}
_FP_OK = {'footprints': [['U1', 0, 0, 0.0, False]], 'pads': [], 'zones': [], 'edges': []}
_FP_MOVED = {'footprints': [['U1', 500, 0, 0.0, False]], 'pads': [], 'zones': [], 'edges': []}


def _pipe(fp_by_board):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.board_path = os.path.join(d, 'input.kicad_pcb')
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    open(pipe.base, 'w').write('board')
    pipe.cfg = {'work': 40000000}
    pipe.last_gate = None; pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._copper_layers = list(LAYERS)
    pipe.tool_gate = lambda board: dict(_GATE)
    pipe.worker_calls = []
    def fake_worker(cmd, *args, **kw):
        pipe.worker_calls.append((cmd, args))
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        if cmd == 'design_fingerprint':
            board = args[args.index('--board') + 1]
            return dict(fp_by_board(board))
        if '--out' in args:
            open(args[args.index('--out') + 1], 'w').write('board')
        return {'status': 'routed', 'seconds': 0.1}
    pipe._worker = fake_worker
    return pipe


def test_leg_gets_the_layer_law():
    pipe = _pipe(lambda board: _FP_OK)
    oc = pipe._run_leg('freerouting')
    assert oc['status'] == 'finished', oc
    calls = [a for (c, a) in pipe.worker_calls if c == 'freeroute']
    assert len(calls) == 1
    args = calls[0]
    assert args[args.index('--no-tracks-on') + 1] == 'In1.Cu', args


def test_leg_rejects_a_moved_part_before_gating():
    pipe = _pipe(lambda board: _FP_OK if board.endswith('base.kicad_pcb') else _FP_MOVED)
    gated = []
    pipe.tool_gate = lambda board: gated.append(board) or dict(_GATE)
    oc = pipe._run_leg('krt')
    assert oc['status'] == 'failed', oc
    assert oc['error'].startswith('design-invariance:'), oc
    assert 'footprints' in oc['error'], oc
    assert gated == [], 'a design-changed candidate must never reach the gate'


if __name__ == '__main__':
    test_copper_stack_index()
    test_freeroute_mt1_and_dsn_law()
    test_krt_flags_and_law_zones()
    test_fingerprint_detects_design_changes()
    test_law_zone_roundtrip_is_invisible()
    test_leg_gets_the_layer_law()
    test_leg_rejects_a_moved_part_before_gating()
    print('engine fairness OK')
