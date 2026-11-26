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

"""Issue #180: the Phase 2 engine starved Nomad-class boards at
the shipped 20M work default (Stage C: 1 unconnected, stopped_by
= work; the 40M rerun, job 0d50e2a7, restored 0/0/14). Every
default surface now ships 40000000. The scaled_timeout tuning
reference is NOT a budget default and stays 20000000.
Run: python3 tests/test_work_default.py"""
import importlib.machinery, importlib.util, os, sys
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'ai_router'))
sys.path.insert(0, os.path.join(ROOT, 'docker'))
import config as cfgmod
import engine_run
import run_job


def _load_server():
    ld = importlib.machinery.SourceFileLoader('srv_uut', os.path.join(ROOT, 'docker', 'server.py'))
    sp = importlib.util.spec_from_loader('srv_uut', ld)
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


def test_defaults_are_40m():
    assert cfgmod.DEFAULTS['work'] == 40000000
    assert _load_server().default_config()['routing']['work'] == 40000000
    assert run_job.build_cfg({'work': 'plenty'}, {}, '/tm')['work'] == 40000000
    assert run_job.build_cfg({}, {}, '/tm')['work'] == 40000000


def test_remaining_surfaces():
    pipe = open(os.path.join(ROOT, 'ai_router', 'pipeline.py')).read()
    assert "get('work', 20000000)" not in pipe
    assert "get('work', 40000000)" in pipe
    dlg = open(os.path.join(ROOT, 'ai_router', 'dialog.py')).read()
    assert 'spin(40000000' in dlg and "get('work', 40000000)" in dlg
    page = open(os.path.join(ROOT, 'docker', 'config_page.html')).read()
    assert 'c.routing.work||40000000' in page


def test_timeout_reference_unchanged():
    # ref_work is the tuning reference for the leg constants, not a
    # budget default: at 40M the legs still double exactly.
    assert engine_run.scaled_timeout(3900, 20000000) == 3900
    assert engine_run.scaled_timeout(3900, 40000000) == 7800


if __name__ == '__main__':
    test_defaults_are_40m(); test_remaining_surfaces(); test_timeout_reference_unchanged()
    print('work default OK')
