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

"""Issue #184: a deterministic tm_relax portfolio leg was refused by
the engine on 100% of Reproducible runs. --relax budgets by rounds,
not --work (engine cli.md at phase-2-end: '--work does not limit
it'), and route_job.cpp's work_bounded for relax is
(round_work > 0 || rounds > 0) -- so --deterministic with only
--work throws '--deterministic needs a work budget'. route_flags
now translates the configured work budget into the engine's own
relax budgeting under deterministic (rounds of the engine's 20M
round quantum, mirroring its rescue derivation); every other
command line is byte-identical to before.
Run: python3 tests/test_relax_deterministic.py"""
import os, stat, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import engine_run as er
import pipeline as pl

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']

def test_route_flags_relax_deterministic():
    flags = er.route_flags({'deterministic': True}, 40000000, relax=True)
    assert flags == ['--work', '40000000', '--round-work', '20000000',
                     '--rounds', '50', '--deterministic'], flags
    flags = er.route_flags({'deterministic': True}, 20000000, relax=True)
    assert flags == ['--work', '20000000', '--round-work', '20000000',
                     '--rounds', '50', '--deterministic'], flags
    # the budget is floored at 50 rounds (the #52 sizing fix,
    # Phase 2E item 3: the configured work number never bounded
    # relax, and a small rounds budget binds and degrades the
    # candidate -- probes in agent-runs/phase2e-ab/PROBE-ROUNDS.md)
    flags = er.route_flags({'deterministic': True}, 50000000, relax=True)
    assert flags == ['--work', '50000000', '--round-work', '20000000',
                     '--rounds', '50', '--deterministic'], flags


def test_route_flags_other_shapes_unchanged():
    # non-relax deterministic: the pre-#184 line, exactly
    assert er.route_flags({'deterministic': True}, 40000000) == ['--work', '40000000', '--deterministic']
    # AMENDED by #52 (Phase 2E item 3): this test used to pin the
    # non-deterministic relax line at --work + --time -- the exact
    # shape #52 reports as the defect (the engine budgets relax by
    # rounds; --work does not limit it, --time does, so the
    # default candidate's endpoint was the clock). Relax is now
    # round-bounded in every mode, with --time kept as the
    # backstop in default mode; test_relax_round_budget.py pins
    # the new shapes. An unusable budget keeps the old line.
    assert er.route_flags({'deterministic': False}, 40000000, 1800, relax=True) == ['--work', '40000000', '--round-work', '20000000', '--rounds', '50', '--time', '1800']
    assert er.route_flags({}, 40000000, 1800, relax=True) == ['--work', '40000000', '--round-work', '20000000', '--rounds', '50', '--time', '1800']
    assert er.route_flags({}, 0, 1800, relax=True) == ['--work', '0', '--time', '1800']
    # route_open_only still composes, ahead of the round budget
    flags = er.route_flags({'deterministic': True, 'route_open_only': True}, 40000000, relax=True)
    assert flags == ['--work', '40000000', '--route-open-only', '--round-work', '20000000',
                     '--rounds', '50', '--deterministic'], flags


def _pipe(cfg_extra):
    d = tempfile.mkdtemp()
    rec = os.path.join(d, 'argv.json')
    tm = os.path.join(d, 'faketm')
    body = ('#!/usr/bin/env python3\n'
            'import json, os, sys\n'
            'a = sys.argv[1:]\n'
            'json.dump(a, open(%r, "w"))\n'
            'out = a[a.index("-o") + 1]\n'
            'open(out, "w").write("board")\n'
            'if "--json" in a:\n'
            '    json.dump({"exit_code": 0, "seconds": 0.5}, open(a[a.index("--json") + 1], "w"))\n'
            'sys.exit(0)\n') % rec
    open(tm, 'w').write(body); os.chmod(tm, stat.S_IRWXU)
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.board_path = os.path.join(d, 'input.kicad_pcb')
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    open(pipe.base, 'w').write('board')
    pipe.cfg = {'tracemaker_path': tm, 'work': 40000000}
    pipe.cfg.update(cfg_extra)
    pipe.last_gate = None; pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._copper_layers = list(LAYERS)
    def fake_worker(cmd, *args, **kw):
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        if '--out' in args:
            open(args[args.index('--out') + 1], 'w').write('board')
        return {'status': 'ok', 'seconds': 0.1}
    pipe._worker = fake_worker
    return pipe, rec


def test_variant_command_line_relax_deterministic():
    import json
    pipe, rec = _pipe({'deterministic': True})
    outp = os.path.join(pipe.workdir, 'pf-tm-relax.kicad_pcb')
    pipe._route_variant('tm_relax', outp)
    argv = json.load(open(rec))
    assert '--relax' in argv, argv
    tail = argv[argv.index('--critical-auto') + 1:]
    # banned In1.Cu rides between the fixed flags and the budget tail
    assert '--round-work' in tail and '--rounds' in tail, argv
    assert tail[tail.index('--round-work') + 1] == '20000000', argv
    assert tail[tail.index('--rounds') + 1] == '50', argv
    assert '--deterministic' in tail and '--time' not in tail, argv


def test_variant_command_line_relax_plain_unchanged():
    # AMENDED by #52: was 'plain relax line unchanged' (no rounds,
    # --time present) -- that line WAS the #52 defect. A plain
    # (default-mode) relax variant now carries the same floored
    # round budget as a deterministic one, with --time kept as
    # the backstop limit (item-3 probes: with --time absent the
    # engine caps the tournament itself at ~120 s).
    import json
    pipe, rec = _pipe({})
    outp = os.path.join(pipe.workdir, 'pf-tm-relax.kicad_pcb')
    pipe._route_variant('tm_relax', outp)
    argv = json.load(open(rec))
    assert '--round-work' in argv and '--rounds' in argv, argv
    assert argv[argv.index('--rounds') + 1] == '50', argv
    assert argv[argv.index('--time') + 1] == '1800', argv


if __name__ == '__main__':
    test_route_flags_relax_deterministic()
    test_route_flags_other_shapes_unchanged()
    test_variant_command_line_relax_deterministic()
    test_variant_command_line_relax_plain_unchanged()
    print('relax deterministic OK')
