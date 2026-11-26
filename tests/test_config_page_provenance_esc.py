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

"""Issue #197: the config page interpolated the engine
provenance (text read from an installed archive's
PROVENANCE.txt -- archive-author-controlled) into innerHTML
unescaped at three sites, although the page's own esc() helper
existed. Provenance must go through prov() (join lines with
' | ' like the dialog, then esc), and the version/sha fields
rendered beside it through esc().
Run: python3 tests/test_config_page_provenance_esc.py"""
import os, re

PAGE = os.path.join(os.path.dirname(__file__), '..', 'docker', 'config_page.html')

def _src():
    with open(PAGE) as f: return f.read()

def test_provenance_never_interpolated_raw():
    src = _src()
    assert 'function prov(' in src
    assert not re.search(r"\+\s*\(h\.tracemaker_provenance", src), 'raw tracemaker_provenance interpolation remains'
    assert not re.search(r"\+\s*\(v\.provenance", src), 'raw v.provenance interpolation remains'
    assert src.count('prov(h.tracemaker_provenance') == 2  # loadAll + loadHealthOnly
    assert src.count('prov(v.provenance') == 1             # the installed-versions table
    # the fields rendered beside the provenance are escaped too
    assert "esc(v.version)" in src and 'esc(h.backend_version)' in src

def test_prov_joins_lines_then_escapes():
    src = _src()
    m = re.search(r'function prov\(s\)\{[^}]+\}', src)
    assert m, 'prov() missing'
    body = m.group(0)
    assert "' | '" in body and 'esc(' in body

if __name__ == '__main__':
    test_provenance_never_interpolated_raw()
    test_prov_joins_lines_then_escapes()
    print('config page provenance esc OK')
