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

import base64, json, os, urllib.request, urllib.error
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.expanduser('~/Documents/workspace/agent-runs/pcbworld-data/pcbench/exacad_sorted_sub')
BASE = 'http://127.0.0.1:8731'
tok = json.load(open(os.path.join(HERE, '..', 'data', 'config', 'config.json')))['auth_token']
mp = os.path.join(HERE, 'manifest.json')
man = json.load(open(mp))

def status(jid):
    req = urllib.request.Request(BASE + '/api/runs/' + jid, headers={'Authorization': 'Bearer ' + tok})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read()).get('status')
    except urllib.error.HTTPError as e:
        return 'http%d' % e.code

fixed = 0
for m in man:
    if status(m['job_id']) != 'http404':
        continue
    bp = os.path.join(ROOT, m['board'], 'processed_v9.kicad_pcb')
    pro = bp[:-len('.kicad_pcb')] + '.kicad_pro'
    body = {'board_b64': base64.b64encode(open(bp, 'rb').read()).decode(),
            'board_name': m['board'] + '.kicad_pcb',
            'overrides': {} if m['variant'].startswith('A') else {'no_tracks_on': 'In1.Cu,In2.Cu'}}
    if os.path.exists(pro):
        body['pro_b64'] = base64.b64encode(open(pro, 'rb').read()).decode()
    req = urllib.request.Request(BASE + '/api/runs', data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method='POST')
    r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    m['job_id_prev_pruned'] = m['job_id']
    m['job_id'] = r['job_id']
    fixed += 1
    print('resubmitted', m['board'], m['variant'], '->', r['job_id'], flush=True)
json.dump(man, open(mp, 'w'), indent=1)
print('resubmitted:', fixed)
