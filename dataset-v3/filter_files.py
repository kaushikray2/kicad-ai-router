"""Per-file driver around filter_and_baseline: DRC-filter + baseline for
explicit board files, one result JSON per board in /boards/results/."""
import json, os, subprocess, sys
sys.path.insert(0, '/work/dataset-v3'); sys.path.insert(0, '/work')
import filter_and_baseline as fb

def main():
    os.makedirs('/boards/results', exist_ok=True)
    for board in sys.argv[1:]:
        bid = os.path.basename(board)[:-len('.kicad_pcb')]
        out = '/boards/results/%s.json' % bid
        if os.path.exists(out):
            prev = json.load(open(out))
            if 'baseline' in prev and prev['baseline'].get('human_min_track_w_mm') is not None: continue
        import collections as _c
        rpt = board + '.drc.json'
        try:
            if os.path.exists(rpt):
                d = json.load(open(rpt))
                unc = len(d.get('unconnected_items', []))
                classes = dict(_c.Counter(v['type'] for v in d.get('violations', [])))
                copper = sum(n for k, n in classes.items() if k in fb.COPPER_CLASSES)
            else:
                unc, copper = fb.drc(board)
                d = json.load(open(rpt)); classes = dict(_c.Counter(v['type'] for v in d.get('violations', [])))
        except Exception as e:
            json.dump({'board_id': bid, 'verdict': 'drc_error', 'error': str(e)[:120]}, open(out, 'w')); continue
        shorts = classes.get('shorting_items', 0); crossings = classes.get('tracks_crossing', 0)
        ok = (unc == 0 and shorts == 0 and crossings == 0)   # owner-adjusted rule, 2026-11-22
        rec = {'board_id': bid, 'verdict': 'accept' if ok else 'reject', 'rule': 'unc==0 & shorts==0 & crossings==0',
               'input_unconnected': unc, 'input_copper': copper, 'input_shorts': shorts,
               'input_tracks_crossing': crossings, 'input_classes': classes}
        if ok:
            r = subprocess.run(['python3', '/work/ai_router/worker.py', 'summary', '--board', board,
                                '--result', '/tmp/s.json'], capture_output=True, timeout=600)
            s = json.load(open('/tmp/s.json')) if os.path.exists('/tmp/s.json') else {}
            per_net, pad_total, min_pitch, fp_keepout, mins = fb.baseline(board, {})
            rec['baseline'] = {'copper_layers': s.get('copper_layers'), 'pour_nets': s.get('pour_nets', []),
                               'footprints': s.get('footprints'), 'pad_count': pad_total, 'net_count': len(per_net),
                               'min_pad_pitch_mm': min_pitch, 'has_footprint_keepouts': fp_keepout,
                               'crossings': s.get('crossings'), 'rudy_peak': s.get('rudy_peak'), 'rudy_p95': s.get('rudy_p95'),
                               'human_tracks': s.get('tracks'), 'human_vias': s.get('vias'), 'human_min_track_w_mm': mins['track_w'], 'human_min_via_d_mm': mins['via_d'], 'human_min_via_drill_mm': mins['via_drill'], 'tracks_by_layer': s.get('tracks_by_layer'),
                               'human_wirelength_mm': s.get('wirelength_mm'), 'nets': per_net}
        json.dump(rec, open(out, 'w'))
        print(bid, rec['verdict'], flush=True)

if __name__ == '__main__':
    main()
