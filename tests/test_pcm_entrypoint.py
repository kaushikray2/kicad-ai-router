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

"""Issue #156: the PCM zip shipped the plugin nested at
plugins/ai_router/ with no plugins/__init__.py. KiCad's plugin
loader (pcbnew.LoadPlugins) loads a package directory only when
it contains an __init__.py -- otherwise it records 'Skip subdir'
and moves on, so the PCM-installed plugin never appeared (no
toolbar button, no Tools -> External Plugins entry) even though
the code imported fine by hand.

The fix: build_package.py writes a PCM entry point at
plugins/__init__.py that imports the bundled ai_router package
(ai_router/__init__.py performs the registration). This test
builds the package in a scratch copy, checks the zip layout,
then extracts the zip the way PCM installs it
(<3rdparty>/plugins/ca_siliconbrane_ai-router/) and drives
KiCad's own LoadPlugins at it: the package module must load and
an AIRouterPlugin must register. The loader half needs pcbnew
and is skipped without it.
Run: python3 tests/test_pcm_entrypoint.py"""
import os, shutil, subprocess, sys, tempfile, zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PKG_DIRNAME = 'ca_siliconbrane_ai-router'  # identifier, dots as underscores

HARNESS = '''
import gc, os, sys
import pcbnew
tp = os.path.join(sys.argv[1], '3rdparty', 'plugins')
pcbnew.LoadPlugins(thirdpartypath=tp)
insts = [o for o in gc.get_objects() if type(o).__name__ == 'AIRouterPlugin']
print('LOADED=%s' % ('@PKG@' in pcbnew.KICAD_PLUGINS))
print('SKIPPED=%s' % ('Skip subdir' in pcbnew.NOT_LOADED_WIZARDS))
print('INSTANCES=%d' % len(insts))
print('NAME=%s' % (insts[0].name if insts else ''))
'''.replace('@PKG@', PKG_DIRNAME)

def test_pcm_entrypoint():
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, 'build_package.py'), d)
    shutil.copytree(os.path.join(ROOT, 'ai_router'), os.path.join(d, 'ai_router'))
    r = subprocess.run([sys.executable, os.path.join(d, 'build_package.py')],
                       capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr[-400:])
    zips = [f for f in os.listdir(os.path.join(d, 'pcm-repo')) if f.endswith('.zip')]
    assert len(zips) == 1, zips
    zpath = os.path.join(d, 'pcm-repo', zips[0])
    names = zipfile.ZipFile(zpath).namelist()
    assert 'plugins/__init__.py' in names, 'PCM entry point missing from zip: %s' % (names,)
    assert 'plugins/ai_router/__init__.py' in names, names
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        print('SKIP loader half (no pcbnew); zip layout OK')
        return
    # Extract the way PCM installs: zip plugins/* lands in
    # <3rdparty>/plugins/<identifier>/ with plugins/ stripped.
    dest = os.path.join(d, 'final', '3rdparty', 'plugins', PKG_DIRNAME)
    os.makedirs(dest)
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if n.startswith('plugins/') and not n.endswith('/'):
                target = os.path.join(dest, n[len('plugins/'):])
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, 'wb') as f:
                    f.write(z.read(n))
    harness = os.path.join(d, 'harness.py')
    with open(harness, 'w') as f:
        f.write(HARNESS)
    home = os.path.join(d, 'home'); os.makedirs(home)
    env = dict(os.environ, HOME=home)
    r = subprocess.run([sys.executable, harness, os.path.join(d, 'final')],
                       capture_output=True, text=True, env=env)
    out = dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)
    assert out.get('LOADED') == 'True', (r.stdout, r.stderr[-600:])
    assert out.get('SKIPPED') == 'False', (r.stdout, r.stderr[-600:])
    assert int(out.get('INSTANCES', '0')) >= 1, (r.stdout, r.stderr[-600:])
    assert out.get('NAME') == 'AI Router (LLM orchestrator)', out
    print('PCM entrypoint OK: KiCad LoadPlugins registered %s' % out['NAME'])

if __name__ == '__main__':
    test_pcm_entrypoint(); print('PASS test_pcm_entrypoint')
