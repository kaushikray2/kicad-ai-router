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

"""Deterministic pipeline tools for the AI Router plugin.

Every tool works on FILES in a work directory; the user's open board is
never modified. The gate is kicad-cli DRC after a zone refill — the same
gate used to validate the method by hand."""
import hashlib, json, math, os, re, shutil, subprocess, sys, time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, 'worker.py')

class PipelineError(Exception):
    pass

# DRC classes a finish/heal step can plausibly ADD with its copper;
# the gate guard reverts a step that increases any of them (v1.2.3:
# widened from the original 3 -- hole_clearance and tracks_crossing
# are real finisher failure modes the old guard could not see).
# P2.1b (#155): the order tool_fix_violations works fault classes
# in -- shorts before clearances before hole rules before the
# cosmetic end of the list (plan.md P2.1b).
FAULT_FIX_ORDER = ('shorting_items', 'tracks_crossing', 'clearance',
                   'hole_clearance', 'hole_to_hole', 'items_not_allowed',
                   'copper_edge_clearance', 'annular_width', 'via_diameter',
                   'drill_out_of_range', 'track_width', 'solder_mask_bridge',
                   'track_dangling', 'via_dangling', 'copper_sliver')
FAULT_CLASSES = ('shorting_items', 'clearance', 'items_not_allowed',
                 'tracks_crossing', 'hole_clearance', 'hole_to_hole',
                 'copper_edge_clearance', 'annular_width', 'via_diameter',
                 'drill_out_of_range', 'track_width', 'solder_mask_bridge',
                 'track_dangling', 'via_dangling', 'copper_sliver')  # v1.9.6 (#39): sliver added -- clean-pass counts it, so the accept key must see it
# Copper-layer DRC classes for reporting (excludes silk/text/library
# noise and unconnected_items, which is tracked separately).
COPPER_CLASSES = FAULT_CLASSES  # v1.9.6 (#39): identical since sliver joined FAULT_CLASSES (kept as a name for reporting call sites)

# v1.11.5 (#123): relative import FIRST (dialog.py's pattern) -- in
# KiCad's package context a top-level 'config' module is not
# importable, and the old absolute-only import silently fell back
# to {} there, leaving hard-coded literals to disagree with
# config.py for any caller passing a partial config. The literals
# at the use sites are reconciled with config.py: reroute_accept
# 'connectivity' (was 'faults' in tool_reroute_window).
try:
    from .config import DEFAULTS as _CFG_DEFAULTS
except Exception:
    try:
        from config import DEFAULTS as _CFG_DEFAULTS
    except Exception:
        _CFG_DEFAULTS = {}

try:
    from . import naming
except Exception:
    import naming
# v1.12.2 (#170): the Phase 2 engine run contracts (pure helpers:
# route flags, exit-code mapping, stopped_by, why_open lines).
try:
    from . import engine_run
except Exception:
    import engine_run

def resolve_ban_names(raw, aliases):
    # v1.10.1 (#49): the ONE layer-law resolution point. raw config
    # names (canonical, board-specific, any case) -> canonical names
    # via the board's alias map from the worker summary; anything else
    # comes back as unknown so the caller can fail loudly instead of
    # each consumer silently dropping the name (the finisher resolved
    # names since v1.9, but TraceMaker's command line, the gate census
    # and the law policy string-matched the raw config).
    out, unknown = [], []
    for n in raw:
        c = (aliases or {}).get(str(n).strip().lower())
        if c is None: unknown.append(n)
        elif c not in out: out.append(c)
    return out, unknown

def _parse_factor(tok):
    # v1.11.5 (#121): a layer-cost factor is a number 1..1000 with
    # up to two decimal places ('1', '1.5', '1.25') -- the range
    # where a price nudges rather than switches is roughly 1.1-3,
    # mostly fractional, and the engine has always accepted
    # fractions (per-mille internally). Returns the float, or None
    # when the token is not of that shape. The worker carries the
    # same check (it loads standalone).
    if not tok or tok.count('.') > 1: return None
    whole, dot, frac = tok.partition('.')
    if not whole.isdigit(): return None
    if dot and (not frac.isdigit() or len(frac) > 2): return None
    return float(tok)

def parse_layer_cost(spec):
    # v1.11 (#73): the layer_cost config spec, engine syntax:
    # comma-separated LAYER=FACTOR pairs ('In1.Cu=10,In2.Cu=10'),
    # factor a number 1..1000 (1 = normal cost; fractional factors
    # to two decimal places since v1.11.5, #121). Returns
    # (pairs, error): pairs is a list of (name, factor) in spec
    # order, [] for an empty/off spec; on ANY malformed token the
    # whole spec is rejected -- (None, reason) -- and the caller
    # treats layer_cost as off. Partial pricing would silently
    # misprice a layer the user named, so there is no salvage mode.
    if spec is None: return [], None
    spec = str(spec).strip()
    if not spec: return [], None
    pairs, seen = [], set()
    for tok in spec.split(','):
        tok = tok.strip()
        if not tok: continue
        if '=' not in tok:
            return None, 'token %r is not LAYER=FACTOR' % tok
        name, _eq, fac = tok.partition('=')
        name = name.strip(); fac = fac.strip()
        if not name:
            return None, 'token %r has an empty layer name' % tok
        f = _parse_factor(fac)
        if f is None:
            return None, 'factor %r for layer %r is not a number (up to two decimal places)' % (fac, name)
        if f < 1 or f > 1000:
            return None, 'factor %r for layer %r is outside 1..1000' % (fac, name)
        if name.lower() in seen:
            return None, 'layer %r is priced twice' % name
        seen.add(name.lower())
        pairs.append((name, f))
    if not pairs:
        return None, 'spec %r names no layers' % spec
    return pairs, None

def resolve_cost_names(pairs, aliases):
    # v1.11 (#73): resolve parsed layer_cost pairs against a board's
    # alias map, the resolve_ban_names discipline -- canonical name
    # out, unknown names reported (never silently dropped by this
    # function; the caller warns and drops). Two spellings of one
    # canonical layer: the first in spec order wins.
    out, unknown, seen = [], [], set()
    for name, fac in pairs or []:
        c = (aliases or {}).get(str(name).strip().lower())
        if c is None: unknown.append(name)
        elif c not in seen:
            seen.add(c); out.append((c, fac))
    return out, unknown

def _fmt_factor(f):
    # Canonical factor spelling: integral factors without a decimal
    # point (an integer spec's engine command line stays
    # byte-identical to the pre-fractional format), fractions
    # trimmed of trailing zeros.
    f = float(f)
    if f == int(f): return str(int(f))
    return ('%.2f' % f).rstrip('0')

def format_layer_cost(pairs):
    # Canonical spec string for --layer-cost / the ledger.
    return ','.join('%s=%s' % (n, _fmt_factor(f)) for n, f in pairs or [])

def cost_tail(g):
    # v1.11.4 (#114): the layer-cost tail shared by every keep/discard
    # key in the stack (_accept_key here, the orchestrator's
    # _final_key). The gate dict carries cost_weighted_wirelength_mm
    # only when a layer_cost spec resolved onto the board; without
    # one the tail is a constant 0.0, so uncosted runs compare
    # exactly as they did before the term existed.
    w = (g or {}).get('cost_weighted_wirelength_mm')
    return 0.0 if w is None else float(w)

def starved_delta(g):
    # v1.11.5 (#116): the routing-caused starved_thermal delta the
    # gate baked into this record (CP_delta style: the board's own
    # base count subtracted, floored at 0 -- pre-existing
    # placement-caused thermals are not the routing's to answer
    # for). Absent on records from before the tier existed: 0.
    return int((g or {}).get('starved_thermal_delta') or 0)

def banned_delta(g):
    # v1.12.2 (#175): the routing-caused banned-layer delta the gate
    # baked into this record (the #116 treatment: the base board's
    # own count under the same law subtracted, floored at 0 --
    # inherited tracks on a banned layer are not this run's to
    # answer for, and each variant Pipeline baking its own base
    # reference is what removes the A/B asymmetry). Records from
    # before the field existed fall back to the RAW count, so old
    # ledgers keep their historical meaning.
    if (g or {}).get('banned_layer_tracks_delta') is None:
        return int((g or {}).get('banned_layer_tracks') or 0)
    return int(g['banned_layer_tracks_delta'] or 0)

