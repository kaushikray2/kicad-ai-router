"""HTTP client for the AI Router Docker backend (stdlib only)."""
import base64, json, os, urllib.request

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
    pro = os.path.splitext(board_path)[0] + '.kicad_pro'
    if os.path.exists(pro):
        payload['pro_b64'] = base64.b64encode(open(pro, 'rb').read()).decode()
    dru = os.path.splitext(board_path)[0] + '.kicad_dru'
    if os.path.exists(dru):
        payload['dru_b64'] = base64.b64encode(open(dru, 'rb').read()).decode()
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
