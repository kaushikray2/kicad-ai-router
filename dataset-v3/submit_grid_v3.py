"""Step 2 baseline grid for dataset-v3: train split only, ordering
{auto, most_pads, none} x max_passes 4, layer laws A/B on >=4L boards.
Boards whose human floor is tighter than KiCad defaults (rule drift)
get a synthesized minimal .kicad_pro at the human's observed
track/via/clearance floor (the drc_fix trick), recorded per job."""
import base64, json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
VOL = os.environ.get('DSV3_OUT', os.path.expanduser('~/Documents/workspace/agent-runs/dataset-v3-boards'))
BASE = 'http://127.0.0.1:8731'
tok = json.load(open(os.path.join(PLUGIN, 'data', 'config', 'config.json')))['auth_token']
baselines = {b['board_id']: b for b in json.load(open(os.path.join(HERE, 'human_baselines.json')))}
split = json.load(open(os.path.join(HERE, 'split.json')))
manifest_path = os.path.join(HERE, 'grid_manifest.json')
manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else []
done_keys = {(m['board'], m['config']) for m in manifest}

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

def drift(b):
    return ((b['human_min_track_w_mm'] or 9) < 0.199 or (b['human_min_via_d_mm'] or 9) < 0.59
            or (b.get('input_classes') or {}).get('clearance', 0) > 0)

def submit(board_path, pro_bytes, name, overrides):
    body = {'board_b64': base64.b64encode(open(board_path, 'rb').read()).decode(),
            'board_name': name, 'overrides': overrides}
    if pro_bytes: body['pro_b64'] = base64.b64encode(pro_bytes).decode()
    req = urllib.request.Request(BASE + '/api/runs', data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=120).read())

LAWS = [('A', {}), ('B', {'no_tracks_on': 'In1.Cu,In2.Cu'})]
total = 0
for bid in split['train']:
    b = baselines[bid]
    bp = os.path.join(VOL, b['stratum'], bid + '.kicad_pcb')
    pro = synth_pro(b) if drift(b) else None
    nlayers = len(b['copper_layers'] or [])
    laws = LAWS if nlayers >= 4 else [LAWS[0]]
    for law_name, law_ov in laws:
        for ordering in ('auto', 'most_pads', 'none'):
            cname = ('%s-%s-p4' % (law_name, ordering)) if nlayers >= 4 else ('%s-p4' % ordering)
            if (bid, cname) in done_keys: continue
            ov = dict(law_ov); ov.update({'first_nets_policy': ordering, 'max_passes': 4})
            r = submit(bp, pro, bid + '.kicad_pcb', ov)
            manifest.append({'board': bid, 'config': cname, 'overrides': ov, 'job_id': r['job_id'],
                             'duplicate': r.get('duplicate'), 'copper_layers': nlayers,
                             'stratum': b['stratum'], 'pro_synthesized': bool(pro)})
            json.dump(manifest, open(manifest_path, 'w'), indent=1)
            total += 1
            print(bid[:40], cname, r['job_id'][:8], 'dup' if r.get('duplicate') else 'new', flush=True)
print('submitted this run:', total, '| manifest total:', len(manifest), flush=True)
