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

"""Issue #196: #180 recalibrated the default work budget to
40M, but a config persisted by a pre-#180 build carries the old
20M default verbatim (server routing.work, client 'work') and
nothing migrated it -- upgraded installs kept starving
Nomad-class boards. The migration is value-targeted: exactly
20000000 (the old shipped default) becomes 40000000; any other
value is the operator's own. Also: run_job's status gate now
carries banned_layer_tracks_delta, and selftest_status gates
its verdict on the delta (#175 semantics), not the raw count.
Run: python3 tests/test_work_migration.py"""
import importlib.machinery, importlib.util, json, os, tempfile

ROOT = os.path.join(os.path.dirname(__file__), '..')
_seq = [0]

def _load_server(data_dir):
    _seq[0] += 1
    os.environ['AIR_DATA_DIR'] = data_dir
    name = 'air_server_uut196_%d' % _seq[0]
    ld = importlib.machinery.SourceFileLoader(name, os.path.join(ROOT, 'docker', 'server.py'))
    sp = importlib.util.spec_from_loader(name, ld)
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m

def _write_cfg(data, cfg):
    d = os.path.join(data, 'config'); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'config.json'), 'w') as f: json.dump(cfg, f)

def test_server_migrates_old_default_work():
    data = os.path.join(tempfile.mkdtemp(), 'data')
    srv = _load_server(data)
    cfg = srv.default_config(); cfg['routing']['work'] = 20000000
    _write_cfg(data, cfg)
    got = srv.load_config()
    assert got['routing']['work'] == 40000000, got['routing']
    with open(os.path.join(data, 'config', 'config.json')) as f:
        assert json.load(f)['routing']['work'] == 40000000  # persisted, not just in memory
    # an operator-chosen value is NOT the old default: untouched
    cfg = srv.default_config(); cfg['routing']['work'] = 12345678
    _write_cfg(data, cfg)
    assert srv.load_config()['routing']['work'] == 12345678

def test_client_migrates_old_default_work():
    ld = importlib.machinery.SourceFileLoader('air_config_uut196', os.path.join(ROOT, 'ai_router', 'config.py'))
    sp = importlib.util.spec_from_loader('air_config_uut196', ld)
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    p = os.path.join(tempfile.mkdtemp(), 'config.json')
    m.CONFIG_PATH = p
    with open(p, 'w') as f: json.dump({'work': 20000000}, f)
    assert m.load()['work'] == 40000000
    with open(p, 'w') as f: json.dump({'work': 999}, f)
    assert m.load()['work'] == 999
    with open(p, 'w') as f: json.dump({}, f)
    assert m.load()['work'] == 40000000  # the shipped default itself

def test_selftest_verdict_uses_banned_delta():
    data = os.path.join(tempfile.mkdtemp(), 'data')
    srv = _load_server(data)
    _write_cfg(data, srv.default_config())
    sd = os.path.join(data, 'selftest'); os.makedirs(sd)
    with open(os.path.join(sd, 'baselines.json'), 'w') as f:
        json.dump({'hbridge-driver.kicad_pcb': {'unconnected_total': 0, 'split_nets': 0, 'copper_violations': 0}}, f)
    def verdict(gate):
        srv._jobs.clear()
        srv._jobs['j1'] = {'kind': 'selftest', 'status': 'done', 'board_name': 'hbridge-driver.kicad_pcb',
                           'created': '2026-11-26 00:00:00', 'gate': gate}
        rows = {r['board']: r for r in srv.selftest_status()}
        return rows['hbridge-driver.kicad_pcb']['verdict']
    base = {'unconnected_total': 0, 'split_nets': [], 'copper_violations': 0}
    # input already carried 5 banned-layer tracks; the run added none -> PASS
    assert verdict(dict(base, banned_layer_tracks=5, banned_layer_tracks_delta=0)) == 'PASS'
    # the run ADDED banned-layer tracks -> DRIFT
    assert verdict(dict(base, banned_layer_tracks=5, banned_layer_tracks_delta=2)) == 'DRIFT'
    # legacy record without the delta: raw count still gates (fallback)
    assert verdict(dict(base, banned_layer_tracks=5)) == 'DRIFT'
    assert verdict(dict(base, banned_layer_tracks=0)) == 'PASS'

if __name__ == '__main__':
    test_server_migrates_old_default_work()
    test_client_migrates_old_default_work()
    test_selftest_verdict_uses_banned_delta()
    print('work migration OK')
