"""Assemble dataset-v3 from the local data volume: join filter results
(verdicts + baselines) with harvest candidates (provenance), emit
human_baselines.json + split.json (repo-grouped, stratified 65/35) +
manifest.json into the repo's dataset-v3/. Idempotent; re-run as more
boards are filtered."""
import collections, json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
VOL = os.path.expanduser('~/Documents/workspace/agent-runs/dataset-v3-boards')

def main():
    cands = {c['board_id']: c for c in json.load(open(os.path.join(VOL, 'candidates.json')))['candidates']}
    accepted = []
    for f in os.listdir(os.path.join(VOL, 'results')):
        if not f.endswith('.json'): continue
        r = json.load(open(os.path.join(VOL, 'results', f)))
        if r.get('verdict') != 'accept' or 'baseline' not in r: continue
        c = cands.get(r['board_id'])
        if c is None: continue
        b = r['baseline']
        accepted.append({'board_id': r['board_id'], 'repo': c['repo'], 'description': c['description'],
                         'components_used': c['components_used'], 'stratum': c['stratum'],
                         'copper_layers': b['copper_layers'], 'pour_nets': b['pour_nets'],
                         'footprints': b['footprints'], 'pad_count': b['pad_count'], 'net_count': b['net_count'],
                         'min_pad_pitch_mm': b['min_pad_pitch_mm'], 'has_footprint_keepouts': b['has_footprint_keepouts'],
                         'crossings': b['crossings'], 'rudy_peak': b['rudy_peak'], 'rudy_p95': b['rudy_p95'],
                         'human_tracks': b['human_tracks'], 'human_vias': b['human_vias'],
                         'human_wirelength_mm': b['human_wirelength_mm'],
                         'human_min_track_w_mm': b['human_min_track_w_mm'], 'human_min_via_d_mm': b['human_min_via_d_mm'],
                         'human_min_via_drill_mm': b['human_min_via_drill_mm'],
                         'input_classes': r.get('input_classes', {}), 'nets': b['nets']})
    accepted.sort(key=lambda a: a['board_id'])
    # repo-grouped stratified split, seed fixed for reproducibility
    rng = random.Random(20261122)
    train, held = [], []
    for stratum in ('A', 'B', 'C'):
        by_repo = collections.defaultdict(list)
        for a in accepted:
            if a['stratum'] == stratum: by_repo[a['repo']].append(a['board_id'])
        repos = sorted(by_repo); rng.shuffle(repos)
        target = round(0.35 * sum(len(v) for v in by_repo.values()))
        n = 0
        for repo in repos:
            if n < target: held.extend(by_repo[repo]); n += len(by_repo[repo])
            else: train.extend(by_repo[repo])
    json.dump(accepted, open(os.path.join(HERE, 'human_baselines.json'), 'w'), indent=1)
    json.dump({'train': sorted(train), 'held_out': sorted(held)}, open(os.path.join(HERE, 'split.json'), 'w'), indent=1)
    counts = collections.Counter(a['stratum'] for a in accepted)
    manifest = {'dataset': 'dataset-v3', 'source': 'bshada/open-schematics (Hugging Face, CC-BY-4.0), streaming harvest',
                'accept_rule': 'ADJUSTED (owner ruling 2026-11-22): input_unconnected == 0 AND shorting_items == 0 AND tracks_crossing == 0. Deviation from the mission text (copper_violations == 0): boards arrive without .kicad_pro, so netclass/thermal context is lost and clearance/thermal/mask counts measure context loss, not routability; shorts and crossings are version-invariant.',
                'accepted': len(accepted), 'per_stratum': dict(counts),
                'train': len(train), 'held_out': len(held),
                'split': 'repo-grouped stratified 65/35, seed 20261122, zero repo overlap'}
    json.dump(manifest, open(os.path.join(HERE, 'manifest.json'), 'w'), indent=1)
    print(json.dumps(manifest, indent=1))

if __name__ == '__main__':
    main()
