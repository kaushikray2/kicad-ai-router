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

"""Specctra DSN layer-law export for external engines (#46).

KiCad's ExportSpecctraDSN conveys the BOARD's stackup layer types,
not the run's layer law: a copper layer the run bans (no_tracks_on)
but the stackup calls signal is exported as a routable signal
layer, so Freerouting routes on it and the gate then counts those
tracks as faults -- the external engine loses by construction.
This module rewrites the exported DSN copy so every banned layer
is declared (type power), which Specctra routers treat as a plane
(never routed), and verifies the rewrite landed. Pure text work,
no pcbnew -- the worker resolves banned canonical layer names to
copper stack positions and hands them here.

KiCad writes one block per copper layer, in stack order:

    (layer F.Cu
      (type signal)
      (property
        (index 0)
      )
    )

A stackup-power layer is exported under its plane NET's name
(e.g. GND), already (type power) -- the rewrite is a no-op there
and verification still passes.
"""
import re

_LAYER_BLOCK = re.compile(
    r'\(layer\s+(?P<name>\S+)\s*\(type\s+(?P<type>signal|power)\)'
    r'\s*\(property\s*\(index\s+(?P<index>\d+)\)')


def layer_blocks(dsn_text):
    """{stack_index: (dsn_layer_name, type)} for every DSN layer."""
    out = {}
    for m in _LAYER_BLOCK.finditer(dsn_text):
        out[int(m.group('index'))] = (m.group('name'), m.group('type'))
    return out


def apply_power_layers(dsn_text, banned_positions):
    """Rewrite banned layers' DSN blocks to (type power).

    banned_positions: {stack_index: canonical_layer_name}. Returns
    (new_text, report) where report is {stack_index:
    {'layer': canonical name, 'dsn_name': name found, 'before':
    type found, 'after': type written}}. An index with no DSN
    block is reported with 'dsn_name': None so the caller can
    fail the leg -- an unverifiable export must not run (#46:
    'export banned layers as type power in the DSN copy and
    verify').
    """
    blocks = layer_blocks(dsn_text)
    report = {}
    for idx in sorted(banned_positions):
        found = blocks.get(idx)
        report[idx] = {'layer': banned_positions[idx],
                       'dsn_name': found[0] if found else None,
                       'before': found[1] if found else None,
                       'after': found[1] if found else None}

    def _sub(m):
        idx = int(m.group('index'))
        if idx in banned_positions and m.group('type') == 'signal':
            report[idx]['after'] = 'power'
            return m.group(0).replace('(type signal)', '(type power)', 1)
        return m.group(0)

    return _LAYER_BLOCK.sub(_sub, dsn_text), report


def unpowered_bans(dsn_text, banned_positions):
    """Banned stack indices whose DSN block is missing or not
    (type power) -- non-empty means the export does not convey
    the law and the leg must not run."""
    blocks = layer_blocks(dsn_text)
    return sorted(idx for idx in banned_positions
                  if blocks.get(idx, (None, None))[1] != 'power')
