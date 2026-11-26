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

"""Issue #123: the config import fallbacks were absolute-only and
silent. Inside KiCad the package context makes a top-level
'config' module unimportable, so pipeline/orchestrator fell back
to {} -- and their hard-coded literals disagreed with config.py
(reroute_accept 'faults' vs 'connectivity', engine_portfolio ''
vs 'tm_relax'), so a caller passing a partial config silently got
the other behaviour. run_job.build_cfg swallowed a failed config
import with a bare pass (the N1 failure mode). Now: relative
import first (as dialog.py already did), literals reconciled with
config.py, and build_cfg logs the failure.
Run: python3 tests/test_config_import_fallback.py"""
import os, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# (1) package context: _CFG_DEFAULTS must be config.py's real DEFAULTS
code = ("import sys; sys.path.insert(0, %r); "
        "import ai_router.pipeline as p; import ai_router.orchestrator as o; "
        "print('PIPE', p._CFG_DEFAULTS.get('reroute_accept'), len(p._CFG_DEFAULTS)); "
        "print('ORC', o._CFG_DEFAULTS.get('engine_portfolio'), len(o._CFG_DEFAULTS))" % ROOT)
r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
assert r.returncode == 0, (r.stdout, r.stderr[-500:])
assert 'PIPE connectivity' in r.stdout and 'ORC tm_relax' in r.stdout, r.stdout

# (2) run_job.build_cfg with a broken config module: must LOG, not pass silently
d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, 'ai_router'))
open(os.path.join(d, 'ai_router', 'pipeline.py'), 'w').write('# stub for find_plugin\n')
open(os.path.join(d, 'ai_router', 'config.py'), 'w').write('def broken(:\n')
shutil.copy(os.path.join(ROOT, 'docker', 'run_job.py'), os.path.join(d, 'run_job.py'))
code = ("import importlib.util, sys; "
        "sp = importlib.util.spec_from_file_location('rj', %r); "
        "m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); "
        "cfg = m.build_cfg({}, {}, '/bin/true'); "
        "print('CFG endpoint:', cfg.get('endpoint'))" % os.path.join(d, 'run_job.py'))
r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=d)
assert r.returncode == 0, (r.stdout, r.stderr[-500:])
assert 'CFG endpoint:' in r.stdout, r.stdout
assert 'config' in r.stderr.lower(), 'build_cfg swallowed the config import failure silently: %r' % r.stderr
print('PASS test_config_import_fallback')
