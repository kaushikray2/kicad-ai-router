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

"""HTTP client for the AI Router Docker backend (stdlib only)."""
import base64, json, os, urllib.request
try:
    from . import naming
except ImportError:
    import naming

def _req(url, path, token='', method='GET', payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url.rstrip('/') + path, data=data, method=method,
                               headers={'Content-Type': 'application/json',
                                        'Authorization': 'Bearer ' + token,
                                        'User-Agent': 'ai-router-plugin/1.0'})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read()

def health(url):
    return json.loads(_req(url, '/health', timeout=30))

def submit(url, token, board_path, overrides):
    payload = {'board_b64': base64.b64encode(open(board_path, 'rb').read()).decode(),
               'board_name': os.path.basename(board_path), 'overrides': overrides or {}}
    pro, dru = naming.sidecar_paths(board_path)  # (#182): project-stem resolution -- a renamed board submits its real netclasses / DRC rules
    if pro:
        payload['pro_b64'] = base64.b64encode(open(pro, 'rb').read()).decode()
    if dru:
        payload['dru_b64'] = base64.b64encode(open(dru, 'rb').read()).decode()
    # v1.11.5 (#150): the markings sidecar travels with the board --
    # without it a backend run silently routes unmarked.
    sidecar = os.path.splitext(board_path)[0] + '.ai-router-markings.json'
    if os.path.exists(sidecar):
        payload['markings_b64'] = base64.b64encode(open(sidecar, 'rb').read()).decode()
    out = json.loads(_req(url, '/api/runs', token, 'POST', payload, timeout=300))
    return out['job_id']

def get_job(url, token, job_id):
    return json.loads(_req(url, '/api/runs/' + job_id, token, timeout=60))

def cancel(url, token, job_id):
    try:
        _req(url, '/api/runs/%s/cancel' % job_id, token, 'POST', {}, timeout=30)
    except Exception:
        pass

def download_sidecar(url, token, job_id, kind, dest):
    # v1.8.2 (#17): best-effort fetch of result.kicad_pro/.dru.
    try:
        data = _req(url, '/api/runs/%s/%s' % (job_id, kind), token, timeout=120)
    except Exception:
        return False
    with open(dest, 'wb') as f:
        f.write(data)
    return True

def download_result(url, token, job_id, dest):
    data = _req(url, '/api/runs/%s/result' % job_id, token, timeout=300)
    with open(dest, 'wb') as f:
        f.write(data)
    return dest
