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

"""Issue #76: importing the ai_router package (or a pure-Python
module through it) must work in a Python without pcbnew/wx --
__init__.py used to import the KiCad plugin unconditionally, so any
package-style import died with ModuleNotFoundError outside KiCad.
The plugin registration itself must still happen when pcbnew IS
available; that path is exercised by every pcbnew test in the suite.
Pure python (subprocess with pcbnew/wx blocked)."""
import os, subprocess, sys, tempfile

ROOT = os.path.join(os.path.dirname(__file__), '..')

def test_package_imports_without_pcbnew():
    d = tempfile.mkdtemp()
    for mod in ('pcbnew', 'wx'):
        with open(os.path.join(d, mod + '.py'), 'w') as f:
            f.write("raise ImportError('blocked for test')\n")
    code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
            "import ai_router; import ai_router.config; "
            "print('import-ok', ai_router.config.DEFAULTS['mode'])" % (d, os.path.abspath(ROOT)))
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert r.returncode == 0 and 'import-ok auto' in r.stdout, (r.returncode, r.stdout, r.stderr[-400:])

if __name__ == '__main__':
    test_package_imports_without_pcbnew(); print('package import OK')
