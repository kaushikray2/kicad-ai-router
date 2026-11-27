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

"""Run comparison and the nothing-worse gate (#62).

compare_runs() produces the A/B table in the shape the engine
adoption A/B established: per board, both stacks' gate triples
{unconnected, splits, copper}, wall times, and routing-caused DRC,
plus aggregates. nothing_worse() is the gate the issue defines: a
candidate run regresses if, on any board, any routing-caused DRC
kind exceeds the baseline run's caused value for that kind, or any
gate-triple component is worse than the baseline's. Estimator drift
is carried in the records and rendered, never gated on.
"""
import json
import os

TRIPLE_FIELDS = ('unconnected', 'splits', 'copper')


def load_run(run_dir):
    """Load a run directory into {'manifest':..., 'records': {slug: rec}}."""
    manifest = None
    mp = os.path.join(run_dir, 'manifest.json')
    if os.path.exists(mp):
        with open(mp) as f:
            manifest = json.load(f)
    records = {}
    bdir = os.path.join(run_dir, 'boards')
    if os.path.isdir(bdir):
        for slug in sorted(os.listdir(bdir)):
            rp = os.path.join(bdir, slug, 'record.json')
            if os.path.exists(rp):
                with open(rp) as f:
                    records[slug] = json.load(f)
    return {'manifest': manifest, 'records': records}


def _triple(rec):
    g = (rec or {}).get('gate') or {}
    return {f: g.get(f) for f in TRIPLE_FIELDS}


def _caused_total(rec):
    inc = [d for d in ((rec or {}).get('caused') or {}).values() if d > 0]
    return sum(inc)


def compare_runs(a, b):
    """A/B comparison of two loaded runs (load_run dicts)."""
    ra, rb = a['records'], b['records']
    rows = []
    for slug in sorted(set(ra) | set(rb)):
        rec_a, rec_b = ra.get(slug), rb.get(slug)
        row = {'slug': slug,
               'a': _triple(rec_a), 'b': _triple(rec_b),
               'caused_a': _caused_total(rec_a),
               'caused_b': _caused_total(rec_b),
               'wall_a': (rec_a or {}).get('wall_s'),
               'wall_b': (rec_b or {}).get('wall_s'),
               'status_a': (rec_a or {}).get('status'),
               'status_b': (rec_b or {}).get('status')}
        ua, ub = row['a']['unconnected'], row['b']['unconnected']
        if ua is None or ub is None:
            row['verdict'] = 'missing'
        elif ub < ua:
            row['verdict'] = 'b_better'
        elif ub > ua:
            row['verdict'] = 'b_worse'
        else:
            row['verdict'] = 'tie'
        rows.append(row)

    def _sum(recs, field):
        return sum((r['gate'].get(field) or 0) for r in recs.values()
                   if r.get('gate'))
    agg = {
        'boards': len(rows),
        'unconnected_a': _sum(ra, 'unconnected'),
        'unconnected_b': _sum(rb, 'unconnected'),
        'splits_a': _sum(ra, 'splits'), 'splits_b': _sum(rb, 'splits'),
        'copper_a': _sum(ra, 'copper'), 'copper_b': _sum(rb, 'copper'),
        'caused_a': sum(_caused_total(r) for r in ra.values()),
        'caused_b': sum(_caused_total(r) for r in rb.values()),
        'b_better': sum(1 for r in rows if r['verdict'] == 'b_better'),
        'b_worse': sum(1 for r in rows if r['verdict'] == 'b_worse'),
        'ties': sum(1 for r in rows if r['verdict'] == 'tie'),
        # #223: incomplete records are counted, never silently
        # dropped from the sums above (a crashed board used to
        # lower the aggregates by vanishing from them).
        'missing_a': sum(1 for r in ra.values()
                         if r.get('status') != 'done'
                         or not r.get('gate')),
        'missing_b': sum(1 for r in rb.values()
                         if r.get('status') != 'done'
                         or not r.get('gate')),
        'error_a': sum(1 for r in ra.values()
                       if r.get('status') == 'error'),
        'error_b': sum(1 for r in rb.values()
                       if r.get('status') == 'error'),
    }
    return {'rows': rows, 'aggregate': agg,
            'a_stack': (a['manifest'] or {}).get('stack'),
            'b_stack': (b['manifest'] or {}).get('stack')}


def _fmt_triple(t):
    if t is None or t.get('unconnected') is None:
        return '-'
    return '%s/%s/%s' % (t['unconnected'], t['splits'], t['copper'])


