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

"""Stage 2 of dataset-v3: KiCad 10 ground-truth filter + human baseline
extraction. Runs inside the ai-router-backend container (pcbnew +
kicad-cli + the plugin tree mounted at /work).
Accept rule (mission): input_unconnected == 0 AND input copper
violations == 0 (pipeline.COPPER_CLASSES) on the ORIGINAL human board,
after refill. For accepted boards, emit the human baseline record."""
import glob, json, os, subprocess, sys
sys.path.insert(0, '/work')
import pcbnew
from ai_router.pipeline import COPPER_CLASSES

BOARDS = '/boards'
OUT = '/boards'

def drc(board):
    rpt = board + '.drc.json'
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--refill-zones', '--format', 'json',
                    '--output', rpt, board], capture_output=True, timeout=600)
    d = json.load(open(rpt))
    unc = len(d.get('unconnected_items', []))
    copper = sum(1 for v in d.get('violations', []) if v['type'] in COPPER_CLASSES)
    return unc, copper

def nm(v): return v / 1e6

def baseline(board, cand):
    b = pcbnew.LoadBoard(board)
    nets = {}
    _mins = {'track_w': None, 'via_d': None, 'via_drill': None}
    for t in b.GetTracks():
        n = t.GetNetname()
        if not n: continue
        e = nets.setdefault(n, {'wl': 0.0, 'vias': 0, 'widths': set(), 'layers': set(), 'pads': []})
        if t.Type() == pcbnew.PCB_VIA_T:
            e['vias'] += 1
            vd = nm(t.GetWidth(pcbnew.F_Cu)); vdr = nm(t.GetDrillValue())
            if _mins['via_d'] is None or vd < _mins['via_d']: _mins['via_d'] = vd
            if _mins['via_drill'] is None or vdr < _mins['via_drill']: _mins['via_drill'] = vdr
        else:
            e['wl'] += nm(t.GetLength()); e['widths'].add(round(nm(t.GetWidth()), 3))
            if _mins['track_w'] is None or nm(t.GetWidth()) < _mins['track_w']: _mins['track_w'] = nm(t.GetWidth())
            e['layers'].add(b.GetLayerName(t.GetLayer()))
    min_pitch = None; fp_keepout = False; pad_total = 0
    for fp in b.GetFootprints():
        if len(list(fp.Zones())): fp_keepout = True
        pts = []
        for p in fp.Pads():
            pos = p.GetPosition(); pts.append((nm(pos.x), nm(pos.y)))
            pad_total += 1
            n = p.GetNetname()
            if n in nets: nets[n]['pads'].append((nm(pos.x), nm(pos.y)))
            elif n: nets.setdefault(n, {'wl': 0.0, 'vias': 0, 'widths': set(), 'layers': set(), 'pads': [(nm(pos.x), nm(pos.y))]})
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                d = ((pts[i][0]-pts[j][0])**2 + (pts[i][1]-pts[j][1])**2) ** 0.5
                if d > 0 and (min_pitch is None or d < min_pitch): min_pitch = d
    per_net = []
    for n, e in sorted(nets.items()):
        if not e['pads']: continue
        xs = [p[0] for p in e['pads']]; ys = [p[1] for p in e['pads']]
        per_net.append({'net': n, 'pads': len(e['pads']),
                        'hpwl_mm': round((max(xs)-min(xs)) + (max(ys)-min(ys)), 2),
                        'human_wl_mm': round(e['wl'], 1), 'human_vias': e['vias'],
                        'human_width_mm': max(e['widths']) if e['widths'] else 0,
                        'human_layers_used': sorted(e['layers'])})
    return per_net, pad_total, (round(min_pitch, 3) if min_pitch else None), fp_keepout, {k: (round(v, 3) if v else None) for k, v in _mins.items()}

def main():
    meta = json.load(open(os.path.join(OUT, 'candidates.json')))
    summ = {}
    for f in glob.glob('/tmp/summaries/*.json'):
        summ[os.path.basename(f)[:-5]] = json.load(open(f))
    accepted, log = [], []
    for cand in meta['candidates']:
        bid = cand['board_id']; board = os.path.join(BOARDS, cand['stratum'], bid + '.kicad_pcb')
        try:
            unc, copper = drc(board)
        except Exception as e:
            log.append({'board_id': bid, 'verdict': 'drc_error', 'error': str(e)[:120]}); continue
        ok = (unc == 0 and copper == 0)
        log.append({'board_id': bid, 'verdict': 'accept' if ok else 'reject', 'input_unconnected': unc, 'input_copper': copper})
        if not ok: continue
        s = summ.get(bid, {})
        per_net, pad_total, min_pitch, fp_keepout, mins = baseline(board, cand)
        accepted.append({'board_id': bid, 'repo': cand['repo'], 'description': cand['description'],
                         'components_used': cand['components_used'], 'stratum': cand['stratum'],
                         'copper_layers': s.get('copper_layers', cand['copper_layers']),
                         'pour_nets': s.get('pour_nets', []), 'footprints': s.get('footprints'),
                         'pad_count': pad_total, 'net_count': len(per_net),
                         'min_pad_pitch_mm': min_pitch, 'has_footprint_keepouts': fp_keepout,
                         'crossings': s.get('crossings'), 'rudy_peak': s.get('rudy_peak'), 'rudy_p95': s.get('rudy_p95'),
                         'human_tracks': s.get('tracks'), 'human_vias': s.get('vias'), 'human_min_track_w_mm': mins['track_w'], 'human_min_via_d_mm': mins['via_d'], 'human_min_via_drill_mm': mins['via_drill'],
                         'human_wirelength_mm': s.get('wirelength_mm'), 'nets': per_net})
        print('accept', bid, flush=True)
    json.dump(accepted, open(os.path.join(OUT, 'human_baselines.json'), 'w'), indent=1)
    json.dump(log, open(os.path.join(OUT, 'filter_log.json'), 'w'), indent=1)
    print('DONE accepted', len(accepted), 'of', len(meta['candidates']))

if __name__ == '__main__':
    main()
