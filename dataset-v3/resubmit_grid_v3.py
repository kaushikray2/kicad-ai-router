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

"""Resubmit dataset-v3 grid jobs lost to the 2026-11-22 backend restart
(16-worker raise). Snapshot-driven: only jobs whose pre-restart disk
status was not 'done' are resubmitted; manifest job_ids are updated in
place (prev_job_id kept)."""
import base64, json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
VOL = os.environ.get('DSV3_OUT', os.path.expanduser('~/Documents/workspace/agent-runs/dataset-v3-boards'))
BASE = 'http://127.0.0.1:8731'
tok = json.load(open(os.path.join(PLUGIN, 'data', 'config', 'config.json')))['auth_token']
baselines = {b['board_id']: b for b in json.load(open(os.path.join(HERE, 'human_baselines.json')))}
manifest = json.load(open(os.path.join(HERE, 'grid_manifest.json')))
snap = json.load(open('/work/dataset-v3/snapshot-pre16.json')) if os.path.exists('/work/dataset-v3/snapshot-pre16.json') else json.load(open(os.path.expanduser('/tmp/v132/snapshot-pre16.json')))

def synth_pro(b):
    tw = b['human_min_track_w_mm'] or 0.2; vd = b['human_min_via_d_mm'] or 0.6; vdr = b['human_min_via_drill_mm'] or 0.3
    clr = round(min(0.20, max(0.10, tw)), 3)
    cls = {'bus_width': 12, 'clearance': clr, 'diff_pair_gap': clr, 'diff_pair_via_gap': 0.25,
           'diff_pair_width': tw, 'line_style': 0, 'microvia_diameter': vd, 'microvia_drill': vdr,
           'name': 'Default', 'pcb_color': 'rgba(0, 0, 0, 0.000)', 'priority': -1,
           'schematic_color': 'rgba(0, 0, 0, 0.000)', 'track_width': tw, 'tuning_profile': '',
           'via_diameter': vd, 'via_drill': vdr, 'wire_width': 6}
    return json.dumps({'meta': {'filename': 'synth.kicad_pro', 'version': 3},
                       'net_settings': {'classes': [cls], 'meta': {'version': 3}, 'net_colors': {},
                                        'netclass_assignments': {}, 'netclass_patterns': []}}).encode()

n = 0
for e in manifest:
    if snap.get(e['job_id']) == 'done': continue
    b = baselines[e['board']]
    bp = os.path.join(VOL, e['stratum'], e['board'] + '.kicad_pcb')
    body = {'board_b64': base64.b64encode(open(bp, 'rb').read()).decode(),
            'board_name': e['board'] + '.kicad_pcb', 'overrides': e['overrides']}
    if e['pro_synthesized']: body['pro_b64'] = base64.b64encode(synth_pro(b)).decode()
    req = urllib.request.Request(BASE + '/api/runs', data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method='POST')
    r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    e['prev_job_id'] = e['job_id']; e['job_id'] = r['job_id']
    json.dump(manifest, open(os.path.join(HERE, 'grid_manifest.json'), 'w'), indent=1)
    n += 1
    print(e['board'][:36], e['config'], r['job_id'][:8], flush=True)
print('resubmitted:', n, flush=True)
