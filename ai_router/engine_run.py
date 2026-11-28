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

"""TraceMaker Phase 2 run contracts (v1.12.2, #170/#171).

Pure helpers for the engine's structured run surface: the route
command-line tail (work budget, --deterministic, --route-open-only),
the structured exit-code mapping, stopped_by wording and the
why_open plain-language lines. No pcbnew, no I/O -- unit-testable
anywhere.

Verified against the binary built at fork tag phase-2-end (commit
598c4e51c2938eaf784a433afc2451a432652399), 2026-11-25:

- exit 0 = routed, 3 = open remain (a normal partial result),
  1 = failed -- the binary ALSO returns 1 for validation refusals
  (a bad --reroute-window value, --deterministic without a work
  budget), each with a specific message in the log; the engine's
  context document says those are exit 2, the binary says 1, and
  the binary wins. 1 and 2 are therefore both fatal to a pass and
  the engine's own message is what gets surfaced.
- --deterministic with a work budget reproduces the output board
  byte-for-byte; the mode refuses to run without a work budget,
  and a --time bound is never passed in this mode (if a clock
  bound ever binds, the engine reports deterministic: false).
"""


def route_flags(cfg, work, time_s=3600, open_only=True, relax=False):
    """The work/mode tail of an engine route command line.

    A default config reproduces the pre-Phase-2 command line
    exactly: ['--work', W, '--time', T]. route_open_only appends
    '--route-open-only' (suppressed for the --reroute call, where
    the mode does not apply). deterministic appends
    '--deterministic' and drops '--time': the run is bounded by
    the work budget alone.

    relax=True marks a --relax leg (#184, #52): the engine budgets
    a relax run by rounds, not by --work (its cli.md: '--work does
    not limit it'), and its deterministic gate counts a relax run
    as work-bounded only via --round-work/--rounds -- a
    deterministic relax leg carrying --work alone is refused
    outright. The configured budget is therefore translated into
    rounds of the engine's own 20M round quantum (the split its
    rescue path derives from a work budget), keeping the same
    total budget. #184 applied the translation under deterministic
    only; #52 extends it to every mode. Default mode keeps
    --time as the backstop limit the rounds are chosen to finish
    well inside: the item-3 probes (2026-11-27) found that with
    --time absent the engine caps the tournament itself
    (~120 s on Nomad whatever the work flags say -- the leg
    stops at ~52% completion), while at the floored budget a
    relax leg reaches its natural stop well inside the limit
    (Nomad 627 s of 1800 s, time_limited False, work identical
    to the unbounded run). The leg's ledger record carries the
    rounds budget, the seconds and the engine's time_limited
    flag, so which bound ended a leg is provable per run. An
    unusable budget keeps the historical --work + --time line
    in default mode (and the engine's refusal under
    deterministic).
    """
    flags = ['--work', str(work)]
    if open_only and cfg.get('route_open_only'):
        flags.append('--route-open-only')
    _rb = relax_round_budget(work) if relax else None
    if _rb:
        flags += ['--round-work', str(_rb[0]), '--rounds', str(_rb[1])]
    if cfg.get('deterministic'):
        flags.append('--deterministic')
    else:
        flags += ['--time', str(time_s)]
    return flags


def relax_round_budget(work):
    """The round budget a --relax leg runs under (#52): rounds of
    the engine's 20M round quantum, floored at 50 and capped at
    the engine's 200 maximum. The configured work budget never
    bounded a relax leg (the engine ignores --work for relax), so
    the floor -- not the work number -- is the operative size;
    it is set from measured saturation (Phase 2E item-3 probes,
    2026-11-27): juniper's tournament reaches its natural stop
    by 10 rounds, STM32's by 50, and on Nomad a 50-round budget
    consumes exactly the unbounded run's work (94,830,888
    expansions, time_limited False) while a 2-round budget
    binds and degrades the candidate (juniper completion
    80.85%, time_limited True). Returns (round_work, rounds),
    or None for an unusable budget (zero, negative,
    non-numeric), where route_flags keeps the historical line.
    One budget for every mode, so a leg's budget reads the
    same in the ledger whichever mode ran it."""
    try:
        _w = int(work)
    except (TypeError, ValueError):
        return None
    if _w <= 0:
        return None
    _rounds = max(50, min(200, -(-_w // 20000000)))
    return (20000000, _rounds)


def scaled_timeout(base_seconds, work, ref_work=20000000):
    """Wall-clock timeout for an engine leg, scaled by work budget.

    v1.12.2 (#176): the leg constants (3900 / 2400 / 1900 s) were
    tuned at the 20M work budget; a leg's runtime grows with the
    budget it is given, so a recalibrated budget would let a
    healthy leg outgrow a fixed timeout and be killed mid-run.
    Linear in work, floored at 1x -- at or below the tuning budget
    the historical values hold exactly. An unusable budget value
    falls back to the tuned constant.
    """
    try:
        w = float(work)
    except (TypeError, ValueError):
        w = float(ref_work)
    if w <= 0:
        w = float(ref_work)
    return int(round(base_seconds * max(1.0, w / float(ref_work))))


def engine_error(rc, summary=None):
    """Error text when an engine route run failed, else None.

    The summary JSON's exit_code wins over the process return
    code when both exist. 0 and 3 are results, not errors.
    """
    code = None
    if isinstance(summary, dict):
        code = summary.get('exit_code')
    if code is None:
        code = rc
    if code in (0, 3):
        return None
    if code == 2:
        return 'TraceMaker refused the run (usage error, exit 2)'
    if code == 1:
        return 'TraceMaker run failed (exit 1)'
    return 'TraceMaker exited with unexpected code %r' % (code,)


_STOPPED_BY = {
    'work': 'the work budget was spent',
    'time': 'the time limit was reached',
    'rounds': 'the round limit was reached',
}


def stopped_by_text(stopped_by):
    """Plain wording for the summary's stopped_by field."""
    if not stopped_by:
        return None
    return _STOPPED_BY.get(stopped_by, 'stopped_by=%s' % (stopped_by,))


def why_open_lines(summary, cap=20):
    """Plain-language log lines for a partial run's why_open block.

    proved[] entries become 'Why open: ...' lines and next[]
    entries become 'Engine suggests: ...' lines, capped so a bad
    board cannot flood the job log; the full block stays in the
    route JSON.
    """
    if not isinstance(summary, dict):
        return []
    wo = summary.get('why_open')
    if not isinstance(wo, dict):
        return []
    lines = []
    proved = wo.get('proved') or []
    for entry in proved[:cap]:
        text = entry.get('text') if isinstance(entry, dict) else None
        if text:
            lines.append('Why open: %s' % text)
    if len(proved) > cap:
        lines.append('Why open: ... and %d more proofs (see the route JSON)' % (len(proved) - cap))
    for nxt in (wo.get('next') or [])[:4]:
        if isinstance(nxt, str) and nxt:
            lines.append('Engine suggests: %s' % nxt)
    return lines
