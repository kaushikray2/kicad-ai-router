import base64, json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
ROOT = os.path.expanduser('~/Documents/workspace/agent-runs/pcbworld-data/pcbench/exacad_sorted_sub')
BASE = 'http://127.0.0.1:8731'
tok = json.load(open(os.path.join(PLUGIN, 'data', 'config', 'config.json')))['auth_token']
split = json.load(open(os.path.join(PLUGIN, 'dataset-v1', 'split.json')))
feats = {}
for r in json.load(open(os.path.join(PLUGIN, 'dataset-v1', 'dataset.json'))):
    feats.setdefault(r['board'], r['features'])
manifest_path = os.path.join(HERE, 'manifest.json')
manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else []
done_keys = {(m['board'], m['config']) for m in manifest}

def submit(board_path, pro_path, name, overrides):
    body = {'board_b64': base64.b64encode(open(board_path, 'rb').read()).decode(),
            'board_name': name, 'overrides': overrides}
    if pro_path:
        body['pro_b64'] = base64.b64encode(open(pro_path, 'rb').read()).decode()
    req = urllib.request.Request(BASE + '/api/runs', data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=120).read())

CONFIGS = []
for ordering in ('auto', 'none', 'most_pads'):
    for passes in (4, 2):
        CONFIGS.append(('', {'first_nets_policy': ordering, 'max_passes': passes}, '%s-p%d' % (ordering, passes)))
LAWS = [('A', {}), ('B', {'no_tracks_on': 'In1.Cu,In2.Cu'})]

total = 0
for dname in split['train']:
    bp = os.path.join(ROOT, dname, 'processed_v9.kicad_pcb')
    pro = os.path.join(ROOT, dname, 'processed_v9.kicad_pro')
    pro = pro if os.path.exists(pro) else None
    nlayers = len(feats.get(dname, {}).get('copper_layers') or [])
    laws = LAWS if nlayers >= 4 else [LAWS[0]]
    for law_name, law_ov in laws:
        for _tag, cfg_ov, cfg_name in CONFIGS:
            cname = '%s-%s' % (law_name, cfg_name) if nlayers >= 4 else cfg_name
            if (dname, cname) in done_keys: continue
            ov = dict(law_ov); ov.update(cfg_ov)
            r = submit(bp, pro, dname + '.kicad_pcb', ov)
            manifest.append({'board': dname, 'config': cname, 'overrides': ov,
                             'job_id': r['job_id'], 'duplicate': r.get('duplicate'),
                             'copper_layers': nlayers})
            json.dump(manifest, open(manifest_path, 'w'), indent=1)
            total += 1
            print(dname[:36], cname, r['job_id'][:8], 'dup' if r.get('duplicate') else 'new', flush=True)
print('submitted this run:', total, '| manifest total:', len(manifest), flush=True)
