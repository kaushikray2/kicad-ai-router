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

"""Harvest hard multi-layer candidates for dataset-v3 from
bshada/open-schematics (HF, CC-BY-4.0), streaming mode.
Pre-filter is pure text analysis -- no KiCad. Accepted candidates are
written to the local data volume (never committed); candidates.json
records provenance + hashes for the KiCad ground-truth filter stage.
"""
import hashlib, json, os, re, sys
from datasets import load_dataset

OUT = os.environ.get('DSV3_OUT', os.path.expanduser('~/Documents/workspace/agent-runs/dataset-v3-boards'))
CAPS = {'A': 120, 'B': 70, 'C': 90}          # candidate caps (pre-DRC)
PER_REPO = 2
CU_RE = re.compile(r'\(\d+ "([^"]+\.Cu)"')
NET_RE = re.compile(r'\(net \d+ "([^"]*)"')
SEG_RE = re.compile(r'\(start ([\d.\-]+) ([\d.\-]+)\)\s*\(end ([\d.\-]+) ([\d.\-]+)\)')

def analyse(text):
    head = text[:6000]
    cu = sorted(set(CU_RE.findall(head)))
    pads = text.count('(pad ')
    fps = text.count('(footprint ')
    zone = '(zone ' in text
    routed = '(segment ' in text and '(via ' in text
    return cu, pads, fps, zone, routed

def stratum(cu_n, pads, zone):
    if cu_n == 4 and zone and 60 <= pads <= 500: return 'A'
    if cu_n in (6, 8): return 'B'
    if cu_n == 2 and pads >= 150: return 'C'
    return None

def geo_hash(text, nets):
    pts = sorted('%s,%s,%s,%s' % (a, b, c, d) for a, b, c, d in SEG_RE.findall(text[:200000]))
    h = hashlib.sha256()
    h.update('|'.join(sorted(nets)).encode()); h.update(str(len(pts)).encode())
    for p in pts[::7]: h.update(p.encode())   # sample for speed, stable for forks
    return h.hexdigest()[:16]

def main():
    ds = load_dataset('bshada/open-schematics', streaming=True, split='train')
    ds = ds.select_columns(['name', 'description', 'extensions_used', 'pcb_files', 'components_used'])
    counts = {'A': 0, 'B': 0, 'C': 0}; per_repo = {}; seen_geo = set(); seen_exact = set()
    cands = []; scanned = 0
    for rec in ds:
        scanned += 1
        if all(counts[s] >= CAPS[s] for s in CAPS): break
        if '.kicad_pcb' not in (rec['extensions_used'] or []): continue
        repo = rec['name'] or 'unknown'
        for bi, text in enumerate(rec['pcb_files'] or []):
            if per_repo.get(repo, 0) >= PER_REPO: break
            if not text or '(segment ' not in text or '(via ' not in text: continue
            cu, pads, fps, zone, routed = analyse(text)
            st = stratum(len(cu), pads, zone)
            if st is None or counts[st] >= CAPS[st]: continue
            ex = hashlib.sha256(text.encode()).hexdigest()
            if ex in seen_exact: continue
            nets = NET_RE.findall(text[:20000])
            gh = geo_hash(text, nets)
            if gh in seen_geo: continue
            seen_exact.add(ex); seen_geo.add(gh); per_repo[repo] = per_repo.get(repo, 0) + 1
            bid = re.sub(r'[^A-Za-z0-9_.-]+', '_', repo) + '__%d' % bi
            d = os.path.join(OUT, st); os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, bid + '.kicad_pcb')
            if not os.path.exists(fp):
                with open(fp, 'w') as f: f.write(text)
            cands.append({'board_id': bid, 'repo': repo, 'description': rec['description'],
                          'components_used': rec['components_used'], 'stratum': st,
                          'copper_layers': cu, 'pad_count': pads, 'footprints': fps,
                          'has_zone': zone, 'sha256': ex, 'geo_hash': gh})
            counts[st] += 1
        if scanned % 500 == 0:
            json.dump({'scanned_records': scanned, 'counts': counts, 'candidates': cands},
                      open(os.path.join(OUT, 'candidates.json'), 'w'), indent=1)
            print('scanned', scanned, 'counts', counts, flush=True)
    json.dump({'scanned_records': scanned, 'counts': counts, 'candidates': cands},
              open(os.path.join(OUT, 'candidates.json'), 'w'), indent=1)
    print('DONE scanned', scanned, 'counts', counts, flush=True)

if __name__ == '__main__':
    main()
