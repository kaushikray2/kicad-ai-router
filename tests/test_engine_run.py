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

"""v1.12.2 (#170/#171): the TraceMaker Phase 2 run contracts --
route command-line flags for the two new run actions, the
structured exit-code mapping, stopped_by wording and why_open
line extraction. Pure python.
Run: python3 tests/test_engine_run.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import engine_run as er


def test_default_flags_reproduce_the_pre_phase2_command_line():
    assert er.route_flags({}, 20000000) == ['--work', '20000000', '--time', '3600']
    assert er.route_flags({'deterministic': False, 'route_open_only': False}, 500, 1800) == ['--work', '500', '--time', '1800']


def test_deterministic_drops_the_time_limit():
    flags = er.route_flags({'deterministic': True}, 20000000)
    assert flags == ['--work', '20000000', '--deterministic']
    assert '--time' not in flags


def test_route_open_only_flag_and_reroute_suppression():
    assert er.route_flags({'route_open_only': True}, 100) == ['--work', '100', '--route-open-only', '--time', '3600']
    assert '--route-open-only' not in er.route_flags({'route_open_only': True}, 100, open_only=False)
    both = er.route_flags({'route_open_only': True, 'deterministic': True}, 100)
    assert both == ['--work', '100', '--route-open-only', '--deterministic']


def test_exit_code_mapping():
    assert er.engine_error(0) is None
    assert er.engine_error(3) is None            # open remain: a result, not an error
    assert er.engine_error(0, {'exit_code': 3}) is None
    assert 'exit 1' in er.engine_error(1)
    assert 'usage' in er.engine_error(2)
    assert er.engine_error(0, {'exit_code': 1}) is not None   # the JSON wins over the process code
    assert er.engine_error(7) is not None


def test_stopped_by_wording():
    assert er.stopped_by_text('work') == 'the work budget was spent'
    assert er.stopped_by_text('time') == 'the time limit was reached'
    assert er.stopped_by_text('rounds') == 'the round limit was reached'
    assert er.stopped_by_text(None) is None
    assert er.stopped_by_text('') is None


def test_why_open_lines():
    summary = {'why_open': {'proved': [{'text': 'U3.8 is walled in by U3 body on F.Cu'},
                                       {'text': 'J2.3 is walled in by the board edge'}],
                            'next': ['raise --work', 'add a layer']}}
    lines = er.why_open_lines(summary)
    assert lines[0] == 'Why open: U3.8 is walled in by U3 body on F.Cu'
    assert lines[1].startswith('Why open: J2.3')
    assert lines[2] == 'Engine suggests: raise --work'
    assert lines[3] == 'Engine suggests: add a layer'
    assert er.why_open_lines({}) == []
    assert er.why_open_lines({'why_open': None}) == []
    assert er.why_open_lines(None) == []
    many = {'why_open': {'proved': [{'text': 'p%d' % i} for i in range(25)], 'next': []}}
    capped = er.why_open_lines(many)
    assert len(capped) == 21 and '5 more proofs' in capped[-1]


if __name__ == '__main__':
    for _n, _f in sorted(list(globals().items())):
        if _n.startswith('test_'):
            _f()
    print('PASS test_engine_run')
