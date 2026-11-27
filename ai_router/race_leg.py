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

"""Phase 2E (#66): one portfolio leg, run as a child process.

tool_portfolio's racing mode spawns this script once per candidate
leg. The child rebuilds just enough Pipeline state to run the leg
against the parent's stripped base (shared, read-only), gates the
result -- with the parent's base-gate references seeded in through
the job file, so the leg's deltas key against the same references
as the parent's own gates -- and writes the outcome to
leg-result.json in its own workdir (atomically, via a tmp file and
a replace). It never touches the parent's current board or
ledger: selection and adoption are the parent's, made in fixed
candidate order, which is what keeps the raced outcome independent
of which leg finishes first.

Usage: race_leg.py <job.json>
"""
import json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as pl


def main():
    with open(sys.argv[1]) as f:
        job = json.load(f)
    wd = job['workdir']
    os.makedirs(wd, exist_ok=True)
    # The run's input copies, so this leg's _sidecars / census /
    # gate resolve netclasses and DRC rules exactly as the
    # parent's do.
    for ext in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
        src = os.path.join(job['parent_workdir'], 'input' + ext)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(wd, 'input' + ext))
    logf = open(os.path.join(wd, 'leg.log'), 'a', buffering=1)
    pipe = object.__new__(pl.Pipeline)
    pipe.cfg = job['cfg']
    pipe.board_path = os.path.join(wd, 'input.kicad_pcb')
    pipe.workdir = wd
    pipe.base = job['base']
    pipe.current = job['base']
    pipe.pass_no = 0
    pipe._oseq = 0
    pipe._wseq = 0
    pipe._gseq = 0
    pipe.ledger = []
    pipe.last_gate = None
    pipe.last_route = None
    pipe.log = lambda m: logf.write(str(m) + '\n')
    if job.get('copper_layers') is not None:
        pipe._copper_layers = job['copper_layers']
    if job.get('locks_protected'):
        pipe._locks_protected = True
    for attr, key in (('_base_starved', 'base_starved'),
                      ('_base_banned', 'base_banned'),
                      ('_base_sha', 'base_sha')):
        if job.get(key) is not None:
            setattr(pipe, attr, job[key])
    if job.get('base_starved') is None or job.get('base_banned') is None:
        # No seeded references (the parent had not gated its base
        # before racing): gate the shared base here, so this leg's
        # deltas key against the same references the parent's own
        # gates use. A failure leaves the deltas self-keyed, which
        # is the pre-reference behaviour, not a leg failure.
        try:
            pipe.tool_gate(pipe.base)
        except Exception:
            pass
    oc = pipe._run_leg(job['engine'])
    tmp = os.path.join(wd, 'leg-result.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(oc, f, default=str)
    os.replace(tmp, os.path.join(wd, 'leg-result.json'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
