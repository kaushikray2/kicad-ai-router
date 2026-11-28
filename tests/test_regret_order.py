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

"""Phase 2E (#65): regret-order sequencing for multi-net repair.

cmd_ilp_repair's default selection is a joint cost optimisation
(ILP, greedy fallback) applied in cost order -- local cost decides
who commits first. With --regret-order, the repair set commits in
regret order instead: a connection's regret is its second-best
available candidate's cost minus its best; single-candidate
connections commit first; connections whose candidates all
conflict with committed legs wait.

Pinned here (the pure pick, _regret_pick): single-candidate
first, highest regret next, deterministic ties, a conflicting
best falls back to the next candidate (which then counts as the
connection's only option), and a fully blocked set picks None.
Requires pcbnew (KiCad python) for the worker load; skipped
elsewhere. Run: python3 tests/test_regret_order.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
    import worker as w
except Exception:
    pcbnew = None

def _cand(net, cost, x=0.0, y=0.0, ln=5.0):
    return {'net': net, 'victims': (), 'segs': [(0, x, y, x + ln, y)],
            'vias': [], 'cost': cost, 'tw': 0.25, 'vr': 0.3}

def test_single_candidate_commits_first():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    by_net = {'A': [_cand('A', 10.0, y=0.0), _cand('A', 90.0, y=20.0)],
              'B': [_cand('B', 50.0, y=40.0)]}
    net, cand = w._regret_pick(by_net, [], 0.2)
    assert net == 'B' and cand['cost'] == 50.0, (net, cand)

def test_highest_regret_next():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    by_net = {'A': [_cand('A', 10.0, y=0.0), _cand('A', 30.0, y=20.0)],   # regret 20
              'B': [_cand('B', 10.0, y=40.0), _cand('B', 15.0, y=60.0)]}  # regret 5
    net, cand = w._regret_pick(by_net, [], 0.2)
    assert net == 'A' and cand['cost'] == 10.0, (net, cand)

def test_conflicting_best_falls_back():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    committed = [_cand('Z', 1.0, x=0.0, y=0.0)]  # occupies y=0, x 0..5
    by_net = {'A': [_cand('A', 10.0, x=1.0, y=0.0),   # conflicts with Z
                    _cand('A', 30.0, x=0.0, y=20.0)]}  # the fallback
    net, cand = w._regret_pick(by_net, committed, 0.2)
    assert net == 'A' and cand['cost'] == 30.0, (net, cand)

def test_fully_blocked_picks_none():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    committed = [_cand('Z', 1.0, x=0.0, y=0.0)]
    by_net = {'A': [_cand('A', 10.0, x=1.0, y=0.0)]}
    assert w._regret_pick(by_net, committed, 0.2) is None

def test_ties_are_deterministic():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    by_net = {'B': [_cand('B', 10.0, y=40.0), _cand('B', 20.0, y=60.0)],
              'A': [_cand('A', 5.0, y=0.0), _cand('A', 15.0, y=20.0)]}  # same regret 10, cheaper best
    net, cand = w._regret_pick(by_net, [], 0.2)
    assert net == 'A' and cand['cost'] == 5.0, (net, cand)

if __name__ == '__main__':
    test_single_candidate_commits_first()
    test_highest_regret_next()
    test_conflicting_best_falls_back()
    test_fully_blocked_picks_none()
    test_ties_are_deterministic()
    print('regret order OK')
