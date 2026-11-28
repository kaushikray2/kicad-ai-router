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

"""Issue #46: the DSN layer-law rewrite (ai_router/dsn_law.py).

KiCad's Specctra DSN export conveys the board's stackup layer
types, not the run's layer law; the worker rewrites the exported
copy so every banned layer is (type power) -- Specctra's plane
type, never routed -- and verifies the rewrite landed. These
tests pin the pure text machinery: block parsing, the
signal->power rewrite, the already-power no-op (a stackup-power
layer exports under its plane net's name), and the unverifiable
ban that must surface instead of passing silently.
Run: python3 tests/test_dsn_law.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import dsn_law

_DSN = """(pcb /tmp/board.dsn
  (structure
    (layer F.Cu
      (type signal)
      (property
        (index 0)
      )
    )
    (layer GND
      (type power)
      (property
        (index 1)
      )
    )
    (layer B.Cu
      (type signal)
      (property
        (index 3)
      )
    )
  )
)"""


def test_dsn_blocks_parse():
    blocks = dsn_law.layer_blocks(_DSN)
    assert blocks == {0: ('F.Cu', 'signal'), 1: ('GND', 'power'), 3: ('B.Cu', 'signal')}, blocks


def test_dsn_rewrite_signal_layer_to_power():
    text, report = dsn_law.apply_power_layers(_DSN, {0: 'F.Cu'})
    assert report[0] == {'layer': 'F.Cu', 'dsn_name': 'F.Cu', 'before': 'signal', 'after': 'power'}, report
    assert dsn_law.layer_blocks(text)[0] == ('F.Cu', 'power')
    assert dsn_law.unpowered_bans(text, {0: 'F.Cu'}) == []
    # untouched layers stay untouched
    assert dsn_law.layer_blocks(text)[3] == ('B.Cu', 'signal')


def test_dsn_rewrite_already_power_is_noop():
    # a stackup-power layer exports under its plane net's name and
    # is already a power layer: no rewrite, verification passes
    text, report = dsn_law.apply_power_layers(_DSN, {1: 'In1.Cu'})
    assert text == _DSN
    assert report[1]['dsn_name'] == 'GND' and report[1]['after'] == 'power'
    assert dsn_law.unpowered_bans(text, {1: 'In1.Cu'}) == []


def test_dsn_unverifiable_ban_is_reported():
    # a banned index with no DSN block must surface, never pass
    text, report = dsn_law.apply_power_layers(_DSN, {2: 'In2.Cu'})
    assert report[2]['dsn_name'] is None
    assert dsn_law.unpowered_bans(text, {2: 'In2.Cu'}) == [2]
    assert dsn_law.unpowered_bans(_DSN, {3: 'B.Cu'}) == [3]


_DSN_QUOTED = """(pcb /tmp/board.dsn
  (structure
    (layer F.Cu
      (type signal)
      (property
        (index 0)
      )
    )
    (layer "GND Plane"
      (type signal)
      (property
        (index 1)
      )
    )
    (layer In2.Cu
      (type mixed)
      (property
        (index 2)
      )
    )
  )
)"""


def test_dsn_quoted_layer_name_parses():
    # #232: KiCad quotes a DSN layer name containing a space.
    # The block must parse (name returned unquoted) -- before
    # the fix the \S+ name pattern skipped it, the banned
    # layer's ban was unverifiable and the leg failed.
    blocks = dsn_law.layer_blocks(_DSN_QUOTED)
    assert blocks[1] == ('GND Plane', 'signal'), blocks
    text, report = dsn_law.apply_power_layers(_DSN_QUOTED, {1: 'In1.Cu'})
    assert report[1]['dsn_name'] == 'GND Plane' and report[1]['after'] == 'power', report
    assert dsn_law.layer_blocks(text)[1] == ('GND Plane', 'power')
    assert dsn_law.unpowered_bans(text, {1: 'In1.Cu'}) == []


def test_dsn_mixed_layer_rewrites_to_power():
    # Defensive: KiCad's exporter normalises a mixed layer to
    # signal, but a banned mixed layer in any DSN is routable
    # copper and must still be rewritten to power.
    text, report = dsn_law.apply_power_layers(_DSN_QUOTED, {2: 'In2.Cu'})
    assert report[2]['before'] == 'mixed' and report[2]['after'] == 'power', report
    assert dsn_law.layer_blocks(text)[2] == ('In2.Cu', 'power')


if __name__ == '__main__':
    test_dsn_blocks_parse()
    test_dsn_rewrite_signal_layer_to_power()
    test_dsn_rewrite_already_power_is_noop()
    test_dsn_unverifiable_ban_is_reported()
    test_dsn_quoted_layer_name_parses()
    test_dsn_mixed_layer_rewrites_to_power()
    print('dsn law OK')