def _accept_key(g):
    # v1.8.2 (#16): step acceptance is lexicographic -- a step is kept
    # only if it is no worse in (copper faults, unconnected items,
    # split nets), in that order. fault_count alone (pre-v1.8.2) kept
    # steps that left the board LESS connected (e.g. a refill that
    # isolates another net's pour fragment), and prune was not gated
    # at all.
    # v1.10.1 (#59): None-is-worst, matching _unc_key -- the old
    # 'or 0' mapped a MISSING unconnected_total to 0, the best value
    # (the mirror image of C1).
    # v1.11.4 (#114): layer-cost-weighted wirelength as the LAST term
    # -- tie-break only; it can never trade a connection or a fault
    # for cost. Before, a finisher leg could displace priced-layer
    # copper and be kept whenever the three gate numbers tied,
    # because no decision site consulted the price (NAS layer-cost
    # run: engine passes placed In1.Cu segments, the delivered board
    # had none).
    # v1.11.5 (#116): starved_thermal as its own tier after the
    # fault/connectivity block and before the cost tail -- a step
    # that starves pour thermals at equal faults/connectivity is
    # now rejected (the NAS layer-cost runs added +11/+8 starved
    # thermals while every key improved or tied), but the tier can
    # never trade a fault or a connection away, and it keys on the
    # routing-caused DELTA, so inherited base thermals don't block.
    # v1.12 (#63): skeleton intrusions price as their own term
    # after starved_delta and before the cost tail: the reservation
    # yields first under congestion, so it never costs a connection
    # -- a leg that closes a split by crossing a corridor is kept
    # and the intrusion recorded; a leg that only adds intrusions
    # still loses on this term. With the feature off the term is 0
    # and the key is the old key plus a constant. #158: intrusions
    # are NOT part of fault_count -- this term is where they are
    # priced in this key.
    _intr = int((g or {}).get('skeleton_intrusions') or 0)
    return (fault_count(g), 10 ** 9 if (g or {}).get('unconnected_total') is None else int(g['unconnected_total']),
            len((g or {}).get('split_nets') or []), starved_delta(g), _intr, cost_tail(g))

def fault_count(g):
    c = (g or {}).get('classes') or {}
    # v1.3.2: tracks on a banned layer (the layer-law census) count
    # as faults -- DRC cannot see them (the pour refills around a
    # track drawn on its own plane), so the census is the only guard.
    # #158: skeleton_intrusions is deliberately NOT in this count.
    # It is a secondary census, priced as its own term in
    # _accept_key / _final_key / _conn_key the way main prices
    # starved_thermal_delta -- never folded into the fault total.
    # Folding it in broke every caller that compares fault_count
    # against a board that structurally has no intrusions (the
    # portfolio skip guard vs the stripped base) or prices it with
    # no intrusion slot of its own (the fix_violations acceptance
    # test, the reroute connectivity key). The census still rides
    # every gate record, so intrusion movement stays visible in
    # the ledger and in finalize's records.
    # v1.12.2 (#175): the term keys on the DELTA above the base
    # board's own count (banned_delta), not the raw census.
    return sum(c.get(k, 0) for k in FAULT_CLASSES) + banned_delta(g)

def _conn_key(g):
    # tool_reroute_window's 'connectivity' acceptance key:
    # unconnected (None-is-worst, #59), splits, real faults, then
    # skeleton intrusions as their own term (#158) -- a reroute can
    # never trade a real fault for fewer corridor crossings.
    return (10 ** 9 if (g or {}).get('unconnected_total') is None else int(g['unconnected_total']),
            len((g or {}).get('split_nets') or []), fault_count(g),
            int((g or {}).get('skeleton_intrusions') or 0))

def _router_added(cur, base):
    # tool_fix_violations' base diff (#155): a current fault is
    # router-added when no base fault of the same type sits within
    # 0.5 mm of it -- matched greedily 1-to-1, nearest pairs first,
    # so one base fault can mask at most one current fault. #160:
    # the old 0.5 mm grid snap implemented a different rule -- it
    # chased base faults that jittered 0.02 mm across a bucket
    # boundary and masked genuinely new faults sharing a cell
    # 0.68 mm away, outside the documented tolerance.
    pairs = []
    for i, f in enumerate(cur):
        for j, g in enumerate(base):
            if g['type'] != f['type']: continue
            d = math.hypot(f['x'] - g['x'], f['y'] - g['y'])
            if d <= 0.5: pairs.append((d, i, j))
    pairs.sort()
    used_cur, used_base, matched = set(), set(), set()
    for _d, i, j in pairs:
        if i in used_cur or j in used_base: continue
        used_cur.add(i); used_base.add(j); matched.add(i)
    return [f for i, f in enumerate(cur) if i not in matched]


