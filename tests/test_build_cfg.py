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

"""N1/#43 + N14/#56: backend job config is built from the plugin
DEFAULTS (single source), with defensive parsing and the engine
whitelist. Pure python."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'docker'))
import run_job

def test_defaults_reach_backend_jobs():
    cfg = run_job.build_cfg({}, {}, '/tm/bin/tracemaker')
    assert cfg['engine_portfolio'] == 'tm_relax'      # shipped default since v1.9.5
    assert cfg['reroute_accept'] == 'connectivity'    # not the dead 'faults' fallback
    assert cfg['max_passes'] == 4

def test_server_and_job_overrides_win():
    cfg = run_job.build_cfg({'max_passes': 2, 'engine_portfolio': 'krt'}, {}, '/tm')
    assert cfg['max_passes'] == 2 and cfg['engine_portfolio'] == 'krt'

def test_engine_whitelist_dedupe_cap():
    cfg = run_job.build_cfg({'engine_portfolio': 'tm_relax,tm_relax,bogus,krt,freerouting,tm_global,tm_escape'}, {}, '/tm')
    assert cfg['engine_portfolio'] == 'tm_relax,krt,freerouting,tm_global'

def test_non_numeric_does_not_crash():
    cfg = run_job.build_cfg({'max_passes': 'lots', 'work': 'plenty'}, {}, '/tm')
    assert cfg['max_passes'] == 4 and cfg['work'] == 20000000

def test_unlisted_defaults_keys_forward():
    # #74: keys with no explicit mapping in build_cfg were silently
    # dropped (cfg already held every DEFAULTS key, so main()'s
    # 'not in cfg' forwarding loop could never fire).
    rt = {'reroute_window_cap': 4, 'reroute_accept': 'faults',
          'reroute_margin': 9.0, 'pass_advice': 'priority',
          'advice_regions': True, 'max_tool_calls': 99}
    cfg = run_job.build_cfg(rt, {}, '/tm')
    assert cfg['reroute_window_cap'] == 4, cfg['reroute_window_cap']
    assert cfg['reroute_accept'] == 'faults', cfg['reroute_accept']
    assert cfg['reroute_margin'] == 9.0, cfg['reroute_margin']
    assert cfg['pass_advice'] == 'priority', cfg['pass_advice']
    assert cfg['advice_regions'] is True, cfg['advice_regions']
    assert cfg['max_tool_calls'] == 99, cfg['max_tool_calls']

def test_server_owned_keys_not_overridable():
    cfg = run_job.build_cfg({'endpoint': 'http://evil', 'api_key': 'x'},
                            {'endpoint': 'http://llm', 'api_key': 'k'}, '/tm')
    assert cfg['endpoint'] == 'http://llm' and cfg['api_key'] == 'k'
