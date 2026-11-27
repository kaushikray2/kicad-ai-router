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

"""KiCad DRC parsing and routing-caused deltas (#62).

"Worse" is defined on routing-caused DRC: per board, per violation
kind, the KiCad DRC of the routed board minus the KiCad DRC of the
stripped input it was routed from. Raw counts bill the router for
faults the board already had; the delta does not. Unconnected items
are tracked as their own kind ('unconnected_items') alongside the
violation types, matching the pipeline gate's class accounting.
Estimator-vs-KiCad drift is computed by the runner and reported
separately -- it is never folded into these deltas or the gate.
"""

UNCONNECTED_KIND = 'unconnected_items'


def parse_drc_report(doc):
    """Summarise one kicad-cli DRC JSON document.

    Returns by_kind (violation types only), total (violation count,
    excluding unconnected items -- KiCad's own accounting), the
    unconnected item count, and kinds (by_kind plus the unconnected
    count under UNCONNECTED_KIND), which is what caused deltas use.
    """
    by_kind = {}
    for v in (doc or {}).get('violations') or []:
        t = v.get('type') or 'unknown'
        by_kind[t] = by_kind.get(t, 0) + 1
    unconnected = len((doc or {}).get('unconnected_items') or [])
    kinds = dict(by_kind)
    if unconnected:
        kinds[UNCONNECTED_KIND] = unconnected
    return {'by_kind': by_kind, 'total': sum(by_kind.values()),
            'unconnected': unconnected, 'kinds': kinds}


def caused_deltas(after_kinds, before_kinds):
    """Per-kind (after - before) over the union of kinds."""
    after_kinds = after_kinds or {}
    before_kinds = before_kinds or {}
    return {k: after_kinds.get(k, 0) - before_kinds.get(k, 0)
            for k in sorted(set(after_kinds) | set(before_kinds))}


def caused_increases(deltas):
    """The positive part of a caused-delta dict: what routing added."""
    return {k: d for k, d in (deltas or {}).items() if d > 0}