class Pipeline:
    def __init__(self, cfg, board_path, log):
        self.cfg = cfg
        self.log = log
        self.board_path = os.path.abspath(board_path)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        self.workdir = os.path.join(os.path.dirname(self.board_path), 'ai-route-' + stamp)
        os.makedirs(self.workdir, exist_ok=True)
        self.pass_no = 0
        self.ledger = []
        self.last_gate = None
        self.last_route = None
        src_copy = os.path.join(self.workdir, 'input.kicad_pcb')
        shutil.copy(self.board_path, src_copy)
        _pro, _dru = naming.sidecar_paths(self.board_path)  # (#182): project-stem resolution, not splitext(board) -- a renamed board keeps its netclasses / DRC rules
        if _pro:
            shutil.copy(_pro, os.path.join(self.workdir, 'input.kicad_pro'))
        if _dru:
            shutil.copy(_dru, os.path.join(self.workdir, 'input.kicad_dru'))
        self._gseq = 0   # v1.8.2 (#15): unique gate report names
        self._oseq = 0   # v1.8.2 (#14): unique step output names
        if cfg.get('route_open_only') and cfg.get('strip_existing', True):
            # v1.12.2 (#171): Finish-this-board routes only what is
            # open and never touches existing copper -- stripping
            # first would delete the copper the mode exists to keep.
            cfg['strip_existing'] = False
            self.log('route_open_only: strip_existing forced off (existing copper is kept)')
        if cfg.get('strip_existing', True):
            self.base = os.path.join(self.workdir, 'base.kicad_pcb')
            r = self._worker('strip', '--board', src_copy, '--out', self.base)
            self.log('Stripped existing routing: %s items removed; kept %s locked items, %s free vias' % (r.get('removed_tracks_vias'), r.get('kept_locked'), r.get('kept_free_vias')))  # v1.11.5 (#119)
        else:
            self.base = src_copy
        self.current = self.base
        self._record('setup', {'base': self.base})
        if cfg.get('route_open_only'):
            # v1.12.2 (#174): structural copper protection for
            # Finish-this-board. Strip is already forced off above;
            # now snapshot the input's lock flags and lock EVERY
            # pre-existing track/via on the working base, so no
            # engine, finisher or victim selection can remove or
            # displace the user's copper during the run. finalize()
            # restores the snapshot onto the delivered board.
            _locked_base = os.path.join(self.workdir, 'base.kicad_pcb')
            _lr = self._worker('lock_all', '--board', self.base, '--out', _locked_base)
            self._locks_file = _lr.get('locks_file')
            self._locks_protected = True
            self.base = _locked_base
            self.current = self.base
            self._record('lock_protect', {'items': _lr.get('items'), 'newly_locked': _lr.get('newly_locked')})
            self.log('route_open_only: %s pre-existing items locked for the run (original lock flags are restored at finalize)' % _lr.get('items'))

    # ------------------------------------------------------------ internals
    def _record(self, step, data):
        entry = {'t': time.strftime('%H:%M:%S'), 'step': step, 'data': data}
        self.ledger.append(entry)
        with open(os.path.join(self.workdir, 'ledger.json'), 'w') as f:
            json.dump(self.ledger, f, indent=1)

    def _sidecars(self, board_path):
        # v1.8.2 (#17): the project file and the custom-rules file
        # travel with every board explicitly. TraceMaker and the
        # workers' SaveBoard happen to chain the .kicad_pro today,
        # but the gate and the workers resolve rules from the board's
        # own basename -- guarantee them instead of relying on the
        # side effect, and propagate .kicad_dru (never copied before).
        base = os.path.splitext(board_path)[0]
        for ext in ('.kicad_pro', '.kicad_dru'):
            src = os.path.join(self.workdir, 'input' + ext)
            if os.path.exists(src):
                dst = base + ext
                if os.path.abspath(dst) != os.path.abspath(src):
                    shutil.copy(src, dst)

    def _out(self, name):
        # v1.8.2 (#14): every step writes a UNIQUE output file, so a
        # step's input and output paths never coincide and a revert
        # restores a file that still holds the pre-step board.
        self._oseq += 1
        return os.path.join(self.workdir, '%s-%d.kicad_pcb' % (name, self._oseq))

    def _worker(self, cmd, *args, timeout=1800):
        py = shutil.which('python3') or sys.executable
        self._wseq = getattr(self, '_wseq', 0) + 1
        result_path = os.path.join(self.workdir, 'worker-%s-%d.json' % (cmd, self._wseq))
        for _i, _a in enumerate(args):
            if _a == '--board' and _i + 1 < len(args):
                try: self._sidecars(args[_i + 1])
                except Exception: pass
        cmdline = [py, WORKER, cmd] + list(args) + ['--result', result_path]
        p = subprocess.run(cmdline, capture_output=True, text=True, timeout=timeout)  # v1.10.1 (#55): per-command -- engine commands pass 3900 so the worker outlives the engines' own 2400/3600 s timeouts
        if os.path.exists(result_path):
            try:
                with open(result_path) as f: return json.load(f)
            except Exception:
                pass
        lines = [l for l in p.stdout.strip().splitlines() if l.strip()]
        marked = [l for l in lines if l.startswith('AIR_RESULT ')]
        if not marked:
            raise PipelineError('worker %s produced no result line: %s' % (cmd, (lines[-1] if lines else p.stderr[-400:])[:300]))
        try:
            return json.loads(marked[-1][len('AIR_RESULT '):])
        except Exception:
            raise PipelineError('worker %s bad output: %s' % (cmd, marked[-1][:300]))

    # ------------------------------------------------------------ tools
    def tool_board_summary(self):
        r = self._worker('summary', '--board', self.current)
        split = [n for n in r['nets'] if n['components'] > 1 and n['net'] not in ('GND',)]
        r['split_nets'] = split
        self._record('summary', {'split_nets': [s['net'] for s in split]})
        return r

    def tool_skeleton(self, board=None):
        # #63: pour-skeleton state of a board (default: current) --
        # per-pour pad components, severed tree edges, the intrusion
        # census. The P2.1b pour fault definition compares the base
        # board's reading with the final board's.
        r = self._worker('skeleton', '--board', board or self.current)
        self._record('skeleton', r)
        return r

    def tool_scorecard(self):
        r = self._worker('scorecard', '--board', self.base)
        self._record('scorecard', r)
        return r

    def tool_route(self, first_nets=None, work=None, from_current=False, priority=None, regions=None):
        self.pass_no += 1
        inp = self.current if from_current else self.base
        outp = os.path.join(self.workdir, 'pass%d.kicad_pcb' % self.pass_no)
        advice = os.path.join(self.workdir, 'advice%d.json' % self.pass_no)
        first_nets = list(first_nets or [])
        if len(first_nets) > 64:  # TraceMaker rejects advice files with >64 first_nets
            self.log('first_nets capped at 64 (was %d)' % len(first_nets))
            first_nets = first_nets[:64]
        adv = {'version': 1, 'first_nets': first_nets}
        if priority: adv['priority'] = [list(p) for p in priority][:512]
        if regions: adv['regions'] = list(regions)[:24]
        with open(advice, 'w') as f:
            json.dump(adv, f)
        log_path = os.path.join(self.workdir, 'tm-pass%d.log' % self.pass_no)
        if getattr(self, '_copper_layers', None) is None:
            try: self._copper_layers = self._worker('summary', '--board', inp).get('copper_layers') or []
            except Exception: self._copper_layers = []
        banned = self._banned_for(inp)  # v1.10.1 (#49): resolved once against this board's stack; unmatched names warn loudly instead of being silently dropped (TraceMaker still receives canonical names only, which it requires)
        cmdline = [self.cfg['tracemaker_path'], 'route', inp, '-o', outp,
                   '--json', os.path.join(self.workdir, 'route%d.json' % self.pass_no),
                   '--stuck-report', os.path.join(self.workdir, 'stuck%d.json' % self.pass_no),
                   '--advice', advice, '--soft-zones', '--keep-vias-off-pads',
                   '--diff-pairs', '--si-rules', '--critical-auto', '--cut-report']
        if banned:
            cmdline += ['--no-tracks-on', ','.join(banned)]
        _cost = self._layer_cost_spec(inp)  # v1.11 (#73)
        if _cost: cmdline += ['--layer-cost', _cost]
        cmdline += engine_run.route_flags(self.cfg, work or self.cfg.get('work', 40000000))  # v1.12.2 (#170/#171)
        with open(log_path, 'w') as lf:
            p = subprocess.run(cmdline, stdout=lf, stderr=subprocess.STDOUT, timeout=engine_run.scaled_timeout(3900, work or self.cfg.get('work', 40000000)))  # v1.12.2 (#176): scales with the work budget
        if not os.path.exists(outp):
            _err = engine_run.engine_error(p.returncode, None)
            if _err:
                _tail = ''
                try: _tail = open(log_path).read().strip().splitlines()[-1][:200]
                except Exception: pass
                raise PipelineError('%s: %s (see %s)' % (_err, _tail, log_path))
            raise PipelineError('TraceMaker produced no board (see %s)' % log_path)
        try:
            self._worker('refill', '--board', outp, '--out', outp)  # zone fills must track the new layout before this board is saved/used
        except Exception as e:
            self.log('WARNING: post-route refill failed (%s) — continuing; the gate refills before measuring' % e)
        self._sidecars(outp)
        text = open(log_path).read()
        # v1.9 (Phase 1.4): read the engine's JSON diagnostics -- the
        # route report (counts, completion, per-variant results) and
        # the stuck report (structured unrouted connections with
        # reasons, crowding and a corridor region each). The log
        # regexes remain as a fallback for a missing/corrupt report.
        unrouted = []
        routed = (None, None)
        stuck = {'unrouted': [], 'by_reason': []}
        rj = {}
        try:
            rj = json.load(open(os.path.join(self.workdir, 'route%d.json' % self.pass_no)))
            routed = (rj.get('routed'), rj.get('connections'))
            stuck['route'] = {k: rj.get(k) for k in ('completion_pct', 'seconds', 'open', 'unconnected_after_refill')}
            stuck['failures'] = rj.get('failures') or []
            cuts = {k: rj[k] for k in rj if 'cut' in k}
            if cuts: stuck['cuts'] = cuts
        except Exception:
            pass
        try:
            sj = json.load(open(os.path.join(self.workdir, 'stuck%d.json' % self.pass_no)))
            for u in sj.get('unrouted') or []:
                fr, to = u.get('from') or {}, u.get('to') or {}
                stuck['unrouted'].append({'net': u.get('net'),
                    'from': '%s.%s' % (fr.get('part'), fr.get('pad')),
                    'to': '%s.%s' % (to.get('part'), to.get('pad')),
                    'reason': u.get('reason'), 'length_mm': u.get('length_mm'),
                    'fails': u.get('fails'), 'region_mm': u.get('region_mm')})
            stuck['by_reason'] = sj.get('unrouted_by_reason') or []
        except Exception:
            pass
        self.last_stuck = stuck
        self.last_stuck_board = outp  # v1.10.1 (#48): the stuck report describes the route OUTPUT; v1.9.6 tagged the pre-route board, so valid corridors were discarded and a restored earlier board could receive a newer pass's corridor
        if stuck['unrouted']:
            unrouted = [{'net': u['net'], 'from': u['from'], 'to': u['to'], 'note': u.get('reason') or ''} for u in stuck['unrouted']]
        else:
            for m in re.finditer(r'unrouted:\s+(\S+):\s+(\S+)\s+->\s+(\S+)\s+\(([^)]*)\)', text):
                unrouted.append({'net': m.group(1), 'from': m.group(2), 'to': m.group(3), 'note': m.group(4)})
        if routed[0] is None:
            m = re.search(r'routed (\d+)/(\d+) connections', text)
            routed = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        self.current = outp
        self.last_route = {'pass': self.pass_no, 'routed': routed, 'unrouted': unrouted, 'board': outp,
                           'stuck_unrouted': len(stuck['unrouted']), 'by_reason': stuck['by_reason'],
                           'route_stats': stuck.get('route'), 'cuts': stuck.get('cuts')} 
        # v1.12.2 (#170): the structured contract -- the exit code,
        # stopped_by and why_open come from the route JSON / process
        # return code, not from log inference. rc 1/2 aborts the
        # pass with the engine's message; rc 3 is a normal partial.
        _err = engine_run.engine_error(p.returncode, rj)
        if _err:
            _tail = ''
            try: _tail = text.strip().splitlines()[-1][:200]
            except Exception: pass
            raise PipelineError('%s: %s (see %s)' % (_err, _tail, log_path))
        self.last_route['exit_code'] = rj.get('exit_code', p.returncode)
        self.last_route['stopped_by'] = rj.get('stopped_by')
        self.last_route['route_open_only'] = bool(rj.get('route_open_only'))
        self.last_route['open_at_start'] = rj.get('open_at_start')
        if rj.get('stopped_by'):
            self.log('Pass %d: engine stopped: %s' % (self.pass_no, engine_run.stopped_by_text(rj.get('stopped_by'))))
        for _line in engine_run.why_open_lines(rj):
            self.log(_line)
        self._record('route', self.last_route)
        self.log('Pass %d: routed %s/%s connections; %d unrouted' % (self.pass_no, routed[0], routed[1], len(unrouted)))
        return self.last_route

    def tool_portfolio(self, engines):
        # v1.9.5 (Phase 1.2): alternative engines run from the same
        # stripped base; every candidate is gated and the winner by
        # the acceptance key becomes the current board. A candidate
        # that violates the layer law keys worse (banned tracks are
        # faults), so selection cannot prefer a law-breaking board
        # over a clean one at the same connectivity.
        engines = list(engines)
        # v1.11.3 (#109): freerouting/krt cannot honour layer_cost --
        # they take no layer-law arguments, and priced layers leave
        # the effective ban set, so the gate levies no penalty on
        # their priced-layer tracks: they would compete at 1x against
        # TraceMaker's priced cost with the mis-score invisible.
        # Exclude them while a valid cost spec is in force, loudly.
        excluded = []
        excluded_roo = []
        excluded_det = []
        _cost = self._layer_cost_spec(self.base)
        if _cost:
            for eng in ('freerouting', 'krt'):
                if eng in engines:
                    engines.remove(eng)
                    excluded.append(eng)
                    self.log('portfolio: %s excluded -- cannot honour layer_cost %s; priced-layer tracks would carry no gate penalty (#109)' % (eng, _cost))
        # v1.12.2 (#174): under route_open_only the same two engines
        # are excluded for the stronger reason -- they re-route the
        # base from scratch and know nothing of the mode, so a win
        # would replace the user's existing copper wholesale.
        if self.cfg.get('route_open_only'):
            for eng in ('freerouting', 'krt'):
                if eng in engines:
                    engines.remove(eng)
                    excluded_roo.append(eng)
                    self.log('portfolio: %s excluded -- route_open_only keeps existing copper; %s re-routes from scratch (#174)' % (eng, eng))
        # v1.12.2 (#176): deterministic mode excludes the same two
        # engines for its own reason -- freerouting runs -mt 2
        # (multithreaded, run-variant) and krt has no determinism
        # contract, so neither may win a Reproducible run.
        if self.cfg.get('deterministic'):
            for eng in ('freerouting', 'krt'):
                if eng in engines:
                    engines.remove(eng)
                    excluded_det.append(eng)
                    self.log('portfolio: %s excluded -- deterministic mode requires the reproducible engine only (#176)' % eng)
        results = []
        route_recs = {}
        winner = None
        for eng in engines:
            outp = self._out('pf-' + re.sub(r'[^A-Za-z0-9]+', '-', eng))
            try:
                if eng == 'freerouting':
                    r = self._worker('freeroute', '--board', self.base, '--out', outp, timeout=3900)
                elif eng == 'krt':
                    r = self._worker('krt_route', '--board', self.base, '--out', outp, timeout=3900)
                elif eng in ('tm_relax', 'tm_global', 'tm_escape'):
                    r = self._route_variant(eng, outp)
                else:
                    self.log('portfolio: unknown engine %r skipped' % eng)
                    continue
            except Exception as e:
                self.log('portfolio: %s failed (%s)' % (eng, e))
                continue
            if not os.path.exists(outp):
                self.log('portfolio: %s produced no board (%s)' % (eng, (r or {}).get('status')))
                continue
            g = self.tool_gate(outp)
            # v1.9.6 (#37 note): candidate selection is FAULTS-first by
            # design, while pass selection in the orchestrator is
            # connectivity-first. The layering is deliberate: faults-
            # first here is what kept KRT's 0-unconnected / 430-1142-
            # copper candidates from winning the Phase 1 A/B. Do not
            # 'unify' the keys without re-running that A/B.
            key = _accept_key(g)
            results.append({'engine': eng, 'key': list(key), 'board': outp,
                            'unconnected': g.get('unconnected_total'),
                            'copper': g.get('copper_violations'),
                            'banned': g.get('banned_layer_tracks'),
                            'seconds': (r or {}).get('seconds')})
            self.log('portfolio: %s key %s (unc %s, copper %s, banned %s)' %
                     (eng, key, g.get('unconnected_total'), g.get('copper_violations'), g.get('banned_layer_tracks')))
            if (r or {}).get('route'): route_recs[eng] = r['route']
            if winner is None or key < winner[0]:
                winner = (key, outp, g, eng)
        _rec = {'engines': list(engines), 'results': results,
                'winner': winner[1] if winner else None}
        if excluded: _rec['excluded_layer_cost'] = excluded
        if excluded_roo: _rec['excluded_route_open_only'] = excluded_roo
        if excluded_det: _rec['excluded_deterministic'] = excluded_det
        self._record('portfolio', _rec)
        if winner:
            self.current = winner[1]
            # v1.12.2 (#177): a TraceMaker win seeds the job route
            # record from the winning variant's route JSON -- with
            # the main passes skipped on a clean win, nothing else
            # would ever populate it.
            _wr = route_recs.get(winner[3])
            if _wr:
                self.last_route = dict(_wr, source='portfolio:' + winner[3])
                self._record('route', self.last_route)
            return winner[2]
        return None

    def _route_variant(self, eng, outp):
        # TraceMaker with a portfolio flag set and no advice -- the
        # variant IS the experiment. Mirrors tool_route's law handling
        # (ban names filtered to this board's copper layers).
        flag = {'tm_relax': '--relax', 'tm_global': '--global', 'tm_escape': '--escape-plan'}[eng]
        if getattr(self, '_copper_layers', None) is None:
            try: self._copper_layers = self._worker('summary', '--board', self.base).get('copper_layers') or []
            except Exception: self._copper_layers = []
        banned = self._banned_for(self.base)  # v1.10.1 (#49)
        cmdline = [self.cfg['tracemaker_path'], 'route', self.base, '-o', outp, flag,
                   '--json', outp + '.route.json',  # v1.12.2 (#177): the variant's structured record, seeded into last_route if it wins
                   '--soft-zones', '--keep-vias-off-pads', '--diff-pairs', '--si-rules', '--critical-auto']
        if banned: cmdline += ['--no-tracks-on', ','.join(banned)]
        _cost = self._layer_cost_spec(self.base)  # v1.11 (#73)
        if _cost: cmdline += ['--layer-cost', _cost]
        cmdline += engine_run.route_flags(self.cfg, self.cfg.get('work', 40000000), 1800, relax=(eng == 'tm_relax'))  # v1.12.2 (#170/#171); relax: #184 (deterministic round budget)
        log_path = outp + '.tm.log'
        with open(log_path, 'w') as lf:
            _rc = subprocess.run(cmdline, stdout=lf, stderr=subprocess.STDOUT, timeout=engine_run.scaled_timeout(2400, self.cfg.get('work', 40000000))).returncode  # v1.12.2 (#176)
        if _rc != 0:
            self.log('TraceMaker variant %s exited with code %d (log: %s)' % (eng, _rc, log_path))  # v1.9.6 (#40)
        if not os.path.exists(outp):
            raise PipelineError('TraceMaker variant %s produced no board' % eng)
        try: self._worker('refill', '--board', outp, '--out', outp)
        except Exception: pass
        self._sidecars(outp)
        # v1.12.2 (#177): hand the caller the variant's structured
        # record (same fields tool_route bakes into last_route), so
        # a portfolio win can seed the job's route record.
        _rj = {}
        try: _rj = json.load(open(outp + '.route.json'))
        except Exception: pass
        _rec = {'engine': eng, 'board': outp,
                'exit_code': _rj.get('exit_code', _rc),
                'stopped_by': _rj.get('stopped_by'),
                'route_open_only': bool(_rj.get('route_open_only')),
                'open_at_start': _rj.get('open_at_start')}
        return {'status': 'routed', 'seconds': _rj.get('seconds'), 'route': _rec}

    def _drc_measure(self, board):
        # v1.9: parse the JSON DRC report -- structured output instead
        # of regexes over the text report, whose layout is a KiCad
        # display detail. Field semantics are identical to the text
        # parse (verified class-by-class on fixture boards): the JSON
        # 'violations' list excludes unconnected items, exactly like
        # the text header total; unconnected entries carry their
        # items' descriptions, whose first word is the item kind and
        # whose [brackets] carry the net, same as the text blocks.
        # Falls back to the text report when JSON is unavailable or
        # malformed; a failed DRC still raises (v1.8.2 #15).
        self._gseq += 1
        seq = self._gseq
        jp = os.path.join(self.workdir, 'gate-%03d.drc.json' % seq)
        try:
            p = subprocess.run([self.cfg.get('kicad_cli', 'kicad-cli'), 'pcb', 'drc', '--refill-zones',
                                '--format', 'json', '--output', jp, board], capture_output=True, text=True, timeout=900)
            if p.returncode == 0 and os.path.exists(jp):
                doc = json.load(open(jp))
                viols = doc['violations']; uncs = doc['unconnected_items']
                classes = dict(Counter(v.get('type') or 'unknown' for v in viols))
                if uncs: classes['unconnected_items'] = len(uncs)
                pour, signal = 0, []
                for u in uncs:
                    items = u.get('items') or []
                    kinds = [str(it.get('description', '')).split(' ', 1)[0] for it in items]
                    nets = sorted({m.group(1) for it in items for m in [re.search(r'\[([^\]]+)\]', str(it.get('description', '')))] if m})
                    if items and all(k == 'Zone' for k in kinds):
                        pour += 1
                    else:
                        signal.append({'nets': nets, 'text': ' '.join(str(it.get('description', '')) for it in items)[:200]})
                return len(viols), len(uncs), classes, pour, signal
        except Exception:
            pass
        tp = os.path.join(self.workdir, 'gate-%03d.drc.txt' % seq)
        p = subprocess.run([self.cfg.get('kicad_cli', 'kicad-cli'), 'pcb', 'drc', '--refill-zones',
                            '--output', tp, board], capture_output=True, text=True, timeout=900)
        if p.returncode != 0 or not os.path.exists(tp):
            raise PipelineError('kicad-cli DRC failed (rc=%s) on %s: %s' % (p.returncode, os.path.basename(board), (p.stderr or '')[-300:]))
        d = open(tp).read()
        total = int(re.search(r'Found (\d+) DRC violations', d).group(1))
        unc = int(re.search(r'Found (\d+) unconnected', d).group(1))
        classes = dict(Counter(re.findall(r'^\[(\w+)\]', d, re.M)))
        pour, signal = 0, []
        for m in re.finditer(r'\[unconnected_items\].*?(?=\n\[|\Z)', d, re.S):
            block = m.group(0)
            items = re.findall(r"@\([^)]*\):\s+(\w+)(?:\s+'[^']*')?\s+\[([^\]]+)\]", block)
            if items and all(k == 'Zone' for k, _n in items):
                pour += 1
            else:
                signal.append({'nets': sorted(set(n for _k, n in items)), 'text': ' '.join(block.split())[:200]})
        return total, unc, classes, pour, signal

    def _layer_info(self, board, summ=None):
        # v1.10.1 (#49): one cached summary per board for layer data.
        # v1.11.2 (#101): a caller already holding this board's fast
        # summary (tool_gate fetches one for its census) seeds the
        # cache with it -- otherwise every gate of a fresh step board
        # paid for a second summary subprocess. The cache stays
        # lazily initialised via getattr: tests build Pipeline via
        # object.__new__ and never run __init__.
        cache = getattr(self, '_layer_info_cache', None)
        if cache is None: cache = self._layer_info_cache = {}
        if board not in cache:
            if summ is None:
                summ = self._worker('summary', '--board', board, '--fast')
            aliases = summ.get('layer_aliases') or {c.lower(): c for c in (summ.get('copper_layers') or [])}
            cache[board] = {'copper_layers': summ.get('copper_layers') or [], 'layer_aliases': aliases}
        return cache[board]

    def _cost_pairs(self):
        # v1.11 (#73): parse the configured layer_cost spec ONCE per
        # Pipeline. A malformed spec warns loudly and prices nothing
        # -- layer_cost is off, exactly as if unset (never raises).
        if getattr(self, '_cost_parse_cache', None) is None:
            pairs, err = parse_layer_cost(self.cfg.get('layer_cost', ''))
            if err:
                self.log('WARNING: layer_cost %r is invalid (%s) -- treated as off' % (self.cfg.get('layer_cost'), err))
                pairs = []
            self._cost_parse_cache = pairs
        return self._cost_parse_cache

    def _effective_policy(self, board, summ=None):
        # v1.11 (#73): the run's layer law for THIS board after the
        # layer_cost conflict rule -- a priced layer is NOT banned,
        # so costed layers leave the effective ban set. Returns
        # (effective_bans, cost_pairs, unknown_bans, unknown_costs),
        # canonical names throughout. The first computation is
        # recorded in the ledger ('layer_policy') so a run proves
        # which policy governed it; the un-banning and any dropped
        # cost names are logged once per Pipeline.
        info = self._layer_info(board, summ)
        aliases = info['layer_aliases']
        cost, unknown_costs = resolve_cost_names(self._cost_pairs(), aliases)
        cost_names = {n for n, _f in cost}
        raw = [x.strip() for x in str(self.cfg.get('no_tracks_on', 'In1.Cu')).split(',') if x.strip()]
        bans, unknown_bans = resolve_ban_names(raw, aliases)
        removed = [b for b in bans if b in cost_names]
        eff = [b for b in bans if b not in cost_names]
        if removed and not getattr(self, '_cost_unban_logged', False):
            self._cost_unban_logged = True
            self.log('layer_cost: %s priced, not banned -- removed from the effective no_tracks_on set (configured bans: %s)' % (', '.join(removed), ','.join(bans) or 'none'))
        if unknown_costs and not getattr(self, '_cost_unknown_logged', False):
            self._cost_unknown_logged = True
            self.log('WARNING: layer_cost names not on this board (dropped): %s (board layers: %s)' % (unknown_costs, info['copper_layers']))
        if not getattr(self, '_policy_recorded', False):
            self._policy_recorded = True
            self._record('layer_policy', {'layer_cost': format_layer_cost(cost),
                                          'no_tracks_on_effective': eff,
                                          'no_tracks_on_configured': raw,
                                          'layer_law_policy': str(self.cfg.get('layer_law_policy', 'auto'))})
        return eff, cost, unknown_bans, unknown_costs

    def _layer_cost_spec(self, board):
        # Canonical '--layer-cost' argument for this board ('' = off).
        return format_layer_cost(self._effective_policy(board)[1])

    def _leg_layer_args(self):
        # v1.11 (#73): extra worker-leg arguments carrying the run's
        # layer_cost (board-resolved canonical spec, the same string
        # the engine receives). [] when off -- leg command lines are
        # then exactly what they were before the feature.
        spec = self._layer_cost_spec(self.current)
        return ['--layer-cost', spec] if spec else []

    def _leg_skeleton_args(self):
        # v1.12 (#63): the pour-skeleton reservation flag for worker
        # legs and the gate's summary. [] when off -- leg command
        # lines are then exactly what they were before the feature,
        # the layer-cost rule.
        return ['--skeleton'] if self.cfg.get('pour_skeleton', True) else []

    def _leg_ban_args(self):
        # v1.11.2 (#104): legs receive the EFFECTIVE ban set for the
        # current board -- canonical, board-resolved, priced layers
        # removed -- exactly the law the engine paths run under. The
        # flag is always emitted: with layer_cost pricing every
        # configured ban the effective set is legitimately empty, and
        # for worker legs an omitted flag means the worker's default
        # {In1_Cu} ban, not 'ban nothing'. NOT _banned_for(): it logs
        # a WARNING on every call when a configured name is unknown
        # on this board, which would spam once per leg;
        # _effective_policy's own warnings are once per Pipeline.
        return ['--no-tracks-on', ','.join(self._effective_policy(self.current)[0])]

    def _banned_for(self, board):
        # v1.10.1 (#49): the layer law, resolved against THIS board.
        # A name that resolves (board spelling or canonical, any case)
        # bans that layer for every consumer. A name matching no layer
        # on this board bans nothing here -- the standing server config
        # names the 4-layer GND plane, and a 2-layer board must not fail
        # for it -- but it is logged loudly, exactly as the gate census
        # warns, never silently dropped. (First cut raised instead of
        # warning; the scratch-backend smoke caught it failing every route on
        # 2-layer boards under the standing config.)
        eff, _cost, unknown, _unknown_costs = self._effective_policy(board)
        if unknown:
            info = self._layer_info(board)
            self.log('WARNING: no_tracks_on names not on this board (ban vacuous here): %s (board layers: %s)' % (unknown, info['copper_layers']))
        return eff

    def tool_gate(self, board=None):
        board = board or self.current
        self._sidecars(board)
        # v1.8.2 (#15): a UNIQUE report per gate + the return code is
        # checked. Before, every post-pass gate reused gate<pass>.drc.txt
        # and a failed kicad-cli silently left the PREVIOUS board's
        # report to be parsed as this board's gate.
        total, unc, classes, pour, signal = self._drc_measure(board)
        summ = self._worker('summary', '--board', board, '--fast', *self._leg_skeleton_args())  # v1.9.6 (#40): the gate discards crossings/RUDY
        # v1.3.2 layer census: any track on a cfg-banned layer is a
        # layer-law fault, counted into fault_count via the gate dict.
        _tbl = summ.get('tracks_by_layer') or {}
        # v1.10.1 (#49): census against the board-resolved canonical
        # names; unknown names warn here (a gate must always measure)
        # -- the route path fails loudly on them via _banned_for.
        # v1.11 (#73): the census keys off the EFFECTIVE ban set -- a
        # layer_cost-priced layer is not banned, so its tracks are
        # legal here exactly as they are on the engine command line.
        _banned_names, _cost_pairs, _unknown_bans, _unknown_costs = self._effective_policy(board, summ)  # v1.11.2 (#101): seed the layer-info cache with the summary above -- no second summary run for this board
        if _unknown_bans: self.log('WARNING: no_tracks_on names not on this board (census ignores them): %s' % _unknown_bans)
        banned_layer_tracks = sum(v for k, v in _tbl.items() if k in _banned_names)
        # v1.5.1: exempt a pour net's tracks on its OWN plane layer(s)
        # (heal bridges there are legal by design); count the rest.
        _tbln = summ.get('tracks_by_layer_net') or {}
        _pl = summ.get('pour_layers') or {}
        banned_exempt_own_plane = 0
        for _net, _lays in _pl.items():
            for _lay in _lays:
                if _lay in _banned_names:
                    banned_exempt_own_plane += (_tbln.get(_lay) or {}).get(_net, 0)
        banned_layer_tracks = max(0, banned_layer_tracks - banned_exempt_own_plane)
        # v1.11.5 (#119): invariance census -- locked items and free
        # vias on the INPUT board vs this board. Strip keeps both by
        # construction; a later stage losing one is a defect no DRC
        # class can see, so every gate records the comparison and a
        # loss warns. The same census serves the #45 invariance gate.
        if getattr(self, '_input_census', None) is None:
            try:
                # v1.12.2 (#174): while route_open_only lock
                # protection is active the honest reference is the
                # protected base (every pre-existing item locked) --
                # a stage losing one must warn. finalize() restores
                # the original locks and resets the census, so the
                # delivered board is compared against the input.
                _census_src = self.base if getattr(self, '_locks_protected', False) else self.board_path
                _is = self._worker('summary', '--board', _census_src, '--fast')
                self._input_census = {'locked_items': _is.get('locked_items'), 'free_vias': _is.get('free_vias')}
            except Exception:
                self._input_census = {'locked_items': None, 'free_vias': None}
        _inv = {'input_locked_items': self._input_census.get('locked_items'), 'input_free_vias': self._input_census.get('free_vias'),
                'locked_items': summ.get('locked_items'), 'free_vias': summ.get('free_vias')}
        _inv['ok'] = not ((_inv['input_locked_items'] is not None and (_inv['locked_items'] or 0) < _inv['input_locked_items']) or
                          (_inv['input_free_vias'] is not None and (_inv['free_vias'] or 0) < _inv['input_free_vias']))
        if not _inv['ok']: self.log('WARNING: invariance census -- this board lost locked items or free vias vs the input: %s' % _inv)
        pour_nets = summ.get('pour_nets', [])
        split_nets = [n['net'] for n in summ['nets'] if n['components'] > 1 and n['net'] != 'GND']
        # v1.2.4: split_signal_nets excludes the board's actual pour nets
        # (the v3 audit's point: 'GND' is just one pour's name). split_nets
        # itself is unchanged -- AUTO derives pour finishing/healing from
        # split_nets x pour_nets, so narrowing it would disable both.
        split_signal_nets = [n['net'] for n in summ['nets'] if n['components'] > 1 and n['net'] not in pour_nets]
        # v1.2.5: split pour nets INCLUDING 'GND' -- heal/AUTO pour
        # consumers keyed off split_nets (which hard-excludes 'GND')
        # silently never healed a via-carrying GND fragment.
        split_pour_nets = [n['net'] for n in summ['nets'] if n['components'] > 1 and n['net'] in pour_nets]
        # v1.11.4 (#115): per-layer track length from the summary census
        # and, when a layer_cost spec resolved onto this board, the
        # cost-weighted wirelength (segment length x layer factor,
        # unpriced layers at 1). Recorded on every gate record, so a
        # layer-cost run is measurable from its ledger alone; the
        # weighted figure stays None when no spec is in force.
        _tll = summ.get('track_length_by_layer') or {}
        _cost_weighted = None
        if _cost_pairs and summ.get('track_length_by_layer') is not None:
            _fac = dict(_cost_pairs)
            _cost_weighted = round(sum(l * _fac.get(nm, 1) for nm, l in _tll.items()), 1)
        # v1.11.5 (#116): starved_thermal / isolated_copper are
        # reported on every gate; starved_thermal is also keyed, as
        # the routing-caused delta against the base board's own
        # count (the first gate of the base board sets the reference;
        # isolated_copper stays report-only -- mostly pour-shape
        # noise).
        _st = int(classes.get('starved_thermal') or 0)
        if board == self.base and getattr(self, '_base_starved', None) is None:
            self._base_starved = _st
        _st_delta = max(0, _st - int(getattr(self, '_base_starved', _st) or 0))
        # v1.12.2 (#175): same baking for the banned-layer census --
        # the first gate of the base board sets the reference under
        # THIS pipeline's law, later gates key on the excess only.
        if board == self.base and getattr(self, '_base_banned', None) is None:
            self._base_banned = banned_layer_tracks
        _banned_delta = max(0, banned_layer_tracks - int(getattr(self, '_base_banned', banned_layer_tracks) or 0))
        self.last_gate = {'violations_total': total, 'unconnected_total': unc,
                          'violations_excl_unconnected': total,  # v1.8.2 (#18): the KiCad header total ALREADY excludes unconnected items (proven on a live report); subtracting them again undercounted
                          'board_sha256': hashlib.sha256(open(board, 'rb').read()).hexdigest(),
                          'copper_violations': sum(classes.get(k, 0) for k in COPPER_CLASSES),
                          'vias': summ.get('vias'), 'wirelength_mm': summ.get('wirelength_mm'),
                          'classes': classes, 'starved_thermal': _st, 'starved_thermal_delta': _st_delta, 'isolated_copper': int(classes.get('isolated_copper') or 0), 'pour_items': pour, 'split_signal_nets': split_signal_nets, 'split_pour_nets': split_pour_nets,
                          'signal_items': signal, 'split_nets': split_nets,
                          'pour_nets': pour_nets, 'invariance': _inv, 'tracks_by_layer': _tbl, 'via_on_pad': summ.get('via_on_pad'), 'track_length_by_layer': _tll, 'cost_weighted_wirelength_mm': _cost_weighted, 'banned_layer_tracks': banned_layer_tracks, 'banned_layer_tracks_delta': _banned_delta, 'banned_exempt_own_plane': banned_exempt_own_plane, 'skeleton_intrusions': summ.get('skeleton_intrusions'), 'skeleton_intrusion_mm': summ.get('skeleton_intrusion_mm'), 'board': board}
        self._record('gate', self.last_gate)
        self.log('Gate: %d unconnected (%d pour, %d signal), %d violations' % (unc, pour, len(signal), total))
        if self.last_gate.get('skeleton_intrusions'):
            self.log('Gate: %d foreign items inside reserved pour corridors (%.1f mm) -- router-added pour faults (#63)' % (self.last_gate['skeleton_intrusions'], self.last_gate.get('skeleton_intrusion_mm') or 0.0))
        return self.last_gate

    def _drc_fault_items(self, board):
        # Item-level fault list for the fix_violations diff (#155):
        # [{type, x, y, nets}] from the JSON DRC report. nets are the
        # candidate offender nets in order -- track/via parties
        # first (a pad cannot be ripped; the copper that came to it
        # can). Fault point = mean of the items' positions.
        self._gseq += 1
        jp = os.path.join(self.workdir, 'fix-%03d.drc.json' % self._gseq)
        p = subprocess.run([self.cfg.get('kicad_cli', 'kicad-cli'), 'pcb', 'drc', '--refill-zones',
                            '--format', 'json', '--output', jp, board], capture_output=True, text=True, timeout=900)
        if p.returncode != 0 or not os.path.exists(jp):
            raise PipelineError('kicad-cli DRC (fault items) failed (rc=%s) on %s: %s' % (p.returncode, os.path.basename(board), (p.stderr or '')[-300:]))
        doc = json.load(open(jp))
        out = []
        for v in doc.get('violations') or []:
            t = v.get('type') or ''
            if t not in FAULT_CLASSES: continue
            items = v.get('items') or []
            if not items: continue
            xs = [float(it['pos']['x']) for it in items]; ys = [float(it['pos']['y']) for it in items]
            cand = []
            for prio, kind in ((0, ('Track', 'Via')), (1, ('Pad', 'Zone'))):
                for it in items:
                    d = str(it.get('description', ''))
                    m = re.search(r'\[([^\]]+)\]', d)
                    if m and d.split(' ', 1)[0] in kind and m.group(1) not in cand:
                        cand.append(m.group(1))
            out.append({'type': t, 'x': round(sum(xs) / len(xs), 2), 'y': round(sum(ys) / len(ys), 2), 'nets': cand})
        return out

    def tool_fix_violations(self):
        # P2.1b (#155): repair router-added copper faults on
        # already-connected nets. Diff the current board's
        # item-level DRC faults against the base board's -- a fault
        # is router-added when no base fault of the same type sits
        # within 0.5 mm. For each, the worker rips only the
        # offending net's copper within +-3 mm and re-closes with
        # the exact-checked finisher. Gate-verified like every
        # stage: the output stands only if the fault count strictly
        # drops, unconnected and splits do not grow (the tool's exit
        # terms), and the faults-first accept key does not regress.
        # A fix that trades faults is reverted.
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        cur = self._drc_fault_items(self.current)
        base = self._drc_fault_items(self.base)
        added = _router_added(cur, base)
        order = {t: i for i, t in enumerate(FAULT_FIX_ORDER)}
        added.sort(key=lambda f: order.get(f['type'], 99))
        added = added[:32]
        if not added:
            r = {'status': 'no_router_faults', 'faults': 0}
            self._record('fix_violations', r)
            self.log('fix_violations: no router-added faults')
            return r
        fp = os.path.join(self.workdir, 'fix-faults.json')
        json.dump(added, open(fp, 'w'))
        outp = self._out('fixed')
        r = self._worker('fix_violations', '--board', self.current, '--out', outp, '--faults', fp,
                         *self._leg_ban_args(), *self._leg_layer_args(), *self._leg_skeleton_args())
        if r.get('fixed') and os.path.exists(outp):
            prev = self.current; self.current = outp
            after = self.tool_gate()
            # #158: fault_count is real faults only, and the
            # unconnected comparison is None-is-worst (#59) -- an
            # unknown count must never read as 0 (fully connected).
            ok = (fault_count(after) < fault_count(before)
                  and (10 ** 9 if after.get('unconnected_total') is None else int(after['unconnected_total'])) <= (10 ** 9 if before.get('unconnected_total') is None else int(before['unconnected_total']))
                  and len(after.get('split_nets') or []) <= len(before.get('split_nets') or [])
                  and _accept_key(after) <= _accept_key(before))
            if not ok:
                self.current = prev; self.last_gate = before
                r = dict(r); r['status'] = 'reverted'
                self.log('fix_violations reverted: faults %d -> %d, unconnected %s -> %s' % (
                    fault_count(before), fault_count(after), before.get('unconnected_total'), after.get('unconnected_total')))
            else:
                r = dict(r); r['status'] = 'fixed'
        self._record('fix_violations', r)
        self.log('fix_violations: %s (%d fixed, %d failed of %d router-added)' % (
            r.get('status'), len(r.get('fixed') or []), len(r.get('failed') or []), len(added)))
        return r

    def tool_reroute_window(self, net, margin=6.0):
        # v1.9 (Phase 1.5): windowed engine re-route. Lock all copper
        # outside a window around the net's stuck connection, let
        # TraceMaker --reroute rip and re-route the window, keep the
        # result only if the lexicographic key improves.
        before = self.last_gate or self.tool_gate()
        kb = _accept_key(before)
        locked = self._out('locked')
        args = ['--board', self.current, '--out', locked, '--net', net, '--margin', str(margin)]
        _stuck = getattr(self, 'last_stuck', None) or {}
        if getattr(self, 'last_stuck_board', None) != self.current:
            _stuck = {}  # v1.9.6 (#38): corridor was measured on another board state -- fall back to the net window
        for u in _stuck.get('unrouted') or []:
            if u.get('net') == net: break
        else:
            u = None
        if u and u.get('region_mm'):
            r = u['region_mm']
            args += ['--fx', str(r[0]), '--fy', str(r[1]), '--tx', str(r[2]), '--ty', str(r[3])]
        res = self._worker('lock_window', *args)
        if res.get('status') != 'locked':
            self._record('reroute_window', {'net': net, 'status': 'lock_failed', 'detail': res})
            return {'status': 'lock_failed', 'net': net}
        outp = self._out('rerouted')
        advice = os.path.join(self.workdir, 'advice-rw-%d.json' % self._oseq)
        with open(advice, 'w') as f: json.dump({'version': 1, 'first_nets': [net]}, f)  # v1.9.6 (#38): protect the target net
        if getattr(self, '_copper_layers', None) is None:
            try: self._copper_layers = self._worker('summary', '--board', self.current).get('copper_layers') or []
            except Exception: self._copper_layers = []
        banned = self._banned_for(self.current)  # v1.10.1 (#49)
        cmdline = [self.cfg['tracemaker_path'], 'route', locked, '-o', outp, '--reroute',
                   '--json', os.path.join(self.workdir, 'route-rw-%d.json' % self._oseq),
                   '--stuck-report', os.path.join(self.workdir, 'stuck-rw-%d.json' % self._oseq),
                   '--advice', advice, '--soft-zones', '--keep-vias-off-pads',
                   '--diff-pairs', '--si-rules', '--critical-auto', '--cut-report']
        if banned: cmdline += ['--no-tracks-on', ','.join(banned)]
        _cost = self._layer_cost_spec(self.current)  # v1.11 (#73)
        if _cost: cmdline += ['--layer-cost', _cost]
        cmdline += engine_run.route_flags(self.cfg, self.cfg.get('work', 40000000), 1800, open_only=False)  # v1.12.2 (#170/#171): --route-open-only does not apply to a --reroute call
        log_path = os.path.join(self.workdir, 'tm-rw-%d.log' % self._oseq)
        try:
            with open(log_path, 'w') as lf:
                subprocess.run(cmdline, stdout=lf, stderr=subprocess.STDOUT, timeout=engine_run.scaled_timeout(1900, self.cfg.get('work', 40000000)))  # v1.12.2 (#176)
        except Exception as e:
            self._record('reroute_window', {'net': net, 'status': 'error', 'detail': str(e)[:200]})
            return {'status': 'error', 'net': net}
        if not os.path.exists(outp):
            self._record('reroute_window', {'net': net, 'status': 'no_output'})
            return {'status': 'no_output', 'net': net}
        try: self._worker('refill', '--board', outp, '--out', outp)
        except Exception: pass
        if res.get('locks_file'):  # v1.10.1 (#50): restore the original lock flags before this board can be delivered
            try: self._worker('restore_locks', '--board', outp, '--out', outp, '--locks', res['locks_file'])
            except Exception as e:
                # v1.11.5 (#124): a failed restore is a FAILED
                # ATTEMPT, not a warning. The old code carried on to
                # gate/accept, so a board whose user locks were never
                # restored could be delivered -- its items silently
                # unprotected against later strip/prune (#119).
                # Discard the re-route; the pre-reroute board stays
                # current.
                self.log('lock restore FAILED (%s); re-route discarded' % e)
                self._record('reroute_window', {'net': net, 'status': 'lock_restore_failed', 'detail': str(e)[:200]})
                return {'status': 'lock_restore_failed', 'net': net}
        self._sidecars(outp)
        after = self.tool_gate(outp)
        if str(self.cfg.get('reroute_accept', _CFG_DEFAULTS.get('reroute_accept', 'connectivity'))) == 'connectivity':
            ck = _conn_key
            kb, ka = ck(before), ck(after)
        else:
            ka = _accept_key(after)
        if ka < kb:
            self.current = outp; self.last_gate = after
            st = 'accepted'
        else:
            self.last_gate = before
            st = 'reverted'
        self._record('reroute_window', {'net': net, 'status': st, 'key_before': kb, 'key_after': ka, 'window': res.get('window'), 'accept': str(self.cfg.get('reroute_accept', _CFG_DEFAULTS.get('reroute_accept', 'connectivity')))})
        self.log('reroute_window %s: %s (key %s -> %s)' % (net, st, kb, ka))
        return {'status': st, 'net': net, 'gate': after}

    def tool_finish_leg(self, net):
        outp = self._out('finish-' + re.sub(r'[^A-Za-z0-9_+-]', '_', net))
        # The finisher self-verifies connectivity only; its grid planner
        # can still introduce copper faults (a via 0.18 mm from a track
        # it displaced around, a leg through a keepout). Gate-verify:
        # a finish that adds shorts/clearance/keepout faults is worse
        # than the split net it fixed, so revert it.
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        kb = _accept_key(before)
        extra = ['--attempt-select'] if self.cfg.get('attempt_select') else []
        r = self._worker('finish_leg', '--board', self.current, '--out', outp, '--net', net, *self._leg_ban_args(), *extra, *self._leg_layer_args(), *self._leg_skeleton_args())
        if r.get('status') in ('routed', 'already_connected') and os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if _accept_key(after) > kb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('finish_leg %s reverted: accept key %s -> %s' % (net, kb, _accept_key(after)))
        self._record('finish_leg', r)
        self.log('finish_leg %s: %s' % (net, r.get('status')))
        return r

    def tool_long_finish(self, net):
        # v1.4 phase (c) Stage B-0: large-window hard-A* finish for
        # far-apart split signal nets. Same gate-verify discipline as
        # tool_finish_leg: a long leg that adds copper faults is
        # reverted, however good its connectivity looks.
        outp = self._out('longfinish-' + re.sub(r'[^A-Za-z0-9_+-]', '_', net))
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        kb = _accept_key(before)
        r = self._worker('long_leg', '--board', self.current, '--out', outp, '--net', net, *self._leg_ban_args(), *self._leg_layer_args(), *self._leg_skeleton_args())
        if r.get('status') in ('routed', 'already_connected') and os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if _accept_key(after) > kb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('long_finish %s reverted: accept key %s -> %s' % (net, kb, _accept_key(after)))
        self._record('long_finish', r)
        self.log('long_finish %s: %s' % (net, r.get('status')))
        return r

    def tool_ilp_repair(self, nets):
        # v1.5 phase (c) Stage B: joint 0-1 ILP candidate selection
        # across co-windowed split signal nets (worker prices each
        # net's victim-set candidates, builds the exact conflict
        # graph, solves, and executes the chosen legs). Gate-verified
        # as a unit: if the combined repair adds copper faults, the
        # whole stage reverts.
        nets = [n for n in (nets or []) if n]
        if not nets: return {'status': 'no_nets'}
        outp = self._out('ilprepair')
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        kb = _accept_key(before)
        r = self._worker('ilp_repair', '--board', self.current, '--out', outp, '--nets', ','.join(nets), *self._leg_ban_args(), *self._leg_layer_args(), *self._leg_skeleton_args())
        if r.get('routed') and os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if _accept_key(after) > kb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('ilp_repair reverted: accept key %s -> %s' % (kb, _accept_key(after)))
        self._record('ilp_repair', r)
        self.log('ilp_repair %s: routed %s (solver %s)' % (','.join(nets), r.get('routed'), r.get('solver')))
        return r

    def tool_heal(self):
        # v1.2.2: per-net invocation. v1.2 called heal once for all
        # pour nets and the gate-verdict reverted EVERY net's bridges
        # if one net added a fault; now each split pour net is healed
        # and gated separately, so a bad bridge costs only its own net.
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        pour = set(before.get('pour_nets') or [])
        cands = list(before.get('split_pour_nets') or [n for n in (before.get('split_nets') or []) if n in pour]) or [None]
        agg = {'status': 'ok', 'bridges': 0, 'healed': [], 'failed': [], 'per_net': {}}
        cur_gate = before
        for net in cands:
            outp = self._out('healed')
            kb = _accept_key(cur_gate)
            args = ['--board', self.current, '--out', outp, *self._leg_ban_args()]
            args += self._leg_layer_args()  # v1.11 (#73): heal routes bridges on the grid
            args += self._leg_skeleton_args()  # v1.12 (#63)
            if net: args += ['--nets', net]
            r = self._worker('heal', *args)
            agg['per_net'][net or '*'] = r.get('status')
            if r.get('bridges') and os.path.exists(outp):
                prev = self.current
                self.current = outp
                after = self.tool_gate()
                if _accept_key(after) > kb:
                    self.current = prev
                    self.last_gate = cur_gate
                    r = dict(r); r['status'] = 'reverted_faults'
                    agg['per_net'][net or '*'] = 'reverted_faults'
                    self.log('heal[%s] reverted: accept key %s -> %s' % (net, kb, _accept_key(after)))
                else:
                    cur_gate = after
                    agg['bridges'] += r.get('bridges', 0)
                    agg['healed'] += r.get('healed') or []
            agg['failed'] += r.get('failed') or []
            self._record('heal', r)
        if agg['bridges'] == 0 and agg['failed']: agg['status'] = 'partial'
        self.log('heal: %d bridges, healed %s, failed %s' % (agg['bridges'], agg['healed'], agg['failed']))
        return agg

    def tool_stitch(self):
        # v1.3.2: gate-verified like finish/heal -- stitch used to
        # accept its output board unconditionally.
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        kb = _accept_key(before)
        outp = self._out('stitched')
        r = self._worker('stitch', '--board', self.current, '--out', outp, *self._leg_ban_args(), *self._leg_layer_args(), *self._leg_skeleton_args())  # v1.11.2 (#102): stitch DOES route -- _bridge_island draws track bridges (<=8 mm, <=2 vias) on the Grid; the v1.11.0 'vias only' exclusion rationale was wrong
        if os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if _accept_key(after) > kb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('stitch reverted: accept key %s -> %s' % (kb, _accept_key(after)))
        self._record('stitch', r)
        self.log('stitch: %d clusters stitched, %d failed' % (len(r.get('stitched', [])), len(r.get('failed', []))))
        return r

    def tool_prune(self):
        if self.cfg.get('route_open_only'):
            # v1.12.2 (#174): prune's whole diet is unlocked,
            # pad-less, zone-less copper -- under Finish-this-board
            # that describes the user's unfinished stubs and fanouts,
            # not router debris. The stage does not run in this mode.
            self._record('prune', {'pruned': 0, 'status': 'skipped_route_open_only'})
            self.log('prune: skipped -- route_open_only keeps all existing copper')
            return {'pruned': 0, 'status': 'skipped_route_open_only'}
        # v1.8.2 (#16): prune is gated like every other step -- before
        # it accepted its output unconditionally, so a prune that
        # disconnected live copper was invisible to the run.
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        kb = _accept_key(before)
        outp = self._out('pruned')
        r = self._worker('prune', '--board', self.current, '--out', outp)
        if r.get('pruned') and os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if _accept_key(after) > kb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_worse'
                self.log('prune reverted: accept key %s -> %s' % (kb, _accept_key(after)))
        self._record('prune', r)
        self.log('prune: %s dead copper items removed' % r.get('pruned'))
        return r

    def finalize(self):
        final = os.path.join(self.workdir, 'final.kicad_pcb')
        # v1.9.6 (#39): record what the final refill does to the fault
        # count -- the caller gates the delivered file, but a refill-
        # introduced delta was previously invisible in the ledger.
        pre = self.last_gate if (self.last_gate and self.last_gate.get('board') == self.current) else None
        self._worker('refill', '--board', self.current, '--out', final)
        if getattr(self, '_locks_protected', False) and getattr(self, '_locks_file', None):
            # v1.12.2 (#174): hand the delivered board its ORIGINAL
            # lock flags back (items added during the run keep the
            # state they were created with), then reset the census
            # reference to the input for the caller's final gate.
            self._worker('restore_locks', '--board', final, '--out', final, '--locks', self._locks_file)
            self._locks_protected = False
            self._input_census = None
            self._record('lock_restore', {'locks_file': self._locks_file})
        pro = os.path.join(self.workdir, 'input.kicad_pro')
        if os.path.exists(pro):
            shutil.copy(pro, os.path.join(self.workdir, 'final.kicad_pro'))
        self._sidecars(final)  # v1.8.2 (#17): also final.kicad_dru when present
        rec = {'final': final}
        if pre is not None:
            after = self.tool_gate(final)
            rec['invariance'] = after.get('invariance')  # v1.11.5 (#119): the delivered board's lock/free-via census vs the input
            rec['fault_delta'] = fault_count(after) - fault_count(pre)
            if rec['fault_delta']:
                self.log('finalize: refill changed the fault count by %+d' % rec['fault_delta'])
        self._record('finalize', rec)
        return final
