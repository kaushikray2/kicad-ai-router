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

"""Bench harness CLI (#62). See bench_harness/README.md.

    python3 -m bench_harness inventory
    python3 -m bench_harness prep    --fixtures-dir DIR
    python3 -m bench_harness run     --stack current --slugs a,b
    python3 -m bench_harness ramp    --baseline RUN_DIR --split train
    python3 -m bench_harness compare RUN_A RUN_B [--gate]

Held-out boards are refused unless --exam is given (exit 4). Long
runs are meant to be launched detached (setsid nohup) and inspected
through the run directory while they work.
"""
import argparse
import json
import os
import sys
import time

from . import compare as compare_mod
from . import inventory as inv
from . import prep as prep_mod
from . import runner

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BOARDS_ROOT = os.environ.get(
    'DSV3_BOARDS', os.path.expanduser(
        '~/Documents/workspace/agent-runs/dataset-v3-boards'))


def default_home():
    return os.environ.get('BENCH_HARNESS_HOME', os.path.expanduser(
        '~/Documents/workspace/agent-runs/bench-harness'))


def _add_selection(ap):
    ap.add_argument('--dataset-dir',
                    default=os.path.join(REPO, 'dataset-v3'))
    ap.add_argument('--boards-root', default=DEFAULT_BOARDS_ROOT)
    ap.add_argument('--fixtures-dir', action='append', default=[],
                    help='directory of fixture .kicad_pcb (repeatable)')
    ap.add_argument('--slugs', default=None,
                    help='comma-separated board slugs (default: the '
                         'whole selected split)')
    ap.add_argument('--split', default=None,
                    choices=['train', 'held_out', 'all'],
                    help='dataset split (default: train; fixtures when '
                         '--fixtures-dir is given and no split is named)')
    ap.add_argument('--exam', action='store_true',
                    help='EXAM MODE: allow held-out boards. Only for '
                         'the final exam; the manifest records it.')


def _select(args):
    boards = []
    if args.fixtures_dir:
        boards += inv.load_fixtures(args.fixtures_dir)
    want_dataset = bool(args.slugs) or args.split or not args.fixtures_dir
    if want_dataset:
        boards += inv.load_dataset_inventory(args.dataset_dir,
                                             args.boards_root)
    slugs = args.slugs.split(',') if args.slugs else None
    if slugs is None:
        # Bulk selection is split-scoped. Explicit slugs are NOT
        # pre-filtered: the held-out guard in select_boards must see
        # a named held-out board so it can refuse it by name (and so
        # --exam can select it deliberately).
        split = args.split
        if split is None:
            split = 'all' if args.fixtures_dir else 'train'
        if split != 'all':
            boards = [b for b in boards
                      if b.split == split or b.split == inv.SPLIT_FIXTURE]
    return inv.select_boards(boards, slugs, exam=args.exam)


