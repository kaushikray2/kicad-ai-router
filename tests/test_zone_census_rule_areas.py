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

"""Issue #181: board_facts counted rule areas as zones. On the
Nomad self-test fixture (sha256 c2926c5221a84b...), b.Zones() is
19, of which 13 have GetIsRuleArea() == True (all empty net
names; enumerated zone-by-zone during the fix) and 6 are copper
pours (5x GND, 1x 3V3) -- so zone_count must be 6. (The audit
report said 11 rule areas / 8 zones; the direct enumeration on
the sha-verified fixture says 13 / 6, and the enumeration wins.)
The worker pour censuses carry the same skip for consistency.
Run: python3 tests/test_zone_census_rule_areas.py"""
import glob, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))

FIXTURE_SHA = 'c2926c5221a84b9f707b2d3d73baf50a56713224a2485ea28ee3c722c99343d3'


def _fixture():
    cands = [os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest', 'nomad-revi-unrouted.kicad_pcb')]
    cands += sorted(glob.glob(os.path.expanduser('~/Documents/workspace/agent-runs/*/docker/selftest/nomad-revi-unrouted.kicad_pcb')))
    cands += sorted(glob.glob(os.path.expanduser('~/Documents/workspace/agent-runs/*/inputs/nomad-revi-unrouted.kicad_pcb')))
    import hashlib
    for c in cands:
        if os.path.exists(c) and hashlib.sha256(open(c, 'rb').read()).hexdigest() == FIXTURE_SHA:
            return c
    return None


def _part():
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        import pytest
        pytest.importorskip('pcbnew')
        return
    fx = _fixture()
    if fx is None:
        import pytest
        pytest.skip('Nomad fixture not present on this machine')
        return
    import board_facts
    facts = board_facts.load_facts(fx)
    assert facts is not None
    assert facts['zone_count'] == 6, facts['zone_count']
    nets = {n for v in facts['zones_by_layer'].values() for n in v}
    assert '' not in nets and 'GND' in nets, nets
    import importlib.machinery, importlib.util
    ld = importlib.machinery.SourceFileLoader('w_uut9', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
    sp = importlib.util.spec_from_loader('w_uut9', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)
    summ = w.cmd_summary(fx, fast=True)
    assert '' not in (summ.get('pour_nets') or [])
    assert 'GND' in (summ.get('pour_nets') or []), summ.get('pour_nets')
    assert all(k for k in (summ.get('pour_layers') or {}))


def test_zone_census_skips_rule_areas():
    _part()


if __name__ == '__main__':
    _part()
    print('zone census rule areas OK')
