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

"""Issue #178: the backend config page had no controls for the
Phase 2 run actions (the server accepted both keys since #171)
and never displayed tracemaker_provenance, so an operator could
not enable Reproducible / Finish-this-board from the page nor
tell which engine build a version string actually is (#173).
Static-page regression: the wiring must exist in the shipped HTML.
Run: python3 tests/test_config_page_controls.py"""
import os

PAGE = open(os.path.join(os.path.dirname(__file__), '..', 'docker', 'config_page.html')).read()

def test_controls_exist():
    assert 'id="rt_det"' in PAGE and 'id="rt_roo"' in PAGE
    assert 'Reproducible run' in PAGE and 'Finish this board' in PAGE

def test_load_and_save_wiring():
    assert "getElementById('rt_det').checked=!!c.routing.deterministic" in PAGE
    assert "getElementById('rt_roo').checked=!!c.routing.route_open_only" in PAGE
    assert 'deterministic:document.getElementById(\'rt_det\').checked' in PAGE
    assert 'route_open_only:document.getElementById(\'rt_roo\').checked' in PAGE

def test_llm_mode_guards_deterministic():
    # #185: the mode select must drive the same clear+disable the
    # dialog applies -- llm + checked rt_det is the poisoned pair.
    assert 'applyDetState' in PAGE
    assert 'onchange="applyDetState()"' in PAGE


def test_finish_disables_strip():
    assert 'applyRooState' in PAGE

def test_provenance_displayed():
    assert 'tracemaker_provenance' in PAGE          # status line from /health
    assert 'v.provenance' in PAGE                    # per-version, from /api/tracemaker

if __name__ == '__main__':
    test_controls_exist(); test_load_and_save_wiring()
    test_finish_disables_strip(); test_provenance_displayed()
    print('config page controls OK')
