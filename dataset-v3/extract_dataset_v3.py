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

"""Extract dataset-v3 results from backend run dirs (run on the backend host).
Reads grid_manifest.json + human_baselines.json + inputs.json from its
own dir; writes dataset.json, gap.json, repair_fixtures.json there and
archives 1-25-stranded best boards under the backend data volume."""
import json, os, random, shutil, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = 'data/runs'
FIXDIR = 'data/fixtures-v3'
manifest = json.load(open(os.path.join(HERE, 'grid_manifest.json')))
baselines = {b['board_id']: b for b in json.load(open(os.path.join(HERE, 'human_baselines.json')))}
inputs = json.load(open(os.path.join(HERE, 'inputs.json')))

rows = []
for e in manifest:
    row = {'board': e['board'], 'config': e['config'], 'stratum': e['stratum'],
           'copper_layers': e['copper_layers'], 'job_id': e['job_id'], 'pro_synthesized': e['pro_synthesized']}
    sp = os.path.join(RUNS, e['job_id'], 'status.json')
    try:
        s = json.load(open(sp))
    except Exception:
        row['status'] = 'missing'; rows.append(row); continue
    row['status'] = s.get('status')
    g = s.get('gate') or {}
    if row['status'] == 'done' and g:
        row.update({'unconnected': g.get('unconnected_total'), 'violations': g.get('violations_total'),
                    'copper_violations': g.get('copper_violations'), 'banned_layer_tracks': g.get('banned_layer_tracks'),
                    'vias': g.get('vias'), 'wirelength_mm': g.get('wirelength_mm'),
                    'split_signal_nets': len(g.get('split_signal_nets') or []), 'split_pour_nets': len(g.get('split_pour_nets') or [])})
        hb = baselines[e['board']]
        if hb['human_vias']: row['vias_ratio'] = round(g['vias'] / hb['human_vias'], 3) if g.get('vias') is not None else None
        if hb['human_wirelength_mm']: row['wl_ratio'] = round(g['wirelength_mm'] / hb['human_wirelength_mm'], 3) if g.get('wirelength_mm') else None
        ic = inputs.get(e['board'], {}).get('input_copper')
        row['copper_delta'] = (g['copper_violations'] - ic) if (ic is not None and g.get('copper_violations') is not None) else None
        row['clean'] = bool(row['unconnected'] == 0 and (row['copper_delta'] is not None and row['copper_delta'] <= 0))
    rows.append(row)
json.dump(rows, open(os.path.join(HERE, 'dataset.json'), 'w'), indent=1)

done = [r for r in rows if r['status'] == 'done' and 'unconnected' in r]
def board_map(rs):
    m = {}
    for r in rs: m.setdefault(r['board'], {})[r['config']] = r
    return m

def gap_block(rs, label):
    bm = board_map(rs)
    cfgs = sorted({r['config'] for r in rs})
    full = {b: v for b, v in bm.items() if len(v) == len(cfgs)}
    if not full: return {'label': label, 'complete_boards': 0}
    totals = {c: sum(v[c]['unconnected'] for v in full.values()) for c in cfgs}
    sbs = min(totals, key=totals.get)
    diffs = [v[sbs]['unconnected'] - min(x['unconnected'] for x in v.values()) for v in full.values()]
    blk = {'label': label, 'complete_boards': len(full), 'per_config_totals': totals,
           'sbs_config': sbs, 'sbs_total': totals[sbs], 'vbs_total': sum(min(x['unconnected'] for x in v.values()) for v in full.values()),
           'gap': sum(diffs), 'clean_rate_per_config': {c: round(sum(1 for v in full.values() if v[c]['clean']) / len(full), 3) for c in cfgs}}
    if len(full) >= 5:
        rng = random.Random(20261122)
        means = []
        for _ in range(10000):
            sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
            means.append(sum(sample) / len(sample))
        means.sort()
        blk['gap_mean_per_board'] = round(sum(diffs) / len(diffs), 3)
        blk['gap_mean_ci95'] = [round(means[250], 3), round(means[9750], 3)]
    return blk

groups = {'stratum_A': [r for r in done if r['stratum'] == 'A'], 'stratum_B': [r for r in done if r['stratum'] == 'B'],
          'stratum_C': [r for r in done if r['stratum'] == 'C'], 'multilayer_ge4': [r for r in done if r['copper_layers'] >= 4],
          'two_layer': [r for r in done if r['copper_layers'] == 2]}
gap = {'jobs_total': len(rows), 'jobs_done': len(done), 'partial': len(done) < len(rows),
       'clean_definition': 'unconnected == 0 AND copper_delta <= 0 (output copper violations vs the human board input count, same COPPER_CLASSES, both kicad-cli DRC with refill)',
       'groups': {k: gap_block(v, k) for k, v in groups.items()},
       'qor_median_per_config': {}}
for c in sorted({r['config'] for r in done}):
    rs = [r for r in done if r['config'] == c]
    rc = [r for r in rs if r['clean']]
    vr = [r['vias_ratio'] for r in rc if r.get('vias_ratio')]; wr = [r['wl_ratio'] for r in rc if r.get('wl_ratio')]
    va = [r['vias_ratio'] for r in rs if r.get('vias_ratio')]; wa = [r['wl_ratio'] for r in rs if r.get('wl_ratio')]
    gap['qor_median_per_config'][c] = {'n_done': len(rs), 'n_clean': len(rc),
                                       'vias_over_human_median_clean': round(statistics.median(vr), 3) if vr else None,
                                       'wirelength_over_human_median_clean': round(statistics.median(wr), 3) if wr else None,
                                       'vias_over_human_median_all': round(statistics.median(va), 3) if va else None,
                                       'wirelength_over_human_median_all': round(statistics.median(wa), 3) if wa else None}
json.dump(gap, open(os.path.join(HERE, 'gap.json'), 'w'), indent=1)

os.makedirs(FIXDIR, exist_ok=True)
fixtures = []
for b, v in board_map(done).items():
    best = min(v.values(), key=lambda r: (r['unconnected'], r['copper_violations'] if r['copper_violations'] is not None else 999))
    if 1 <= best['unconnected'] <= 25:
        dst = os.path.join(FIXDIR, '%s__%s.kicad_pcb' % (b, best['config']))
        src = os.path.join(RUNS, best['job_id'], 'result.kicad_pcb')
        if os.path.exists(src): shutil.copyfile(src, dst)
        fixtures.append({'board': b, 'stratum': best['stratum'], 'best_config': best['config'], 'job_id': best['job_id'],
                         'unconnected': best['unconnected'], 'copper_violations': best['copper_violations'],
                         'archived_board': dst, 'human_board': 'dataset-v3-boards/%s/%s.kicad_pcb' % (best['stratum'], b)})
fixtures.sort(key=lambda f: f['unconnected'])
json.dump(fixtures, open(os.path.join(HERE, 'repair_fixtures.json'), 'w'), indent=1)
print('rows:', len(rows), 'done:', len(done), 'fixtures:', len(fixtures))
print(json.dumps({k: (v.get('gap'), v.get('complete_boards')) for k, v in gap['groups'].items()}, indent=1))