def render_compare_markdown(comp, title='A/B comparison'):
    lines = ['# %s' % title, '',
             'Gate triple is {unconnected / splits / copper}. Caused = '
             'routing-caused DRC items (KiCad DRC after routing minus '
             'the stripped input, positive part).', '',
             '| Board | A triple | B triple | Caused A | Caused B | '
             'Wall A (s) | Wall B (s) | Verdict |',
             '|---|---|---|---|---|---|---|---|']
    for r in comp['rows']:
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s |' % (
            r['slug'], _fmt_triple(r['a']), _fmt_triple(r['b']),
            r['caused_a'], r['caused_b'],
            r['wall_a'] if r['wall_a'] is not None else '-',
            r['wall_b'] if r['wall_b'] is not None else '-',
            r['verdict']))
    a = comp['aggregate']
    lines += ['', 'Aggregate over %d boards: unconnected A %d vs B %d; '
              'splits A %d vs B %d; copper A %d vs B %d; caused A %d vs '
              'B %d; B better on %d, worse on %d, tied on %d boards '
              '(by unconnected); missing/error records: A %d missing '
              '(%d error), B %d missing (%d error) -- incomplete '
              'boards are excluded from the sums and fail the '
              'nothing-worse gate.' % (a['boards'], a['unconnected_a'],
              a['unconnected_b'], a['splits_a'], a['splits_b'],
              a['copper_a'], a['copper_b'], a['caused_a'], a['caused_b'],
              a['b_better'], a['b_worse'], a['ties'], a['missing_a'],
              a['error_a'], a['missing_b'], a['error_b'])]
    return '\n'.join(lines) + '\n'


def nothing_worse(candidate_records, baseline_records):
    """The nothing-worse gate: candidate vs baseline, per #62.

    A regression is: a board with no baseline record; a routing-caused
    DRC kind whose candidate caused value exceeds the baseline's; or
    a gate-triple component worse than the baseline's. Returns
    {'ok': bool, 'regressions': [...]}.
    """
    regressions = []
    for slug in sorted(candidate_records):
        crec = candidate_records[slug]
        brec = baseline_records.get(slug)
        if brec is None:
            regressions.append({'board': slug, 'kind': 'board',
                                'baseline': None, 'candidate': 'present',
                                'detail': 'no baseline record'})
            continue
        # #223: a record that did not complete a measured run is a
        # regression, never a pass -- a crashed candidate used to
        # sail through here because its missing caused read as {}
        # (all zeros) and its missing gate values skipped every
        # triple check.
        if crec.get('status') != 'done':
            regressions.append({'board': slug, 'kind': 'status',
                                'baseline': brec.get('status'),
                                'candidate': crec.get('status'),
                                'detail': 'candidate run did not '
                                          'complete'})
            continue
        if brec.get('status') != 'done':
            regressions.append({'board': slug, 'kind': 'status',
                                'baseline': brec.get('status'),
                                'candidate': crec.get('status'),
                                'detail': 'baseline run did not '
                                          'complete'})
            continue
        if 'caused' not in crec or 'caused' not in brec:
            regressions.append({'board': slug, 'kind': 'caused',
                                'baseline': 'caused' in brec,
                                'candidate': 'caused' in crec,
                                'detail': 'caused DRC missing -- the '
                                          'board was not measured'})
            continue
        c_caused = crec.get('caused') or {}
        b_caused = brec.get('caused') or {}
        for kind in sorted(set(c_caused) | set(b_caused)):
            cv, bv = c_caused.get(kind, 0), b_caused.get(kind, 0)
            if cv > bv:
                regressions.append({'board': slug, 'kind': kind,
                                    'baseline': bv, 'candidate': cv})
        cg, bg = crec.get('gate') or {}, brec.get('gate') or {}
        for f in TRIPLE_FIELDS:
            cv, bv = cg.get(f), bg.get(f)
            if cv is None or bv is None:
                regressions.append({'board': slug,
                                    'kind': 'gate.' + f,
                                    'baseline': bv, 'candidate': cv,
                                    'detail': 'gate value missing -- '
                                              'the board was not '
                                              'measured'})
            elif cv > bv:
                regressions.append({'board': slug, 'kind': 'gate.' + f,
                                    'baseline': bv, 'candidate': cv})
    return {'ok': not regressions, 'regressions': regressions}


def render_run_summary(records, manifest=None):
    """Human table for one run (summary.md)."""
    lines = ['# Bench run summary', '']
    if manifest:
        lines.append('Stack: %s | git %s | version %s | engine sha256 %s'
                     % ((manifest.get('stack') or {}).get('name'),
                        (manifest.get('git_sha') or '')[:12],
                        manifest.get('version'),
                        ((manifest.get('engine') or {}).get('sha256')
                         or '')[:12]))
        lines.append('')
    lines += ['| Board | Split | Gate triple | Caused DRC | Wall (s) | '
              'Status |',
              '|---|---|---|---|---|---|']
    for slug in sorted(records):
        r = records[slug]
        caused = sum(d for d in (r.get('caused') or {}).values() if d > 0)
        lines.append('| %s | %s | %s | %d | %s | %s |' % (
            slug, r.get('split'), _fmt_triple(_triple(r)), caused,
            r.get('wall_s') if r.get('wall_s') is not None else '-',
            r.get('status')))
    return '\n'.join(lines) + '\n'
