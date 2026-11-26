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

"""Run the plugin AUTO pipeline on one board in an isolated process.
Usage: python3 bench_one.py <board.kicad_pcb> <banned_layers_csv> <max_passes>
Prints one RESULT json line."""
import sys, os, json, time
sys.path.insert(0, os.path.expanduser('~/.local/share/kicad/10.0/scripting/plugins'))
from ai_router.config import load
from ai_router.pipeline import Pipeline
from ai_router import orchestrator
board, banned, max_passes = sys.argv[1], sys.argv[2], int(sys.argv[3])
cfg = load()
cfg['mode'] = 'auto'
cfg['max_passes'] = max_passes
cfg['no_tracks_on'] = banned
cfg['strip_existing'] = True
def log(m):
    print('[bench] ' + str(m), flush=True)
t0 = time.time()
try:
    pipe = Pipeline(cfg, board, log)
    res = orchestrator.run_auto(cfg, pipe, log)
    g = res.get('gate') or {}
    print('RESULT ' + json.dumps({'status': 'ok', 'seconds': round(time.time() - t0, 1),
        'unconnected_total': g.get('unconnected_total'), 'pour_items': g.get('pour_items'),
        'split_nets': g.get('split_nets'), 'violations_total': g.get('violations_total'),
        'workdir': pipe.workdir}), flush=True)
except Exception as e:
    print('RESULT ' + json.dumps({'status': 'error', 'seconds': round(time.time() - t0, 1), 'error': str(e)[:300]}), flush=True)
