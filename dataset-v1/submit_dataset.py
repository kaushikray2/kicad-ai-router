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

import base64, glob, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.expanduser('~/Documents/workspace/agent-runs/pcbworld-data/pcbench/exacad_sorted_sub')
BASE = 'http://127.0.0.1:8731'
tok = json.load(open(os.path.join(HERE, '..', 'data', 'config', 'config.json')))['auth_token']
manifest_path = os.path.join(HERE, 'manifest.json')
manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else []
done_keys = {(m['board'], m['variant']) for m in manifest}

def submit(board_path, pro_path, name, overrides):
    body = {'board_b64': base64.b64encode(open(board_path, 'rb').read()).decode(),
            'board_name': name, 'overrides': overrides}
    if pro_path:
        body['pro_b64'] = base64.b64encode(open(pro_path, 'rb').read()).decode()
    req = urllib.request.Request(BASE + '/api/runs', data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=120).read())

boards = sorted(glob.glob(os.path.join(ROOT, '*', 'processed_v9.kicad_pcb')))
print('corpus boards:', len(boards), flush=True)
for bp in boards:
    dname = os.path.basename(os.path.dirname(bp))
    pro = bp[:-len('.kicad_pcb')] + '.kicad_pro'
    pro = pro if os.path.exists(pro) else None
    text = open(bp, encoding='utf-8', errors='replace').read(200000)
    layers = {m for m in re.findall(r'"([^"]*\.Cu)"', text) if not m.startswith('*')}
    variants = [('A-in2-allowed', {})]
    if len(layers) >= 4:
        variants.append(('B-in2-banned', {'no_tracks_on': 'In1.Cu,In2.Cu'}))
    for vname, ov in variants:
        if (dname, vname) in done_keys:
            continue
        r = submit(bp, pro, dname + '.kicad_pcb', ov)
        manifest.append({'board': dname, 'variant': vname, 'job_id': r['job_id'],
                         'duplicate': r.get('duplicate'), 'copper_layers': sorted(layers)})
        json.dump(manifest, open(manifest_path, 'w'), indent=1)
        print(dname, vname, r['job_id'], 'dup' if r.get('duplicate') else 'new', flush=True)
print('submitted total:', len(manifest), flush=True)