def _log(m):
    print('[bench %s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


def cmd_inventory(args):
    boards = []
    if args.fixtures_dir:
        boards += inv.load_fixtures(args.fixtures_dir)
    boards += inv.load_dataset_inventory(args.dataset_dir,
                                         args.boards_root)
    counts = {}
    for b in boards:
        counts[b.split] = counts.get(b.split, 0) + 1
    print('inventory: %s' % counts)
    for b in boards:
        print('%-52s %-9s %-9s %s' % (
            b.slug, b.split, b.stratum,
            b.pcb if b.pcb and os.path.exists(b.pcb) else 'MISSING'))
    return 0


def cmd_prep(args):
    boards = _select(args)
    cache_dir = os.path.join(args.home, 'cache')
    for b in boards:
        meta = prep_mod.prepare(b, cache_dir, runner.strip_board)
        print('%-52s key=%s prepared=%s cache=%s' % (
            b.slug, meta['source_key'][:12], meta['prepared_sha256'][:12],
            'hit' if meta['cache_hit'] else 'miss'))
    return 0


def cmd_run(args):
    boards = _select(args)
    overrides = json.loads(args.cfg_json) if args.cfg_json else {}
    run_id = args.run_id or '%s-%s' % (
        time.strftime('%Y%m%d-%H%M%S'), args.stack)
    run_dir, manifest, records = runner.run_set(
        boards, args.stack, overrides, args.home, run_id,
        exam=args.exam, log=_log)
    bad = [s for s, r in records.items() if r.get('status') != 'done']
    print('run complete: %s (boards=%d, errors=%d)'
          % (run_dir, len(records), len(bad)))
    print('summary: %s' % os.path.join(run_dir, 'summary.md'))
    return 2 if bad else 0


def cmd_ramp(args):
    boards = _select(args)
    overrides = json.loads(args.cfg_json) if args.cfg_json else {}
    base = compare_mod.load_run(args.baseline)
    order = [t.strip() for t in args.tier_order.split(',') if t.strip()]
    tiers = {}
    for b in boards:
        tiers.setdefault(b.stratum, []).append(b)
    seq = [t for t in order if t in tiers]
    seq += [t for t in sorted(tiers) if t not in seq]
    run_id = args.run_id or '%s-%s' % (
        time.strftime('%Y%m%d-%H%M%S'), args.stack)
    for tier in seq:
        tdir, _m, records = runner.run_set(
            tiers[tier], args.stack, overrides, args.home,
            '%s-tier-%s' % (run_id, tier), exam=args.exam, log=_log)
        gate = compare_mod.nothing_worse(records, base['records'])
        if not gate['ok']:
            print('RAMP STOP: tier %s regresses against baseline %s:'
                  % (tier, args.baseline))
            for r in gate['regressions']:
                print('  %s' % r)
            return 3
        print('tier %s: nothing worse; continuing' % tier)
    print('ramp complete: no tier regressed')
    return 0


def cmd_compare(args):
    a = compare_mod.load_run(args.run_a)
    b = compare_mod.load_run(args.run_b)
    comp = compare_mod.compare_runs(a, b)
    md = compare_mod.render_compare_markdown(
        comp, title='A/B: %s vs %s' % (args.run_a, args.run_b))
    if args.out:
        with open(args.out, 'w') as f:
            f.write(md)
        print('wrote %s' % args.out)
    else:
        print(md)
    if args.gate:
        gate = compare_mod.nothing_worse(b['records'], a['records'])
        if gate['ok']:
            print('nothing-worse gate: PASS (B regresses nowhere vs A)')
            return 0
        print('nothing-worse gate: FAIL')
        for r in gate['regressions']:
            print('  %s' % r)
        return 3
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='bench_harness')
    ap.add_argument('--home', default=default_home(),
                    help='harness data root (cache/ + runs/)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('inventory'); _add_selection(p)
    p.set_defaults(fn=cmd_inventory)
    p = sub.add_parser('prep'); _add_selection(p)
    p.set_defaults(fn=cmd_prep)
    p = sub.add_parser('run'); _add_selection(p)
    p.add_argument('--stack', default='current')
    p.add_argument('--cfg-json', default=None,
                   help='JSON object of pipeline config overrides')
    p.add_argument('--run-id', default=None)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser('ramp'); _add_selection(p)
    p.add_argument('--stack', default='current')
    p.add_argument('--cfg-json', default=None)
    p.add_argument('--run-id', default=None)
    p.add_argument('--baseline', required=True,
                   help='baseline run directory to gate each tier on')
    p.add_argument('--tier-order', default='A,B,C',
                   help='stratum order for the ramp')
    p.set_defaults(fn=cmd_ramp)
    p = sub.add_parser('compare')
    p.add_argument('run_a'); p.add_argument('run_b')
    p.add_argument('--gate', action='store_true',
                   help='also run the nothing-worse gate (B vs A); '
                        'exit 3 on regression')
    p.add_argument('--out', default=None)
    p.set_defaults(fn=cmd_compare)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except inv.HeldOutError as e:
        print('REFUSED: %s' % e)
        return 4
    except KeyError as e:
        print('REFUSED: %s' % e)
        return 4


if __name__ == '__main__':
    sys.exit(main())
