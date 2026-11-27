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

# Engineered nets (#67 Step 1, branch-only): markings sidecar + TL
# calculator.  Defensive import in the config pattern -- a run without
# markings must behave byte-identically to before, so any failure here
# simply disables the feature.
_ENG_IMPORT_ERROR = None
try:
    from ai_router import markings as _markings
    from ai_router import tlcalc as _tlcalc
    from ai_router import rfcheck as _rfcheck
    from ai_router import powertree as _powertree
    from ai_router import powerplan as _powerplan
    from ai_router import wcca as _wcca
    from ai_router import kbclient as _kbclient
    from ai_router import library_rules as _library_rules
    from ai_router import placement_audit as _placement_audit
    from ai_router import lengthmatch as _lengthmatch
    from ai_router import hvdomains as _hvdomains
    from ai_router import quietnets as _quietnets
except Exception as _e:
    # #140 (B14): the old flat-import fallback could never
    # succeed -- five of these modules use relative imports, so
    # the fallback always collapsed to all-None after partially
    # binding names, indistinguishable from a real failure.  It
    # is gone: package import or nothing, the exception is kept
    # for the loud failure in setup() (#150), and every handle is
    # defined either way.
    _ENG_IMPORT_ERROR = repr(_e)
    _markings = None
    _tlcalc = None
    _rfcheck = None
    _powertree = None
    _powerplan = None
    _wcca = None
    _kbclient = None
    _library_rules = None
    _placement_audit = None
    _lengthmatch = None
    _hvdomains = None
    _quietnets = None

# KiCad's built-in Default netclass values (what LoadBoard synthesises
# when a board has no .kicad_pro); used as the clone source for an
# engineered-net class when there is no project class to clone.
_ENG_DEFAULT_CLASS = {'bus_width': 12, 'clearance': 0.2, 'diff_pair_gap': 0.2,
                      'diff_pair_via_gap': 0.25, 'diff_pair_width': 0.25,
                      'line_style': 0, 'microvia_diameter': 0.3,
                      'microvia_drill': 0.1, 'name': 'Default',
                      'pcb_color': 'rgba(0, 0, 0, 0.000)', 'priority': -1,
                      'schematic_color': 'rgba(0, 0, 0, 0.000)',
                      'track_width': 0.2, 'tuning_profile': '',
                      'via_diameter': 0.8, 'via_drill': 0.4, 'wire_width': 6}


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

def _treatment_key(g):
    # v1.11.5 (#133): engineered treatments (meanders, power
    # widening, RF fences/vias, audit zones) are judged on the
    # first three elements of the acceptance key -- faults,
    # unconnected, splits -- NEVER on the tail. The tail (the
    # layer-cost-weighted wirelength main's key carries after
    # #115, behind the starved-thermal delta) measures routing
    # cost: a length-adding treatment increases it BY DESIGN, so
    # on the full key every meander would revert whenever a layer
    # cost is set. Slicing [:3] makes the comparison hold for this
    # branch's 3-element key and the merged key alike.
    return _accept_key(g)[:3]

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


def _hv_worst_pair_req_mm(d, doms, terminations=True):
    """#200: the worst IPC-2221B spacing (mm) domain `d` must keep
    against the whole declared domain set -- every other declared
    domain AND the implicit rest-of-board domain at 0 V (coating
    'uncoated', altitude the max over the declared set) -- with
    each pair evaluated from BOTH sides (hvdomains'
    pair_required_symmetric_mm) so the governing side's coating /
    altitude wins.  terminations=True (the default) applies the
    A6/A7 termination columns alongside the conductor columns:
    the materialise paths stamp around PADS, and pads are
    terminations, not conductors (at 30 V uncoated the conductor
    column B2 reads 0.10 mm where terminations require 0.25 mm).
    Shared by _domain_setup, _eng_patch_pro_hv and (through the
    symmetric primitive) the tool_hv_domains check path, so all
    three HV paths agree on the domain set and the requirement."""
    if _hvdomains is None:
        return 0.0
    def _v(x):
        return float(x.get('peak_v', x.get('voltage_v')) or 0.0)
    implicit = {'peak_v': 0.0, 'voltage_v': 0.0,
                'coating': 'uncoated',
                'altitude_m': max([float(x.get('altitude_m') or 0)
                                   for x in doms] or [0])}
    req = 0.0
    for o in list(doms) + [implicit]:
        if o is d:
            continue
        dv = abs(_v(d) - _v(o))
        if dv <= 0:
            continue
        req = max(req, _hvdomains.pair_required_symmetric_mm(
            dv, True, d, o, terminations)[0])
    return req


class Pipeline:
    def __init__(self, cfg, board_path, log):
        self.cfg = dict(cfg)  # #189: the run coerces keys (route_open_only forces strip_existing off); that write belongs to the run, not to the caller's dict
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
        if self.cfg.get('mode') == 'llm' and self.cfg.get('deterministic'):
            # (#194): Reproducible is an AUTO-mode property (#171).
            # #185 enforced that at the config boundaries (dialog,
            # run_job.build_cfg), but a direct Pipeline caller could
            # still hand an LLM run --deterministic; the pipeline is
            # the layer every construction path passes through, so
            # the coercion lives here too.
            self.cfg['deterministic'] = False
            self.log('deterministic forced off -- mode is llm (Reproducible is an Auto-mode property)')
        if self.cfg.get('route_open_only') and self.cfg.get('strip_existing', True):
            # v1.12.2 (#171): Finish-this-board routes only what is
            # open and never touches existing copper -- stripping
            # first would delete the copper the mode exists to keep.
            self.cfg['strip_existing'] = False
            self.log('route_open_only: strip_existing forced off (existing copper is kept)')
        if self.cfg.get('strip_existing', True):
            self.base = os.path.join(self.workdir, 'base.kicad_pcb')
            r = self._worker('strip', '--board', src_copy, '--out', self.base)
            self.log('Stripped existing routing: %s items removed; kept %s locked items, %s free vias' % (r.get('removed_tracks_vias'), r.get('kept_locked'), r.get('kept_free_vias')))  # v1.11.5 (#119)
        else:
            self.base = src_copy
        self.current = self.base
        self._record('setup', {'base': self.base})
        if self.cfg.get('route_open_only'):
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
        self._eng = None
        if _markings is None or _tlcalc is None or _hvdomains is None or _quietnets is None:
            # v1.11.5 (#150): a markings sidecar exists but the
            # engineered modules failed to import (e.g. a PCM zip
            # that does not ship them) -- fail LOUDLY. Silently
            # running unmarked would route the board as if it had
            # no markings at all.
            _sc = naming.project_sidecar_path(self.board_path, '.ai-router-markings.json')
            if os.path.exists(_sc):
                raise RuntimeError('markings sidecar present (%s) but the engineered-nets '
                                   'modules failed to import (%s) -- refusing to run unmarked'
                                   % (_sc, _ENG_IMPORT_ERROR))
        self._domains_path = None  # #80: set by _domain_setup when materialise markings exist
        try:
            self._engineered_setup()
        except Exception as e:
            # Markings must never break a route: surface it and continue.
            self._eng = {'error': 'engineered setup failed: %s' % e}
            self.log('Engineered nets: setup failed (%s) -- continuing unmarked' % e)
            self._record('engineered_nets', {'error': str(e)})
        self._audit = None
        self._audit_apply = False
        try:
            self.tool_placement_audit()
        except Exception as e:
            # The audit must never break a route either.
            self.log('Placement audit failed (%s) -- continuing without it' % e)
            self._record('placement_audit', {'error': str(e)})
        if self.current != self.base:
            # v1.11.5 (#132): a gate-adopted audit materialisation
            # becomes part of the base every pass starts from.
            self.base = self.current
        try:
            _mk = _markings.load_markings(self.board_path)
            # #138 (B13): domain setup is an engineered stage too
            if _mk is not None and (_mk.hv_domains or _mk.quiet)                     and self.cfg.get('engineered_nets', True):
                self._domain_setup(_mk)
            if _mk is not None and _mk.hv_domains                     and self.cfg.get('engineered_nets', True):
                # #148 (B24): HV spacing must bind the ENGINE, not
                # only the finisher grid and a post-route report.
                try:
                    self._eng_hv_classes = self._eng_patch_pro_hv(_mk)
                    if self._eng_hv_classes:
                        self.log('HV netclasses enforced: %s'
                                 % ', '.join('%s=%.3f mm' % (
                                     v['class'], v['clearance_mm'])
                                     for v in self._eng_hv_classes.values()))
                except Exception as e:
                    self.log('HV netclass setup failed (%s) -- '
                             'continuing report-only' % e)
        except Exception as e:
            self.log('Domain setup failed (%s) -- continuing' % e)

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
    # ------------------------------------------------- engineered nets (#67)
    def _engineered_first_nets(self):
        eng = getattr(self, '_eng', None)
        if isinstance(eng, dict):
            base = list(eng.get('first_nets') or [])
        else:
            base = []
        # v1.11.5 (#132): the audit's extras join ONLY when the audit
        # is in apply mode (markings present, or placement_audit =
        # 'apply'). By default the audit is report-only: an unmarked
        # board's routing order must not change because a library
        # footprint happened to classify as an RF module.
        extra = []
        if getattr(self, '_audit_apply', False):
            extra = ((getattr(self, '_audit', None) or {}).get('first_nets_extra') or [])
        return base + [n for n in extra if n not in base]

    def _engineered_setup(self):
        # #67 Step 1: when a markings sidecar exists for the input board,
        # size its impedance-marked nets from the board stackup, write
        # the computed widths into the working copy's project netclasses
        # (one AI-IMP-* class per net, cloned from the net's current
        # class), and remember the nets so tool_route puts them first.
        # No sidecar -> self._eng stays False and nothing changes.
        if _markings is None or _tlcalc is None:
            self._eng = False
            return
        if not self.cfg.get('engineered_nets', True):
            self._eng = False
            return
        try:
            mk = _markings.load_markings(self.board_path)
        except _markings.MarkingsError as e:
            self._eng = {'error': str(e), 'first_nets': []}
            self.log('Engineered nets: %s -- continuing unmarked' % e)
            self._record('engineered_nets', {'error': str(e)})
            return
        if mk is None:
            self._eng = False
            return
        nets = mk.impedance_nets()
        pairs = mk.differential
        legs = [n for p in pairs for n in p['nets']]
        all_marked = nets + [l for l in legs if l not in nets]
        state = {'markings_file': os.path.basename(mk.path or ''),
                 'first_nets': all_marked, 'nets': {},
                 'entries': {e['net']: e for e in mk.impedance},
                 'diff_entries': {p['name']: p for p in pairs}, 'diff': {}}
        self._eng = state
        if not nets and not pairs:
            self._record('engineered_nets',
                         {'note': 'sidecar present, no impedance or differential markings'})
            return
        su = self._worker('stackup', '--board', self.base, '--nets', ','.join(all_marked))
        state['stackup_defined'] = bool(su.get('defined'))
        state['stackup_placeholder'] = bool(su.get('placeholder'))
        state['placeholder_reasons'] = su.get('placeholder_reasons') or []
        if not su.get('defined'):
            self.log('Engineered nets: no stackup in the board file -- marked '
                     'nets %s join first_nets but cannot be sized' % all_marked)
            self._record('engineered_nets',
                         {'stackup_defined': False,
                          'placeholder_reasons': state['placeholder_reasons'],
                          'first_nets': all_marked})
            return
        copper = {c['name']: c for c in su.get('copper') or []}
        sized = {}
        for entry in mk.impedance:
            net = entry['net']
            info = (su.get('nets') or {}).get(net) or {}
            cand = [c for c in (entry.get('layers') or info.get('pad_layers') or [])
                    if c in copper]
            per_layer = {lname: self._eng_layer_width(entry, copper[lname], len(copper))
                         for lname in cand}
            primary = next((l for l in cand if per_layer[l].get('width_mm')), None)
            rec = {'target_z0': entry['z0_ohm'], 'freq_hz': entry.get('freq_hz'),
                   'dk_basis': entry.get('dk_basis') or 'stackup',
                   'candidate_layers': cand, 'per_layer': per_layer,
                   'primary_layer': primary,
                   'width_mm': per_layer[primary]['width_mm'] if primary else None,
                   'pads': info.get('pads', 0)}
            if not cand:
                rec['warning'] = 'net has no pads on any copper layer (not on board?)'
            elif primary is None:
                rec['warning'] = 'no layer produced a solvable width'
            sized[net] = rec
            state['nets'][net] = rec
        dsized = {}
        for pair in pairs:
            pname = pair['name']
            pad_layers = sorted({l for n in pair['nets']
                                 for l in (((su.get('nets') or {}).get(n) or {}).get('pad_layers') or [])})
            cand = [c for c in (pair.get('layers') or pad_layers) if c in copper]
            per_layer = {lname: self._eng_diff_layer(pair, copper[lname], len(copper))
                         for lname in cand}
            primary = next((l for l in cand if per_layer[l].get('width_mm')), None)
            rec = {'nets': pair['nets'], 'target_zdiff': pair['zdiff_ohm'],
                   'freq_hz': pair.get('freq_hz'), 'candidate_layers': cand,
                   'per_layer': per_layer, 'primary_layer': primary,
                   'width_mm': per_layer[primary]['width_mm'] if primary else None,
                   'gap_mm': per_layer[primary]['gap_mm'] if primary else None}
            if not cand:
                rec['warning'] = 'pair legs have no pads on any copper layer (not on board?)'
            elif primary is None:
                rec['warning'] = 'no layer produced a solvable pair geometry'
            dsized[pname] = rec
            state['diff'][pname] = rec
        applied = self._eng_patch_pro(sized, su)
        dapplied = self._eng_patch_pro_diff(dsized, su) if dsized else {}
        su2 = self._worker('stackup', '--board', self.base, '--nets', ','.join(all_marked))
        for net, rec in sized.items():
            got = ((su2.get('nets') or {}).get(net) or {}).get('netclass_width_mm')
            rec['applied_class'] = applied.get(net)
            rec['applied_width_mm'] = got
            rec['width_verified'] = bool(
                got is not None and rec['width_mm'] is not None
                and abs(got - rec['width_mm']) < 0.001)
        for pname, rec in dsized.items():
            rec['applied_class'] = dapplied.get(pname)
            ok = bool(rec.get('width_mm') and rec.get('gap_mm'))
            rec['applied_per_leg'] = {}
            for leg in rec['nets']:
                li = (su2.get('nets') or {}).get(leg) or {}
                gw, gs = li.get('netclass_diff_width_mm'), li.get('netclass_diff_gap_mm')
                rec['applied_per_leg'][leg] = {'diff_width_mm': gw, 'diff_gap_mm': gs}
                ok = bool(ok and gw is not None and gs is not None
                          and abs(gw - rec['width_mm']) < 0.001
                          and abs(gs - rec['gap_mm']) < 0.001)
            rec['verified'] = ok
        self._record('engineered_nets', {
            'stackup_defined': True, 'placeholder': state['stackup_placeholder'],
            'sized': {n: {'primary_layer': r['primary_layer'],
                          'width_mm': r['width_mm'],
                          'verified': r.get('width_verified')}
                      for n, r in sized.items()},
            'diff_sized': {p: {'primary_layer': r['primary_layer'],
                               'width_mm': r['width_mm'], 'gap_mm': r['gap_mm'],
                               'verified': r.get('verified')}
                           for p, r in dsized.items()}})
        self.log('Engineered nets: sized %s'
                 % {n: r['width_mm'] for n, r in sized.items()})

    def _eng_layer_model(self, entry, cu, ncu):
        """Build the tlcalc model for ONE copper layer of an
        impedance marking: {'fn': w -> TLResult, 'model': name,
        'flags': [...]} or an {'error': ...} dict (with model/
        flags where the original solver path carried them).
        Extracted verbatim from _eng_layer_width so the width
        solver and the per-layer Z0 band check (#67 tol_pct,
        rfcheck GATE-13) share ONE model and can never drift."""
        target = entry['z0_ohm']
        f = entry.get('freq_hz') or 0.0
        t = cu.get('thickness_mm')
        flags = []
        if t is None:
            t = 0.035
            flags.append('cu_thickness_assumed_0.035')
        cls = cu.get('class')
        if cls in ('surface_top', 'surface_bottom'):
            diel = cu.get('diel_below') if cls == 'surface_top' else cu.get('diel_above')
            if not diel or not diel.get('thickness_mm'):
                return {'error': 'no dielectric inside the surface layer'}
            h = diel['thickness_mm']
            er = entry.get('dk') or diel.get('er')
            if not er:
                return {'error': 'dielectric er unknown and no dk in marking'}
            tand = diel.get('loss_tangent') or 0.0
            if entry.get('model') == 'coplanar':
                gap = entry.get('gap_mm')
                if not gap:
                    return {'error': 'coplanar marking needs gap_mm'}
                fn = lambda w: _tlcalc.coplanar(w, gap, h, t, er, f_hz=f,
                                                grounded=ncu > 1, tand=tand)
                model = 'coplanar'
            else:
                mask = None
                want = entry.get('mask')
                sm = cu.get('mask')
                if want == 'covered' or (want is None and sm):
                    if sm and sm.get('thickness_mm'):
                        mask = (sm['thickness_mm'], sm.get('er') or 3.5)
                        if not sm.get('er'):
                            flags.append('mask_er_assumed_3.5')
                    else:
                        mask = (0.02, 3.5)
                        flags.append('mask_assumed_0.02mm_er3.5')
                fn = lambda w: _tlcalc.microstrip(w, h, t, er, f_hz=f,
                                                  tand=tand, mask=mask)
                model = 'microstrip'
        elif cls == 'inner':
            da, db = cu.get('diel_above'), cu.get('diel_below')
            if (not da or not db or not da.get('thickness_mm')
                    or not db.get('thickness_mm')):
                return {'error': 'inner layer lacks dielectric on both sides'}
            h1 = db['thickness_mm']  # gap from strip face to the lower plane
            h2 = da['thickness_mm']
            b = h1 + t + h2
            if entry.get('dk'):
                er = entry['dk']
            elif da.get('er') and db.get('er'):
                er = (da['er'] * h2 + db['er'] * h1) / (h1 + h2)
                if abs(da['er'] - db['er']) > 0.05:
                    flags.append('mixed_dielectric_er_weighted')
            else:
                er = da.get('er') or db.get('er')
            if not er:
                return {'error': 'dielectric er unknown and no dk in marking'}
            tand = da.get('loss_tangent') or db.get('loss_tangent') or 0.0
            fn = lambda w: _tlcalc.stripline(w, b, t, er, a=h1, f_hz=f, tand=tand)
            model = 'stripline'
        else:
            return {'error': 'unknown layer class %r' % (cls,)}
        return {'fn': fn, 'model': model, 'flags': flags}

    def _eng_layer_width(self, entry, cu, ncu):
        m = self._eng_layer_model(entry, cu, ncu)
        if m.get('error'):
            return m
        fn, model, flags = m['fn'], m['model'], m['flags']
        target = entry['z0_ohm']
        try:
            w = _tlcalc.solve_width(lambda ww: fn(ww).z0, target)
        except ValueError as e:
            # #145 (B20 row 4): one unsolvable geometry (e.g. a
            # coplanar gap the thickness correction consumes) must
            # fail THIS net, not the whole sizing stage
            return {'model': model, 'flags': flags,
                    'error': 'geometry unsolvable: %s' % e}
        if w is None:
            return {'model': model, 'flags': flags,
                    'error': 'target z0 unreachable in width range'}
        res = fn(w)
        return {'model': model, 'width_mm': round(w, 4),
                'z0_at_width': round(res.z0, 3), 'eps_eff': round(res.eps_eff, 4),
                'in_range': res.in_range, 'flags': flags + list(res.flags),
                'loss_db_per_mm': res.loss_db_per_mm}

    def _eng_patch_pro(self, sized, su):
        # Write one AI-IMP-* netclass per sized net into the WORKDIR copy
        # of the project file (never the user's own): clone of the net's
        # current effective class with the track width replaced, plus an
        # exact-name pattern assignment.  _sidecars() propagates the
        # patched file beside every board the workers/engine touch.
        pro_path = os.path.join(self.workdir, 'input.kicad_pro')
        data = None
        if os.path.exists(pro_path):
            try:
                with open(pro_path) as fh:
                    data = json.load(fh)
            except Exception:
                data = None
        if not isinstance(data, dict):
            data = {}
        nset = data.setdefault('net_settings', {})
        classes = nset.setdefault('classes', [])
        patterns = nset.setdefault('netclass_patterns', [])
        if not any(isinstance(c, dict) and c.get('name') == 'Default' for c in classes):
            classes.insert(0, dict(_ENG_DEFAULT_CLASS))
        by_name = {c.get('name'): c for c in classes if isinstance(c, dict)}
        applied = {}
        used = set()
        for net, rec in sized.items():
            if not rec.get('width_mm'):
                continue
            base_name = 'AI-IMP-' + (re.sub(r'[^A-Za-z0-9]+', '_', net).strip('_') or 'NET')
            name, k = base_name, 2
            while name in used:
                name = '%s_%d' % (base_name, k)
                k += 1
            used.add(name)
            src_name = ((su.get('nets') or {}).get(net) or {}).get('netclass')
            clone = dict(by_name.get(src_name) or by_name.get('Default')
                         or _ENG_DEFAULT_CLASS)
            clone['name'] = name
            clone['track_width'] = round(rec['width_mm'], 4)
            clone['priority'] = 1000
            if name in by_name:
                classes[classes.index(by_name[name])] = clone
            else:
                classes.append(clone)
            by_name[name] = clone
            # KiCad projects commonly carry an explicit Default pattern
            # for every net; a second matching pattern makes KiCad build
            # a COMPOSITE class whose width resolution kept Default's
            # value in testing.  The marking replaces the net's
            # assignment outright (the clone carries every other
            # parameter of the class it replaces).
            patterns[:] = [pp for pp in patterns
                           if not (isinstance(pp, dict) and pp.get('pattern') == net)]
            patterns.append({'netclass': name, 'pattern': net})
            applied[net] = name
        with open(pro_path, 'w') as fh:
            json.dump(data, fh, indent=1)
        return applied


    def _eng_patch_pro_hv(self, mk):
        # #148 (B24): one AI-HV-* netclass per HV domain in the
        # WORKDIR project copy, clearance = the domain's required
        # spacing against its worst neighbour (every other declared
        # domain, plus the implicit rest-of-board at 0 V), taking
        # the larger of the conductor/termination requirements.
        # Netclasses are net-selective (the property rule areas
        # lacked, #80), the engine reads them (the same channel
        # the impedance classes use), and the kicad-cli gate scores
        # netclass clearance violations as faults -- so the spacing
        # is enforced during routing, not just reported after it.
        if _hvdomains is None:
            return {}
        doms = list(mk.hv_domains or [])
        if not doms:
            return {}
        # #200: one shared worst-pair computation (both sides of
        # every pair + the implicit 0 V rest-of-board domain),
        # shared with _domain_setup so the netclass path and the
        # finisher-rect path can never disagree again.
        reqs = [_hv_worst_pair_req_mm(d, doms, terminations=True)
                for d in doms]
        pro_path = os.path.join(self.workdir, 'input.kicad_pro')
        data = None
        if os.path.exists(pro_path):
            try:
                with open(pro_path) as fh:
                    data = json.load(fh)
            except Exception:
                data = None
        if not isinstance(data, dict):
            data = {}
        nset = data.setdefault('net_settings', {})
        classes = nset.setdefault('classes', [])
        patterns = nset.setdefault('netclass_patterns', [])
        if not any(isinstance(c, dict) and c.get('name') == 'Default'
                   for c in classes):
            classes.insert(0, dict(_ENG_DEFAULT_CLASS))
        by_name = {c.get('name'): c for c in classes if isinstance(c, dict)}
        applied = {}
        used = set()
        for d, req in zip(doms, reqs):
            if req <= 0:
                continue
            base_name = 'AI-HV-' + (re.sub(r'[^A-Za-z0-9]+', '_',
                                           d['name']).strip('_') or 'DOMAIN')
            name, k = base_name, 2
            while name in used:
                name = '%s_%d' % (base_name, k)
                k += 1
            used.add(name)
            clone = dict(by_name.get('Default') or _ENG_DEFAULT_CLASS)
            clone['name'] = name
            clone['clearance'] = round(req, 4)
            clone['priority'] = 1000
            if name in by_name:
                classes[classes.index(by_name[name])] = clone
            else:
                classes.append(clone)
            by_name[name] = clone
            nets = list(d['nets'])
            patterns[:] = [pp for pp in patterns
                           if not (isinstance(pp, dict)
                                   and pp.get('pattern') in nets)]
            for net in nets:
                patterns.append({'netclass': name, 'pattern': net})
            applied[d['name']] = {'class': name,
                                  'clearance_mm': round(req, 4),
                                  'nets': nets}
        if applied:
            with open(pro_path, 'w') as fh:
                json.dump(data, fh, indent=1)
        return applied

    def _eng_diff_layer(self, pair, cu, ncu):
        # #67 Step 3: edge-coupled pair geometry for one layer.  Surface
        # layers use tlcalc's IPC-2141A / AN-905 differential microstrip
        # form, inner layers the Cohn differential stripline; the
        # single-ended companion is the same layer's Step 1 model.
        # Solder mask is not modelled for pairs in this draft (flagged).
        t = cu.get('thickness_mm') or 0.035
        cls = cu.get('class')
        flags = ['diff_mask_not_modelled'] if cls in ('surface_top', 'surface_bottom') else []
        if cls in ('surface_top', 'surface_bottom'):
            diel = cu.get('diel_below') if cls == 'surface_top' else cu.get('diel_above')
            if not diel or not diel.get('thickness_mm'):
                return {'error': 'no dielectric inside the surface layer'}
            h = diel['thickness_mm']
            er = pair.get('dk') or diel.get('er')
            if not er:
                return {'error': 'dielectric er unknown and no dk in marking'}
            diff_of = lambda w, s: _tlcalc.diff_microstrip(w, s, h, t, er)
            single_of = lambda w: _tlcalc.microstrip(w, h, t, er, f_hz=pair.get('freq_hz') or 0.0)
            model = 'diff_microstrip'
        elif cls == 'inner':
            da, db = cu.get('diel_above'), cu.get('diel_below')
            if (not da or not db or not da.get('thickness_mm')
                    or not db.get('thickness_mm')):
                return {'error': 'inner layer lacks dielectric on both sides'}
            h1 = db['thickness_mm']
            h2 = da['thickness_mm']
            b = h1 + t + h2
            if pair.get('dk'):
                er = pair['dk']
            elif da.get('er') and db.get('er'):
                er = (da['er'] * h2 + db['er'] * h1) / (h1 + h2)
            else:
                er = da.get('er') or db.get('er')
            if not er:
                return {'error': 'dielectric er unknown and no dk in marking'}
            diff_of = lambda w, s: _tlcalc.diff_stripline(w, s, b, t, er)
            single_of = lambda w: _tlcalc.stripline(w, b, t, er, a=h1)
            model = 'diff_stripline'
        else:
            return {'error': 'unknown layer class %r' % (cls,)}
        r = _tlcalc.solve_diff_geometry(diff_of, single_of, pair['zdiff_ohm'],
                                        gap_mm=pair.get('gap_mm'),
                                        width_mm=pair.get('width_mm'))
        if 'error' in r:
            return {'model': model, 'flags': flags, 'error': r['error']}
        r = dict(r)
        r['model'] = model
        r['flags'] = flags + list(r.get('flags') or [])
        return r

    def _eng_patch_pro_diff(self, dsized, su):
        # #67 Step 3: one AI-DIFF-* netclass per pair in the workdir
        # project file, same discipline as _eng_patch_pro: clone of the
        # first leg's current class with track width, diff-pair width
        # and diff-pair gap replaced, and BOTH legs' pattern
        # assignments replaced outright.
        pro_path = os.path.join(self.workdir, 'input.kicad_pro')
        data = None
        if os.path.exists(pro_path):
            try:
                with open(pro_path) as fh:
                    data = json.load(fh)
            except Exception:
                data = None
        if not isinstance(data, dict):
            data = {}
        nset = data.setdefault('net_settings', {})
        classes = nset.setdefault('classes', [])
        patterns = nset.setdefault('netclass_patterns', [])
        if not any(isinstance(c, dict) and c.get('name') == 'Default' for c in classes):
            classes.insert(0, dict(_ENG_DEFAULT_CLASS))
        by_name = {c.get('name'): c for c in classes if isinstance(c, dict)}
        applied = {}
        used = set()
        for pname, rec in dsized.items():
            if not rec.get('width_mm') or not rec.get('gap_mm'):
                continue
            base_name = 'AI-DIFF-' + (re.sub(r'[^A-Za-z0-9]+', '_', pname).strip('_') or 'PAIR')
            name, k = base_name, 2
            while name in used:
                name = '%s_%d' % (base_name, k)
                k += 1
            used.add(name)
            src_name = ((su.get('nets') or {}).get(rec['nets'][0]) or {}).get('netclass')
            clone = dict(by_name.get(src_name) or by_name.get('Default')
                         or _ENG_DEFAULT_CLASS)
            clone['name'] = name
            clone['track_width'] = round(rec['width_mm'], 4)
            clone['diff_pair_width'] = round(rec['width_mm'], 4)
            clone['diff_pair_gap'] = round(rec['gap_mm'], 4)
            clone['priority'] = 1000
            if name in by_name:
                classes[classes.index(by_name[name])] = clone
            else:
                classes.append(clone)
            by_name[name] = clone
            for leg in rec['nets']:
                patterns[:] = [pp for pp in patterns
                               if not (isinstance(pp, dict) and pp.get('pattern') == leg)]
                patterns.append({'netclass': name, 'pattern': leg})
            applied[pname] = name
        with open(pro_path, 'w') as fh:
            json.dump(data, fh, indent=1)
        return applied

    # ---- #67 Step 2: RF checks + treatments (branch-only) ----
    # Annex band conventions: thresholds are evaluated at the band TOP,
    # loss estimates at the band-centre frequency listed here; the
    # return-via ceiling is 2.54 mm up to 3 GHz and 1.5 mm above it
    # (annex GATE-4/GEN-3, from the Keysight via-design note).
    _BAND_TOP_HZ = {'SUB': 1e9, 'MID': 3e9, 'HIGH': 6e9}
    _BAND_MID_HZ = {'SUB': 5e8, 'MID': 2e9, 'HIGH': 4.5e9}
    _BAND_CEILING_MM = {'SUB': 2.54, 'MID': 2.54, 'HIGH': 1.5}

    def _eng_rf_spec(self, board):
        """Build the rfgeom spec + rfcheck contexts for every marked net
        from the markings state and a fresh stackup read of `board`.
        Returns (spec, ctxs).  The reference map mirrors the adjacency
        logic of engineered_report; h is the dielectric thickness
        between the routed layer and that reference."""
        eng = self._eng
        entries = eng.get('entries') or {}
        su = self._worker('stackup', '--board', board,
                          '--nets', ','.join(entries.keys()))
        copper = su.get('copper') or []
        order = [c['name'] for c in copper]
        by_name = {c['name']: c for c in copper}
        spec, ctxs = {'nets': {}}, {}
        for net, entry in entries.items():
            info = (su.get('nets') or {}).get(net) or {}
            routed = sorted((info.get('track_layers') or {}).keys())
            # #145 (B20 row 1): the ground net for this entry --
            # its own declaration, else the board's pour evidence
            # ('GND' if it pours anywhere, else the sole pour net,
            # else 'GND').  Never a bare hardcoded 'GND': on AGND /
            # GND_RF / VSS boards every corridor read as severed.
            all_pours = {pn for c in copper
                         for pn in (c.get('pour_nets') or [])}
            gnd = entry.get('gnd_net') or (
                'GND' if 'GND' in all_pours else
                (next(iter(all_pours)) if len(all_pours) == 1 else 'GND'))
            refs, refs_any = {}, {}
            for lname in routed:
                if lname not in by_name:
                    continue
                k = order.index(lname)
                cls = by_name[lname].get('class')
                cu = by_name[lname]
                cand = []
                if cls == 'surface_top':
                    cand = [(order[k + 1], (cu.get('diel_below') or {}).get('thickness_mm'))] if k + 1 < len(order) else []
                elif cls == 'surface_bottom':
                    cand = [(order[k - 1], (cu.get('diel_above') or {}).get('thickness_mm'))] if k > 0 else []
                else:
                    if k > 0:
                        cand.append((order[k - 1], (cu.get('diel_above') or {}).get('thickness_mm')))
                    if k + 1 < len(order):
                        cand.append((order[k + 1], (cu.get('diel_below') or {}).get('thickness_mm')))
                # #145 (B20 row 3): a neighbouring layer is a
                # reference plane only if it actually carries a
                # pour of the ground net -- a neighbouring SIGNAL
                # layer is not a plane, however close it sits.
                refs[lname] = [{'ref_layer': a, 'h_mm': h}
                               for (a, h) in cand if h
                               and gnd in (by_name[a].get('pour_nets') or [])]
                # The rfcheck reference map (ctx['refs']) is the
                # PHYSICAL plane: the first candidate that pours
                # ANY net.  #145 row 3 still holds -- a layer with
                # no pour is not a plane -- but a layer pouring a
                # DIFFERENT net (+3V3 under B.Cu) is a real plane
                # and exactly what GATE-12 must catch; filtering
                # ctx refs to GND pours made GATE-12 unreachable
                # for the case it exists for.  The corridor spec
                # keeps the GND-only refs above.
                refs_any[lname] = [{'ref_layer': a, 'h_mm': h}
                                   for (a, h) in cand if h
                                   and (by_name[a].get('pour_nets') or [])]
            band = _tlcalc.band_of(entry.get('freq_hz') or 0)
            if band not in self._BAND_TOP_HZ:
                # #145 (B20 row 5): tlcalc's band contract says
                # callers must REFUSE above 6 GHz (and for unknown
                # bands) -- the old .get(band, 3e9) silently sized
                # them against the 3 GHz limits
                spec.setdefault('refused', {})[net] = (
                    'frequency band %r is outside the validated '
                    'range (max 6 GHz); refusing to size' % (band,))
                continue
            sized = (eng.get('nets') or {}).get(net) or {}
            prim = (sized.get('per_layer') or {}).get(sized.get('primary_layer') or '') or {}
            eps_eff = prim.get('eps_eff')
            f_top = self._BAND_TOP_HZ.get(band, 3e9)
            lam_top = _tlcalc.lambda_g_mm(eps_eff, f_top) if eps_eff else None
            lam_f = _tlcalc.lambda_g_mm(eps_eff, entry['freq_hz']) if eps_eff and entry.get('freq_hz') else None
            ceiling = self._BAND_CEILING_MM.get(band, 2.54)
            pi = entry.get('pi') or {}
            pi_refs = list(pi.get('radio') or []) + list(pi.get('antenna') or [])
            spec['nets'][net] = {'gnd_net': gnd, 'ceiling_mm': ceiling,
                                 'refs': refs, 'pi_refs': pi_refs}
            layer_z0 = {}
            for lname, lw in (sized.get('per_layer') or {}).items():
                if not lw.get('width_mm') or lname not in by_name:
                    continue
                lm = self._eng_layer_model(entry, by_name[lname],
                                            len(copper))
                if lm.get('fn'):
                    layer_z0[lname] = lm['fn']
            ctxs[net] = {
                'band': band, 'freq_hz': entry.get('freq_hz'),
                'stub_limit_mm': (lam_top / 20.0) if lam_top else None,
                'return_ceiling_mm': ceiling,
                'width_mm': sized.get('width_mm'),
                'w_over_lambda_g': (round(sized['width_mm'] / lam_f, 4)
                                    if lam_f and sized.get('width_mm') else None),
                'est_loss_db': (round(prim['loss_db_per_mm'] * (info.get('length_mm') or 0.0), 3)
                                if prim.get('loss_db_per_mm') is not None else None),
                'length_mm': round(info.get('length_mm') or 0.0, 2),
                'finish': su.get('copper_finish'), 'eps_eff': eps_eff,
                'gnd_net': gnd,
                # #145 (B20 row 6): the user's loss budget reaches
                # rfcheck (it read ctx['loss_budget_db'] all along;
                # the pipeline never passed it)
                'loss_budget_db': entry.get('loss_budget_db'),
                'refs': {ln: {'ref_layer': rl[0]['ref_layer'],
                              'pour_nets': by_name[rl[0]['ref_layer']].get('pour_nets') or []}
                         for ln, rl in refs_any.items() if rl},
                # #67 tol_pct band (rfcheck GATE-13): the target,
                # the tolerance (marking's own, else the markings
                # default) and the per-layer stackup models.
                'z0_ohm': entry.get('z0_ohm'),
                'tol_pct': entry.get('tol_pct') or
                    (_markings.DEFAULT_TOL_PCT if _markings else 10.0),
                'layer_z0': layer_z0}
        return spec, ctxs

    def _eng_gate_try(self, cmd, args, label, extra_ok=None):
        """Run a worker command on a copy of the current board and adopt
        the copy only if the TREATMENT key does not regress (and an
        optional extra predicate on the after-gate holds). The
        treatment key is _accept_key[:3] -- see _treatment_key (#133)."""
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        kb = _treatment_key(before)
        outp = self._out(label)
        shutil.copy(self.current, outp)
        r = self._worker(cmd, '--board', outp, *args)
        adopted = False
        if os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            ka = _treatment_key(after)
            # #134 (B9): DOMINANCE, not lexicographic order -- a
            # treatment is adopted only if it is no worse in EVERY
            # component (faults, unconnected, splits).  The old
            # lexicographic comparison adopted a treatment that
            # removed one fault while adding unconnected items,
            # and no later stage could recover them (the N17
            # pattern, cf. #58).
            dominated = all(a <= b for a, b in zip(ka, kb))
            bad = not dominated or (extra_ok is not None and not extra_ok(before, after))
            if bad:
                self.current = prev
                self.last_gate = before
                r = dict(r)
                r['status'] = ('reverted_dominance'
                               if dominated is False and ka <= kb
                               else 'reverted_faults')
            else:
                self.last_gate = after
                adopted = True
                if _accept_key(after) > _accept_key(before):
                    # v1.11.5 (#133): adopted on the treatment key
                    # although the FULL key regressed -- that
                    # regression is the stripped cost tail alone,
                    # recorded distinctly as 'reverted_cost' (what
                    # the pre-fix full-key gate would have done),
                    # never conflated with a fault rejection.
                    r = dict(r); r['full_key_status'] = 'reverted_cost'
        r = dict(r); r['adopted'] = adopted
        return r

    def tool_rf_treatments(self):
        # #67 Step 2 treatments: return-path vias for every marked net,
        # then GCPW ground-tying fences for coplanar nets that asked.
        # Each command is admitted as a unit through the gate.
        eng = getattr(self, '_eng', None)
        if not isinstance(eng, dict) or eng.get('error') or _rfcheck is None:
            return {'status': 'skipped'}
        entries = eng.get('entries') or {}
        if not entries:
            return {'status': 'no_markings'}
        spec, ctxs = self._eng_rf_spec(self.current)
        out = {'status': 'ok'}
        vspec = {'nets': {n: {'gnd_net': ctxs[n].get('gnd_net') or 'GND',
                              'ceiling_mm': ctxs[n]['return_ceiling_mm']}
                          for n in spec['nets']}}
        p = os.path.join(self.workdir, 'rf_vias_spec.json')
        with open(p, 'w') as fh:
            json.dump(vspec, fh)
        r = self._eng_gate_try('rf_vias', ['--spec', p], 'rf_vias')
        out['return_vias'] = r
        self._record('rf_vias', {'placed': len(r.get('placed') or []),
                                 'unplaced': r.get('unplaced'),
                                 'adopted': r.get('adopted')})
        self.log('rf_vias: %d placed, %d unplaced, adopted=%s'
                 % (len(r.get('placed') or []), len(r.get('unplaced') or []), r.get('adopted')))
        fence_nets = {}
        for n, e in entries.items():
            if e.get('model') == 'coplanar' and e.get('fence') and ctxs.get(n, {}).get('eps_eff'):
                band = ctxs[n]['band']
                lam = _tlcalc.lambda_g_mm(ctxs[n]['eps_eff'], self._BAND_TOP_HZ.get(band, 3e9))
                if lam:
                    fence_nets[n] = {'gnd_net': ctxs[n].get('gnd_net') or 'GND',
                                     'pitch_mm': round(lam / (10.0 if band == 'HIGH' else 20.0), 3),
                                     'gap_mm': e.get('gap_mm')}
        if fence_nets:
            p = os.path.join(self.workdir, 'rf_fence_spec.json')
            with open(p, 'w') as fh:
                json.dump({'nets': fence_nets}, fh)
            r = self._eng_gate_try('rf_fence', ['--spec', p], 'rf_fence')
            out['fence'] = r
            self._record('rf_fence', {'placed': len(r.get('placed') or []),
                                      'adopted': r.get('adopted')})
            self.log('rf_fence: %d placed, adopted=%s' % (len(r.get('placed') or []), r.get('adopted')))
        return out

    def tool_smooth_marked(self):
        # #67 Step 2 smoothing: one net per gated call; a net is kept
        # only if the acceptance key AND the clearance class both hold.
        eng = getattr(self, '_eng', None)
        if not isinstance(eng, dict) or eng.get('error'):
            return {'status': 'skipped'}
        entries = eng.get('entries') or {}
        targets = [n for n, e in entries.items() if e.get('smooth')]
        if not targets:
            return {'status': 'no_marked_smooth'}
        agg = {'status': 'ok', 'per_net': {}}
        for net in targets:
            def _clr_ok(b4, af):
                return ((af.get('classes') or {}).get('clearance', 0)
                        <= (b4.get('classes') or {}).get('clearance', 0))
            r = self._eng_gate_try('smooth', ['--net', net], 'smooth', extra_ok=_clr_ok)
            agg['per_net'][net] = r
            self._record('rf_smooth', {'net': net,
                                       'smoothed': len(r.get('smoothed') or []),
                                       'skipped': len(r.get('skipped') or []),
                                       'adopted': r.get('adopted')})
            self.log('smooth[%s]: %d joints, adopted=%s'
                     % (net, len(r.get('smoothed') or []), r.get('adopted')))
        return agg

    def tool_placement_audit(self):
        # #42: pre-routing placement audit + library-rule
        # materialisation.  Runs on the working copy after strip,
        # before any route pass.  The audit never moves a part; it
        # reports placement facts.  v1.11.5 (#132): REPORT-ONLY by
        # default -- library rule-area zones are written (through
        # the gate, with provenance in the ledger) and first_nets
        # advice for RF-class parts is contributed ONLY when a
        # markings sidecar exists or placement_audit == 'apply'.
        if _placement_audit is None or _library_rules is None:
            return {'status': 'unavailable'}
        if not self.cfg.get('placement_audit', True):
            return {'status': 'disabled'}
        facts = self._worker('auditgeom', '--board', self.current) or {}
        libs = {}
        try:
            libs = _library_rules.load_library_rules(self.board_path)
        except Exception as e:
            self.log('placement audit: library tables unreadable: %s' % e)
        library = {}
        for fp in facts.get('footprints') or []:
            fpid = fp.get('fpid')
            if not fpid or fpid in library:
                continue
            p = _library_rules.library_footprint_path(libs, fpid)
            if p:
                try:
                    library[fpid] = _library_rules.parse_footprint_rules(open(p, encoding='utf-8').read())  # v1.11.5 (#132): utf-8, as the tables
                except Exception:
                    library[fpid] = None
            else:
                library[fpid] = None
        rep = _placement_audit.audit(facts, library)
        rep['status'] = 'ok'
        self._audit = rep
        # v1.11.5 (#132): REPORT-ONLY by default. The audit runs on
        # every board and its report is always recorded, but writing
        # zones into the base and reordering first_nets are side
        # effects an unmarked board never asked for: they apply only
        # when a markings sidecar exists for the board, or the
        # placement_audit setting explicitly says 'apply'.
        apply = (self.cfg.get('placement_audit') == 'apply')
        if not apply and _markings is not None                 and self.cfg.get('engineered_nets', True):
            # #203: sidecar PRESENCE alone must not apply -- with
            # the engineered_nets switch off the audit stays
            # report-only on local runs, exactly as on backend
            # runs (where the gated upload means no sidecar is
            # present).  The explicit placement_audit == 'apply'
            # opt-in above stays independent of the switch.
            try:
                apply = _markings.load_markings(self.board_path) is not None
            except Exception:
                apply = False
        self._audit_apply = bool(apply)
        rep['applied'] = self._audit_apply
        rep['materialised'] = []
        if rep.get('materialise') and self._audit_apply:
            p = os.path.join(self.workdir, 'audit_zones.json')
            with open(p, 'w') as fh:
                json.dump(rep['materialise'], fh)
            # Through the gate, like every other board mutation: the
            # zones land on a candidate and are adopted only if the
            # acceptance key does not regress.
            r = self._eng_gate_try('audit_zones', ['--spec', p], 'audit_zones')
            if r.get('adopted'):
                rep['materialised'] = r.get('added') or []
                for m in rep['materialise']:
                    self._record('placement_materialise',
                                 {'ref': m['ref'], 'provenance': m['provenance'],
                                  'layers': m['layers'], 'keepout': m['keepout']})
                self.log('placement audit: materialised %d library rule zones (gate-adopted)'
                         % len(rep['materialised']))
            else:
                self.log('placement audit: zone materialisation reverted by the gate (%s)'
                         % r.get('status'))
        elif rep.get('materialise'):
            self.log('placement audit: %d library rule zones reported only '
                     '(no markings, placement_audit not "apply")' % len(rep['materialise']))
        bad = sum(1 for it in rep['footprints'].values()
                  for c in it['checks'] if c.get('ok') is False
                  or c['kind'] == 'keepout_intrusion')
        self._record('placement_audit',
                     {'footprints': len(rep['footprints']),
                      'failed_checks': bad,
                      'applied': self._audit_apply,
                      'gaps': [g['ref'] for g in rep['gaps']],
                      'first_nets_extra': rep['first_nets_extra']})
        self.log('placement audit: %d footprints, %d failed checks, %d library gaps'
                 % (len(rep['footprints']), bad, len(rep['gaps'])))
        return rep

    def _eng_layer_delay_info(self):
        su = self._worker('stackup', '--board', self.current)
        layers = {}
        for c in ((su or {}).get('copper') or []):
            cls = c.get('class')
            diel = (c.get('diel_below') if cls != 'surface_bottom'
                    else c.get('diel_above')) or c.get('diel_above') or c.get('diel_below') or {}
            # #152 (B23): key by the BOARD's layer name, as #84 did
            # for the power stage -- routed segments carry board
            # names, so canonical keys silently missed renamed
            # layers and member_stats fell back to er 4.5 stripline.
            layers[c.get('board_name') or c['name']] = {'class': cls,
                                 'er': diel.get('er') or 4.5,
                                 'h_mm': diel.get('thickness_mm') or 0.2,
                                 'thickness_mm': c.get('thickness_mm') or 0.035}
        return layers

    def _eng_member_stats(self, nets, layers):
        facts = self._worker('powergeom', '--board', self.current,
                             '--nets', ','.join(nets)) or {}
        out = {}
        for net in nets:
            segs = []
            for t in (facts.get(net) or {}).get('tracks', []):
                if t.get('arc'):
                    segs.append({'layer': t['layer'], 'length_mm': t['length_mm'],
                                 'width_mm': t['w']})
                else:
                    import math as _m
                    segs.append({'layer': t['layer'],
                                 'length_mm': _m.hypot(t['x2'] - t['x1'], t['y2'] - t['y1']),
                                 'width_mm': t['w']})
            out[net] = _lengthmatch.member_stats(segs, layers)
        return out

    def tool_length_match(self):
        # #69: length-matched groups -- evaluate, meander the short
        # members through the exact-checker gate, re-evaluate, and
        # report per member with shortfalls stated.
        if _lengthmatch is None:
            return {'status': 'unavailable'}
        try:
            mk = _markings.load_markings(self.board_path)
        except Exception as e:
            return {'status': 'markings_error', 'error': str(e)}
        if mk is None or not mk.length_groups:
            return {'status': 'no_length_groups'}
        layers = self._eng_layer_delay_info()
        diff_by_name = {p['name']: p for p in mk.differential}
        groups = []
        for entry in mk.length_groups:
            nets = entry.get('nets')
            if not nets and entry.get('pair'):
                pair = diff_by_name.get(entry['pair'])
                if pair is None:
                    groups.append({'name': entry['name'], 'error':
                                   'pair %r not in differential markings' % entry['pair']})
                    continue
                nets = pair['nets']
            stats = self._eng_member_stats(nets, layers)
            ev = _lengthmatch.evaluate_group(entry, stats)
            ev['assumptions'] = [
                'delay from geometry + stackup eps_eff (microstrip form on '
                'surface layers, eps_r on inner layers); no field solver',
                'meander self-coupling is not modelled',
                'via barrel length is not counted in member length']
            for net, m in ev['members'].items():
                if m['within_tolerance']:
                    continue
                # #99: the need comes from whichever criteria
                # actually FAIL.  #152 (B23): it now aims at the
                # CENTRE of the tolerance band (lengthmatch.
                # meander_need), not the edge -- edge-aimed tuning
                # could round to just outside the band.
                st = stats[net]
                rate = (st['delay_ps'] / st['length_mm']) if st['length_mm'] else 6.0
                need = _lengthmatch.meander_need(m, entry, rate)
                if need is None or need <= 0:
                    continue
                r = self._eng_gate_try('meander', ['--net', net, '--add-mm', '%.3f' % need],
                                       'meander')
                m['meander'] = r
                self._record('length_meander', {'group': entry['name'], 'net': net,
                                                'requested_mm': round(need, 3),
                                                'adopted': r.get('adopted')})
                self.log('meander[%s/%s]: +%.2f mm requested, adopted=%s'
                         % (entry['name'], net, need, r.get('adopted')))
            stats2 = self._eng_member_stats(nets, layers)
            ev['after'] = _lengthmatch.evaluate_group(entry, stats2)['members']
            for net, m in ev['members'].items():
                m['after'] = ev['after'].get(net)
                if m['after'] and not m['after']['within_tolerance']:
                    m['failed'] = True
                    m['shortfall_mm'] = m['after']['delta_mm']
                    m['shortfall_ps'] = m['after']['delta_ps']
            groups.append(ev)
        self._eng_length_plan = {'groups': groups}
        self._record('length_match', {'groups': [g['name'] for g in groups]})
        return {'status': 'ok', 'groups': len(groups)}

    def _eng_power_netlist(self):
        # #68 Step 2: the schematic netlist joins the run inputs.
        # Order: explicit config path, a sibling <board>.net export,
        # then kicad-cli export from a sibling .kicad_sch.  Returns a
        # path or None (the power stage then reports unavailable --
        # it never guesses a tree).
        p = self.cfg.get('power_netlist')
        if p and os.path.exists(p):
            return p
        net = naming.project_sidecar_path(self.board_path, '.net')
        if os.path.exists(net):
            return net
        sch = naming.project_sidecar_path(self.board_path, '.kicad_sch')
        cli = shutil.which('kicad-cli')
        if os.path.exists(sch) and cli:
            out = os.path.join(self.workdir, 'power_netlist.net')
            try:
                subprocess.run([cli, 'sch', 'export', 'netlist', '--format',
                                'kicadsexpr', '-o', out, sch],
                               capture_output=True, timeout=180)
            except Exception as e:
                self.log('power: netlist export failed: %s' % e)
                return None
            if os.path.exists(out):
                return out
        return None

    def tool_power(self):
        # #68 Step 2: per-segment WCCA sizing on the routed board.
        # Plan first (pure), then widen-only + via arrays, each
        # admitted through the exact checker like every other
        # engineered stage; the plan is kept for the report.
        if _powertree is None or _powerplan is None:
            return {'status': 'unavailable'}
        try:
            mk = _markings.load_markings(self.board_path)
        except Exception as e:
            return {'status': 'markings_error', 'error': str(e)}
        if mk is None or not mk.power:
            return {'status': 'no_power_markings'}
        marked_nets = [e.get('net') for e in mk.power]
        netlist = self._eng_power_netlist()
        if netlist is None:
            self._eng_power_plan = {'status': 'netlist_unavailable',
                                    'marked_nets': marked_nets,
                                    'note': 'no schematic netlist found beside '
                                            'the board and kicad-cli export '
                                            'unavailable; power analysis skipped'}
            self._record('power', {'status': 'netlist_unavailable'})
            return self._eng_power_plan
        try:
            parsed = _powertree.parse_netlist(open(netlist).read())
            entries = mk.power
            resolutions = []
            if _kbclient is not None and any(
                    'kb' in s for e in entries
                    for s in (e.get('loads') or {}).values()):
                cache_path = self.cfg.get('kb_cache') or (
                    os.path.splitext(self.board_path)[0] + '.ai-router-kb-cache.json')
                cache = _kbclient.KbCache(cache_path)
                kb = None
                if self.cfg.get('kb_endpoint'):
                    try:
                        kb = _kbclient.HttpKb(self.cfg['kb_endpoint'])
                    except Exception as e:
                        self.log('power: KB endpoint unusable: %s' % e)
                entries, resolutions = _kbclient.resolve_loads(entries, cache, kb)
            tree = _powertree.compute_currents(
                _powertree.build_tree(parsed, entries))
        except Exception as e:
            self._eng_power_plan = {'status': 'tree_failed', 'error': str(e),
                                    'marked_nets': marked_nets}
            self._record('power', {'status': 'tree_failed', 'error': str(e)})
            return self._eng_power_plan
        su = self._worker('stackup', '--board', self.current)
        if not (su or {}).get('copper'):
            # #136 (B11): the board file carries no stackup -- say
            # so and skip, instead of sizing against an empty layer
            # set and dying downstream with KeyError 'F.Cu'.
            self._eng_power_plan = {
                'status': 'needs_stackup',
                'marked_nets': marked_nets,
                'note': 'board file carries no stackup (worker '
                        'reasons: %s); power sizing skipped rather '
                        'than sized against an invented stackup'
                        % ((su or {}).get('reasons') or 'no copper layers')}
            self._record('power', {'status': 'needs_stackup'})
            return self._eng_power_plan
        layers = {}
        cu_total = 0.0
        for c in ((su or {}).get('copper') or []):
            t = c.get('thickness_mm') or 0.035
            # #84: key by the BOARD's layer name -- cmd_powergeom
            # facts (and _build_graph's pad attachment) use board
            # names, so canonical keys (In1.Cu for a layer the board
            # calls GND) silently detached every pad on that layer.
            layers[c.get('board_name') or c['name']] = {'type': 'copper', 'thickness_mm': t}
            cu_total += t
        bt = (su or {}).get('board_thickness_mm') or 0.0
        if bt > cu_total:
            layers['__dielectric__'] = {'type': 'dielectric',
                                        'thickness_mm': bt - cu_total}
        corners = dict(_wcca.DEFAULT_CORNERS)
        corners.update(mk.power_corners())
        nets_wanted = sorted(set(tree['rails']) | {tree['return_net']})
        facts = self._worker('powergeom', '--board', self.current,
                             '--nets', ','.join(nets_wanted)) or {}
        entries_by_net = {e['net']: e for e in entries}
        plan = _powerplan.plan_power(tree, parsed, facts, layers, corners,
                                     entries_by_net)
        plan['status'] = 'ok'
        plan['netlist'] = os.path.basename(netlist)
        if resolutions:
            plan['kb_resolutions'] = resolutions
        self._eng_power_plan = plan
        applied = {}
        if plan.get('widen'):
            p = os.path.join(self.workdir, 'power_widen.json')
            with open(p, 'w') as fh:
                json.dump(plan['widen'], fh)
            r = self._eng_gate_try('power_widen', ['--spec', p], 'power_widen')
            applied['widen'] = r
            self._record('power_widen', {'applied': len(r.get('applied') or []),
                                         'adopted': r.get('adopted')})
            self.log('power_widen: %d applied, adopted=%s'
                     % (len(r.get('applied') or []), r.get('adopted')))
        if plan.get('via_adds'):
            p = os.path.join(self.workdir, 'power_vias.json')
            with open(p, 'w') as fh:
                json.dump(plan['via_adds'], fh)
            r = self._eng_gate_try('power_vias', ['--spec', p], 'power_vias')
            applied['vias'] = r
            self._record('power_vias', {'placed': len(r.get('placed') or []),
                                        'adopted': r.get('adopted')})
            self.log('power_vias: %d placed, adopted=%s'
                     % (len(r.get('placed') or []), r.get('adopted')))
        plan['applied'] = applied
        # #136 (B11): the plan was stored before the gates ran --
        # mark every widened segment with its gate outcome so the
        # report shows adopted vs reverted per segment, not only
        # in aggregate under 'applied'.
        _outcome = ('adopted' if (applied.get('widen') or {}).get('adopted')
                    else 'reverted')
        for _net, _uuids in (plan.get('widen') or {}).items():
            for _seg in (plan['rails'].get(_net) or {}).get('segments', []):
                if _seg.get('uuid') in _uuids:
                    _seg['widen_outcome'] = _outcome
        self._record('power', {'status': 'ok',
                               'rails': sorted(plan['rails']),
                               'widen_segments': sum(len(v) for v in plan['widen'].values()),
                               'via_adds': len(plan['via_adds'])})
        return {'status': 'ok', 'applied': applied}

    @staticmethod
    def _pad_shape_items(p):
        """hvdomains items for one pad's REAL shape (#146 (B21)).
        Facts exported before the SDF fields existed fall back to
        the old inscribed disc.  Rects become their four edges
        (corner radius ignored -- the rect overstates the copper,
        the safe direction for a minimum-gap check); ovals become
        their capsule spine."""
        import math
        kind = p.get('skind')
        if not kind:
            return [('disc', p['x'], p['y'], max(p['w'], p['h']) / 2.0)]
        x, y, sx, sy, rot = p['x'], p['y'], p['sx'], p['sy'], p.get('rot', 0.0)
        if kind == 'disc':
            return [('disc', x, y, sx)]
        if kind == 'oval':
            r = min(sx, sy)
            d = abs(sx - sy)
            if sx >= sy:
                ux, uy = math.cos(rot), math.sin(rot)
            else:
                ux, uy = -math.sin(rot), math.cos(rot)
            return [('seg', x - d * ux, y - d * uy,
                     x + d * ux, y + d * uy, r)]
        cs = []
        for (lx, ly) in ((sx, sy), (sx, -sy), (-sx, -sy), (-sx, sy)):
            cs.append((x + lx * math.cos(rot) - ly * math.sin(rot),
                       y + lx * math.sin(rot) + ly * math.cos(rot)))
        return [('seg', cs[i][0], cs[i][1], cs[(i + 1) % 4][0],
                 cs[(i + 1) % 4][1], 0.0) for i in range(4)]

    def _hv_items(self, facts, net, layer):
        """Copper items of one net on one layer, hvdomains shapes."""
        items, filled = [], []
        nf = facts.get('nets', {}).get(net)
        if not nf:
            return items, filled
        for t in nf.get('tracks', []):
            if t['layer'] == layer:
                items.append(('seg', t['x1'], t['y1'], t['x2'], t['y2'],
                              t['w'] / 2.0))
        for p in nf.get('pads', []):
            if layer in (p.get('layers') or []):
                items += self._pad_shape_items(p)
        for v in nf.get('vias', []):
            items.append(('disc', v['x'], v['y'], v['d'] / 2.0))
        for z in nf.get('zones', []):
            if z['layer'] == layer:
                pts = [tuple(q) for q in z['pts']]
                filled.append(pts)
                for i in range(len(pts)):
                    x1, y1 = pts[i]
                    x2, y2 = pts[(i + 1) % len(pts)]
                    items.append(('seg', x1, y1, x2, y2, 0.0))
        return items, filled

    def _hv_pad_items(self, facts, net, layer):
        """Pad geometry of one net on one layer (terminations,
        for the #127 A6/A7 rule; real shapes since #146)."""
        nf = facts.get('nets', {}).get(net)
        if not nf:
            return []
        out = []
        for p in nf.get('pads', []):
            if layer in (p.get('layers') or []):
                out += self._pad_shape_items(p)
        return out

    def tool_hv_domains(self):
        """#70: per domain pair, required (IPC-2221B Table 6-1) vs
        measured clearance and surface creepage, with the limiting
        geometry located.  Report-only: never moves copper."""
        if _hvdomains is None:
            return None
        try:
            mk = _markings.load_markings(self.board_path)
        except Exception as e:
            return {'error': 'markings_error: %s' % e}
        if mk is None or not mk.hv_domains:
            return None
        H = _hvdomains
        facts = self._worker('hvgeom', '--board', self.current)
        if not facts:
            return {'error': 'hvgeom_failed'}
        layers = facts.get('layers') or []
        if not layers:
            return {'error': 'no_copper_layers'}
        surface = {layers[0], layers[-1]}
        outline = [tuple(p) for p in (facts.get('outline') or [])]
        cutouts = [[tuple(p) for p in c]
                   for c in (facts.get('cutouts') or [])]
        doms = mk.hv_domains
        # #146 (B21): verify every declared net exists -- a
        # mistyped name used to pass silently with zero pairs.
        all_nets = set(facts.get('nets', {}))
        missing_nets = [{'domain': d['name'], 'net': n}
                        for d in doms for n in d['nets']
                        if n not in all_nets]
        # #146 (B21): copper outside every declared domain is a
        # domain too -- the implicit rest-of-board at 0 V.  Without
        # it, declaring just the HV net (the natural minimal
        # marking) checked nothing at all.
        declared = {n for d in doms for n in d['nets']}
        rest_nets = sorted(all_nets - declared)
        doms_ext = list(doms)
        if rest_nets:
            doms_ext.append({'name': '(rest of board)', 'nets': rest_nets,
                             'voltage_v': 0.0, 'peak_v': 0.0,
                             'coating': 'uncoated', 'altitude_m': 0,
                             'implicit': True})
        items, filled, blockers = {}, {}, {}
        for layer in layers:
            all_items, all_filled = [], []
            for net in facts.get('nets', {}):
                it, fi = self._hv_items(facts, net, layer)
                items[(net, layer)] = it
                filled[(net, layer)] = fi
                all_items += it
                all_filled += fi
            blockers[layer] = (all_items, all_filled)
        pairs = []
        for i in range(len(doms_ext)):
            for j in range(i + 1, len(doms_ext)):
                da, db = doms_ext[i], doms_ext[j]
                dv = abs(da['voltage_v'] - db['voltage_v'])
                pair = {'a': da['name'], 'b': db['name'],
                        'delta_v': dv, 'layers': [], 'violations': []}
                if dv <= 0:
                    pair['note'] = ('same declared potential; no spacing '
                                    'requirement')
                    pairs.append(pair)
                    continue
                for layer in layers:
                    surf = layer in surface
                    ia = [it for n in da['nets']
                          for it in items.get((n, layer), [])]
                    ib = [it for n in db['nets']
                          for it in items.get((n, layer), [])]
                    if not ia or not ib:
                        continue
                    gap, pa, pb = H.min_clearance(ia, ib)
                    # #127 (B1): does a termination (pad) achieve
                    # the closest approach?  Then the A6/A7
                    # termination column applies alongside the
                    # conductor column (pads are openings in
                    # solder mask -- never B4).
                    term = False
                    if surf:
                        pads_a = [it for n in da['nets'] for it in
                                  self._hv_pad_items(facts, n, layer)]
                        pads_b = [it for n in db['nets'] for it in
                                  self._hv_pad_items(facts, n, layer)]
                        cand = []
                        if pads_a:
                            cand.append(H.min_clearance(pads_a, ib)[0])
                        if pads_b:
                            cand.append(H.min_clearance(ia, pads_b)[0])
                        term = bool(cand) and abs(min(cand) - gap) < 1e-9
                    # #200: the shared symmetric primitive --
                    # both sides evaluated, the larger governs
                    # (identical semantics to the open-coded pair
                    # this replaces; the materialise paths now
                    # use the same primitive via
                    # _hv_worst_pair_req_mm).
                    req, col, cond_col = H.pair_required_symmetric_mm(
                        dv, surf, da, db, term)
                    entry = {'layer': layer, 'column': col,
                             'conductor_column': cond_col,
                             'termination': term,
                             'required_mm': round(req, 3)}
                    if dv > 500:
                        # #128 (B2): disclose the whole-voltage
                        # reading alongside the primary one
                        alt = H.pair_required_alt_symmetric_mm(
                            dv, surf, da, db, term)
                        entry['required_mm_whole_voltage_reading'] = round(alt, 3)
                    entry['clearance_mm'] = round(gap, 3)
                    entry['clearance_at'] = [round(pa[0], 2),
                                             round(pa[1], 2)]
                    if gap < req - 1e-9:
                        pair['violations'].append(
                            {'kind': 'clearance', 'layer': layer,
                             'required_mm': round(req, 3),
                             'actual_mm': round(gap, 3),
                             'at': entry['clearance_at']})
                    if surf:
                        bl, bf = blockers[layer]
                        cr, cpitch = H.creepage_path(
                            ia, ib, bl, outline, cutouts,
                            pitch=0.25, filled=bf)
                        entry['creepage_pitch_mm'] = cpitch
                        if cr is None:
                            # #146 (B21): unmeasurable is NOT a
                            # pass -- reported as not verified.
                            entry['creepage_mm'] = None
                            entry['creepage_status'] = 'not_verified'
                        else:
                            entry['creepage_mm'] = round(cr, 2)
                            entry['creepage_status'] = 'ok'
                            # one-pitch margin: the grid result
                            # is only as fine as its pitch
                            if cr < req + cpitch - 1e-9:
                                entry['creepage_status'] = 'violation'
                                pair['violations'].append(
                                    {'kind': 'creepage', 'layer': layer,
                                     'required_mm': round(req, 3),
                                     'actual_mm': round(cr, 2),
                                     'pitch_margin_mm': cpitch,
                                     'at': entry['clearance_at']})
                    pair['layers'].append(entry)
                if pair['delta_v'] > 0 and not pair['layers']:
                    pair['note'] = ('no copper found for one or both '
                                    'domains on any layer -- check '
                                    'the net names')
                pairs.append(pair)
        rep = {
            'standard': 'IPC-2221B Table 6-1 (single spacing table '
                        'applied to both clearance and creepage); '
                        'above-500 V reading: 500 V band value + '
                        'excess x column rate (para. 6.3 form, '
                        'IPC-A-610 worked example), with the '
                        'whole-voltage reading shown per pair; '
                        'IPC-2221C (Dec 2023) supersedes B -- '
                        'values not re-verified against C (#128)',
            'assumptions': [
                {'domain': d['name'], 'peak_v': d.get('peak_v', d['voltage_v']),
                 'ac': bool(d.get('ac')),
                 'coating': d.get('coating', 'uncoated'),
                 'altitude_m': d.get('altitude_m', 0),
                 'pollution_degree': d.get('pollution_degree'),
                 'pollution_degree_applied': False,
                 'material_group': d.get('material_group'),
                 'implicit': bool(d.get('implicit'))}
                for d in doms_ext],
            'missing_nets': missing_nets,
            'creepage_grid_pitch_mm': max(
                [e['creepage_pitch_mm'] for pr in pairs
                 for e in pr['layers'] if 'creepage_pitch_mm' in e]
                or [0.25]),
            'not_verified': sum(
                1 for pr in pairs for e in pr['layers']
                if e.get('creepage_status') == 'not_verified'),
            'unmodelled': ['under-component creepage paths (no body data)',
                           'cross-layer creepage',
                           'clearance to enclosure/chassis',
                           'IEC 62368-1 creepage tables (see the research '
                           'annex on issue #70)'],
            'pairs': pairs,
            'enforced_netclasses': getattr(self, '_eng_hv_classes', None),
        }
        # #146 (B21): an evaluation that checked nothing, or could
        # not measure, is an error state -- never a silent pass.
        evaluated = [pr for pr in pairs if pr['layers']]
        if missing_nets:
            rep['status'] = 'error'
            rep['error'] = ('declared domain nets not found on the '
                            'board: %s' % ', '.join(
                                '%s:%s' % (m['domain'], m['net'])
                                for m in missing_nets))
        elif not evaluated and any(pr['delta_v'] > 0 for pr in pairs):
            rep['status'] = 'error'
            rep['error'] = 'no domain pair could be evaluated'
        elif any(pr['violations'] for pr in pairs):
            rep['status'] = 'violations'
        elif rep['not_verified']:
            rep['status'] = 'not_verified'
        else:
            rep['status'] = 'ok'
        self._eng_hv_report = rep
        self.log('HV domains: %d pair(s), %d violation(s)' % (
            len(pairs), sum(len(p['violations']) for p in pairs)))
        return rep

    def _domain_setup(self, mk):
        # #80 (supersedes the #70/#71 materialise step): markings
        # flagged materialise used to be written onto the board as
        # solid rule areas around the marked nets' own pads -- but
        # KiCad rule areas are not net-selective, so the areas sealed
        # the pads and the marked nets were unroutable.  The same
        # geometry is now computed as rects and handed to the
        # finisher/repair legs, whose Grid stamps them for every
        # routed net except the entry's own/allowed nets
        # (worker.Grid.build).  The TraceMaker passes no longer see
        # rule areas; the post-route hv/quiet checkers remain the
        # authoritative gate for anything the engine leaves behind.
        if _hvdomains is None and _quietnets is None:
            return None
        facts = self._worker('hvgeom', '--board', self.current)
        if not facts:
            return None
        layers = facts.get('layers') or []
        if not layers:
            return None
        entries = []
        if _hvdomains is not None:
            H = _hvdomains
            for d in [d for d in mk.hv_domains if d.get('materialise')]:
                # #200: the rects are stamped around PADS, so the
                # requirement is the shared worst-pair value WITH
                # termination columns and WITH the implicit 0 V
                # rest-of-board domain -- the old loop maximised
                # over declared domains only, with the conductor
                # column (B2), so a single materialise domain got
                # req 0 (zero keepouts) and every other board got
                # a radius understated by the B2-vs-A6 difference.
                req = _hv_worst_pair_req_mm(d, mk.hv_domains,
                                            terminations=True)
                if req <= 0:
                    continue
                rects = []
                for net in d['nets']:
                    nf = facts.get('nets', {}).get(net) or {}
                    for p in nf.get('pads', []):
                        r = max(p['w'], p['h']) / 2.0 + req
                        x, y = p['x'], p['y']
                        rects.append({'rect': [x - r, y - r, x + r, y + r],
                                      'layers': p.get('layers') or [layers[0]]})
                if rects:
                    entries.append({'kind': 'hv', 'name': d['name'],
                                    'nets': list(d['nets']),
                                    'rects': rects})
        if _quietnets is not None:
            pads_by_net, pads_by_ref = {}, {}
            for net, nf in (facts.get('nets') or {}).items():
                for p in nf.get('pads', []):
                    p = dict(p, net=net)
                    pads_by_net.setdefault(net, []).append(p)
                    pads_by_ref.setdefault(p.get('ref'), []).append(p)
            for e in [e for e in mk.quiet if e.get('materialise')]:
                cfg = _quietnets.resolve(e)
                iso = cfg['isolation_mm']
                raw = []
                if e.get('ref'):
                    ps = pads_by_ref.get(e['ref']) or []
                    nets = {p['net'] for p in ps if p['net']}
                    if ps:
                        r = max(max(p['w'], p['h']) for p in ps) / 2.0
                        raw.append([min(p['x'] for p in ps) - r - iso,
                                    min(p['y'] for p in ps) - r - iso,
                                    max(p['x'] for p in ps) + r + iso,
                                    max(p['y'] for p in ps) + r + iso])
                else:
                    nets = {e['net']}
                for n in nets:
                    for p in pads_by_net.get(n, []):
                        r = max(p['w'], p['h']) / 2.0 + iso
                        raw.append([p['x'] - r, p['y'] - r,
                                    p['x'] + r, p['y'] + r])
                allowed = set(nets) | set(cfg['allow_nets'] or [])
                if cfg.get('guard_net'):
                    allowed.add(cfg['guard_net'])
                if raw:
                    entries.append({'kind': 'quiet', 'name': cfg['name'],
                                    'nets': sorted(allowed),
                                    'rects': [{'rect': r, 'layers': layers}
                                              for r in raw]})
        if not entries:
            return None
        sp = os.path.join(self.workdir, 'domains.json')
        with open(sp, 'w') as f:
            json.dump(entries, f)
        self._domains_path = sp
        out = {'entries': len(entries),
               'rects': sum(len(e['rects']) for e in entries),
               'note': 'net-selective Grid enforcement (#80): no rule '
                       'areas written; post-route hv/quiet checkers '
                       'remain the gate'}
        self.ledger.append({'tool': 'domain_enforce', **out})
        self.log('domain enforcement: %d entr(ies), %d rect(s) -- '
                 'finisher/repair legs only, no rule areas'
                 % (out['entries'], out['rects']))
        return out

    def _quiet_ctx(self, facts):
        layers = facts.get('layers') or []
        items, zones, pads, vias = {}, {}, [], []
        for net, nf in (facts.get('nets') or {}).items():
            for layer in layers:
                it, fi = self._hv_items(facts, net, layer)
                if it:
                    items[(net, layer)] = it
                if fi:
                    zones[(net, layer)] = fi
            for p in nf.get('pads', []):
                pads.append({'ref': p.get('ref'), 'net': net,
                             'x': p['x'], 'y': p['y'], 'w': p['w'],
                             'h': p['h'], 'layers': p.get('layers') or []})
            for v in nf.get('vias', []):
                vias.append({'net': net, 'x': v['x'], 'y': v['y'],
                             'd': v['d']})
        return {'layers': layers, 'items': items, 'pads': pads,
                'vias': vias, 'zones': zones}

    def tool_quiet_nets(self):
        """#71: isolation gates for quiet markings -- intrusions
        with locations, load-cap ground returns, guard continuity.
        Report-only: never moves copper."""
        if _quietnets is None or _hvdomains is None:
            return None
        try:
            mk = _markings.load_markings(self.board_path)
        except Exception as e:
            return {'error': 'markings_error: %s' % e}
        if mk is None or not mk.quiet:
            return None
        facts = self._worker('hvgeom', '--board', self.current)
        if not facts:
            return {'error': 'hvgeom_failed'}
        ctx = self._quiet_ctx(facts)
        # #147 (B22): supply nets from the power section, so a
        # component entry's quiet set is its signal nets only.
        ctx['power_nets'] = [e['net'] for e in mk.power if e.get('net')]
        results = []
        for e in mk.quiet:
            r = _quietnets.evaluate(e, ctx)
            # #219: intent is not enforcement -- stamp whether this
            # entry's keepout actually landed in the domains.json
            # the finisher legs read (written by _domain_setup at
            # construction when materialise entries produced rects).
            r['enforced_in_domains'] = bool(
                getattr(self, '_domains_path', None) and e.get('materialise'))
            results.append(r)
        rep = {'limitation': 'geometric isolation only -- no coupling '
                             'simulation, no dB claims',
               'results': results}
        self._eng_quiet_report = rep
        self.log('quiet nets: %d entrie(s), %d intrusion(s)' % (
            len(results), sum(len(r['intrusions']) for r in results)))
        return rep

    def engineered_report(self, final_board):
        # #67 Step 1 per-net verification report.  Verifies geometry and
        # stackup math only -- it does not predict S11 (annex VER-2).
        eng = getattr(self, '_eng', None)
        if not isinstance(eng, dict):
            return None
        if eng.get('error'):
            return {'error': eng['error']}
        rep = {'scope': 'Verifies geometry and stackup math only; '
                        'it does not predict S11.',
               'stackup_defined': eng.get('stackup_defined'),
               'stackup_placeholder': eng.get('stackup_placeholder'),
               'placeholder_reasons': eng.get('placeholder_reasons') or [],
               'fab_notes': [
                   'Request an impedance test coupon per panel '
                   '(IPC-TM-650 2.5.5.7) and the fab as-built stackup '
                   'calculation for every marked net.',
                   'Dk basis is recorded per net; widths are solved with '
                   'Dk at the operating frequency (annex CALC-3) -- a '
                   '1 MHz Dk on a GHz design invalidates the width.',
                   'Widths assume the solder-mask state recorded per net; '
                   'ENIG above 1 GHz adds loss the calculator does not '
                   'model (annex WARN-2).',
                   'A coupon verifies the fab process on coupon geometry; '
                   'it is not a measurement of the routed feed itself.'],
               'nets': {}}
        rep['prefab_checklist'] = [
            'Disposition every GATE failure in the per-net checks before '
            'releasing the design to fab; corridor severances (GATE-1) and '
            'return-via gaps (GATE-4/5) are layout defects - fix them in '
            'the layout, not in the markings.',
            'Warnings (WARN-*) are recorded dispositions: keep this report '
            'with the fab package.',
            'Archive the markings sidecar (%s) with the design: it is the '
            'source of the RF intent these checks verify.' % eng.get('markings_file')]
        treats = [e2.get('data') for e2 in self.ledger
                  if e2.get('step') in ('rf_vias', 'rf_fence', 'rf_smooth')]
        if treats:
            rep['treatments'] = treats
        plan = getattr(self, '_eng_power_plan', None)
        if plan is not None:
            rep['power'] = plan
        audit = getattr(self, '_audit', None)
        if audit is not None:
            rep['placement_audit'] = audit
        qrep = getattr(self, '_eng_quiet_report', None)
        if qrep is not None:
            rep['quiet_nets'] = qrep
        hvrep = getattr(self, '_eng_hv_report', None)
        if hvrep is not None:
            rep['hv_domains'] = hvrep
        lplan = getattr(self, '_eng_length_plan', None)
        if lplan is not None:
            rep['length_groups'] = lplan
        nets = list((eng.get('nets') or {}).keys())
        if not nets and not (eng.get('diff') or {}):
            if plan is None:
                rep['note'] = 'sidecar present, no impedance markings'
            return rep
        all_marked = nets + [l for r in (eng.get('diff') or {}).values()
                             for l in r['nets'] if l not in nets]
        checks = {}
        if _rfcheck is not None and (eng.get('entries') or {}):
            try:
                spec, ctxs = self._eng_rf_spec(final_board)
                sp = os.path.join(self.workdir, 'rfgeom_spec.json')
                with open(sp, 'w') as fh:
                    json.dump(spec, fh)
                facts = self._worker('rfgeom', '--board', final_board, '--spec', sp)
                for n, f in (facts or {}).items():
                    if n in ctxs:
                        checks[n] = _rfcheck.evaluate_net(
                            (eng.get('entries') or {}).get(n) or {}, ctxs[n], f)
            except Exception as exc:
                checks = {'error': str(exc)}
        su = self._worker('stackup', '--board', final_board, '--nets', ','.join(all_marked))
        copper = su.get('copper') or []
        order = [c['name'] for c in copper]
        by_name = {c['name']: c for c in copper}
        for net in nets:
            sized = (eng.get('nets') or {}).get(net) or {}
            info = (su.get('nets') or {}).get(net) or {}
            routed_layers = sorted((info.get('track_layers') or {}).keys())
            ref = []
            for lname in routed_layers:
                if lname not in by_name:
                    continue
                k = order.index(lname)
                cls = by_name[lname].get('class')
                if cls == 'surface_top':
                    adj = [order[k + 1]] if k + 1 < len(order) else []
                elif cls == 'surface_bottom':
                    adj = [order[k - 1]] if k > 0 else []
                else:
                    adj = ([order[k - 1]] if k > 0 else []) + \
                          ([order[k + 1]] if k + 1 < len(order) else [])
                for a in adj:
                    ref.append({'layer': lname, 'reference_layer': a,
                                'gnd_pour': bool(by_name[a].get('gnd_pour')),
                                'result': 'ok' if by_name[a].get('gnd_pour') else 'warn'})
            e = {'target_z0_ohm': sized.get('target_z0'),
                 'freq_hz': sized.get('freq_hz'),
                 'band': _tlcalc.band_of(sized.get('freq_hz') or 0) if _tlcalc else None,
                 'dk_basis': sized.get('dk_basis'),
                 'primary_layer': sized.get('primary_layer'),
                 'computed_width_mm': sized.get('width_mm'),
                 'applied_width_mm': sized.get('applied_width_mm'),
                 'width_verified': sized.get('width_verified'),
                 'per_layer': sized.get('per_layer'),
                 'routed_layers': routed_layers,
                 'vias': info.get('vias'),
                 'routed_length_mm': round(info.get('length_mm') or 0.0, 2),
                 'reference_plane': ref,
                 'warnings': []}
            if sized.get('warning'):
                e['warnings'].append(sized['warning'])
            if any(r['result'] == 'warn' for r in ref):
                e['warnings'].append('no ground pour found on a reference layer')
            f = sized.get('freq_hz')
            prim = (sized.get('per_layer') or {}).get(sized.get('primary_layer') or '') or {}
            if f and prim.get('eps_eff'):
                lam = _tlcalc.lambda_g_mm(prim['eps_eff'], f)
                e['rf'] = {
                    'lambda_g_mm': round(lam, 2) if lam else None,
                    'w_over_lambda_g': (round(sized['width_mm'] / lam, 4)
                                        if lam and sized.get('width_mm') else None),
                    'loss_db_per_mm': prim.get('loss_db_per_mm'),
                    'est_feed_loss_db': (round(prim['loss_db_per_mm'] * (info.get('length_mm') or 0.0), 3)
                                         if prim.get('loss_db_per_mm') is not None else None)}
            if isinstance(checks, dict) and net in checks:
                e['checks'] = checks[net]
                e['check_summary'] = {
                    'fail': sum(1 for x in checks[net] if x['severity'] == 'fail'),
                    'warn': sum(1 for x in checks[net] if x['severity'] == 'warn')}
            rep['nets'][net] = e
        diffs = []
        for pname, rec in (eng.get('diff') or {}).items():
            lens = {leg: round(((su.get('nets') or {}).get(leg) or {}).get('length_mm') or 0.0, 2)
                    for leg in rec['nets']}
            diffs.append({
                'name': pname, 'nets': rec['nets'],
                'target_zdiff_ohm': rec.get('target_zdiff'),
                'primary_layer': rec.get('primary_layer'),
                'per_layer': rec.get('per_layer'),
                'applied_width_mm': rec.get('width_mm'),
                'applied_gap_mm': rec.get('gap_mm'),
                'applied_class': rec.get('applied_class'),
                'verified': rec.get('verified'),
                'leg_lengths_mm': lens,
                'skew_mm': (round(abs(lens[rec['nets'][0]] - lens[rec['nets'][1]]), 2)
                            if len(lens) == 2 else None),
                'note': 'Skew is a measured fact only: length matching is a '
                        'separate item, and KiCad DRC diff-pair checks '
                        'remain the authority.'})
        if diffs:
            rep['differential'] = diffs
        self._record('engineered_report', {'nets': list(rep['nets'].keys())})
        return rep

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
        eng_first = self._engineered_first_nets()
        if eng_first:
            first_nets = eng_first + [n for n in first_nets if n not in eng_first]
        if str(self.cfg.get('first_nets_policy', 'auto')).lower() == 'none':
            # v1.11.5 (#132): the policy is applied AFTER the extras
            # are merged -- 'none' means no first_nets advice at all,
            # engineered extras included (the prepend used to bypass
            # the policy entirely). The 64-net cap likewise applies
            # to the merged list, below.
            first_nets = []
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
        # (#193): a failed pass (rc / route-JSON exit_code 1-2)
        # must not move ANY run state -- the old order assigned
        # last_stuck / last_stuck_board / current / last_route
        # first and raised afterwards, so the failed board became
        # current, overwrote a prior valid last_route, and could
        # be delivered by the orchestrator's best-is-None path.
        _err = engine_run.engine_error(p.returncode, rj)
        if _err:
            _tail = ''
            try: _tail = text.strip().splitlines()[-1][:200]
            except Exception: pass
            raise PipelineError('%s: %s (see %s)' % (_err, _tail, log_path))
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
        # pass with the engine's message (checked above, #193);
        # rc 3 is a normal partial.
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
        if _rc not in (0, 3):
            self.log('TraceMaker variant %s exited with code %d (log: %s)' % (eng, _rc, log_path))  # v1.9.6 (#40); #188: rc 3 is a normal partial result, not an error
        if not os.path.exists(outp):
            # #188: surface the engine's own message, as tool_route
            # does -- the bare 'produced no board' discarded the
            # refusal text (it sat unread in the .tm.log; the
            # deterministic tm_relax refusal hid there on every
            # Reproducible run until #184).
            _err = engine_run.engine_error(_rc, None)
            if _err:
                _tail = ''
                try: _tail = open(log_path).read().strip().splitlines()[-1][:200]
                except Exception: pass
                raise PipelineError('%s: %s (see %s)' % (_err, _tail, log_path))
            raise PipelineError('TraceMaker variant %s produced no board (see %s)' % (eng, log_path))
        try: self._worker('refill', '--board', outp, '--out', outp)
        except Exception: pass
        self._sidecars(outp)
        # v1.12.2 (#177): hand the caller the variant's structured
        # record (same fields tool_route bakes into last_route), so
        # a portfolio win can seed the job's route record.
        _rj = {}
        try: _rj = json.load(open(outp + '.route.json'))
        except Exception: pass
        # (#193): engine_error was consulted only when the output
        # file was MISSING -- a variant that failed (rc / JSON
        # exit_code 1-2) after writing a board returned 'routed'
        # and competed in the portfolio on its gate numbers, and a
        # win seeded last_route with the failed record. A failed
        # variant is a failed leg: raise, and tool_portfolio's
        # per-leg catch logs it with the engine's message.
        _err = engine_run.engine_error(_rc, _rj)
        if _err:
            _tail = ''
            try: _tail = open(log_path).read().strip().splitlines()[-1][:200]
            except Exception: pass
            raise PipelineError('%s: %s (see %s)' % (_err, _tail, log_path))
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
        _rc = None
        try:
            with open(log_path, 'w') as lf:
                _rc = subprocess.run(cmdline, stdout=lf, stderr=subprocess.STDOUT, timeout=engine_run.scaled_timeout(1900, self.cfg.get('work', 40000000))).returncode  # v1.12.2 (#176)
        except Exception as e:
            self._record('reroute_window', {'net': net, 'status': 'error', 'detail': str(e)[:200]})
            return {'status': 'error', 'net': net}
        if not os.path.exists(outp):
            self._record('reroute_window', {'net': net, 'status': 'no_output'})
            return {'status': 'no_output', 'net': net}
        # (#193): the return code used to be discarded and the
        # route JSON never read -- a failed --reroute that wrote a
        # board proceeded to gate + accept on gate numbers alone.
        _rwj = {}
        try: _rwj = json.load(open(os.path.join(self.workdir, 'route-rw-%d.json' % self._oseq)))
        except Exception: pass
        _err = engine_run.engine_error(_rc, _rwj)
        if _err:
            _tail = ''
            try: _tail = open(log_path).read().strip().splitlines()[-1][:200]
            except Exception: pass
            self.log('reroute_window %s: %s: %s (see %s)' % (net, _err, _tail, log_path))
            self._record('reroute_window', {'net': net, 'status': 'engine_failed', 'detail': _err})
            return {'status': 'engine_failed', 'net': net}
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

    def _leg_domain_args(self):
        # #80: the finisher/repair legs receive the domain spec so
        # their Grids can enforce HV/quiet spacing net-selectively.
        p = getattr(self, '_domains_path', None)
        return ['--domains', p] if p else []

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
        r = self._worker('finish_leg', '--board', self.current, '--out', outp, '--net', net, *self._leg_ban_args(), *extra, *self._leg_layer_args(), *self._leg_skeleton_args(), *self._leg_domain_args())
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
        r = self._worker('long_leg', '--board', self.current, '--out', outp, '--net', net, *self._leg_ban_args(), *self._leg_layer_args(), *self._leg_skeleton_args(), *self._leg_domain_args())
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
        r = self._worker('ilp_repair', '--board', self.current, '--out', outp, '--nets', ','.join(nets), *self._leg_ban_args(), *self._leg_layer_args(), *self._leg_skeleton_args(), *self._leg_domain_args())
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
            args += self._leg_domain_args()
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
        r = self._worker('stitch', '--board', self.current, '--out', outp, *self._leg_ban_args(), *self._leg_layer_args(), *self._leg_skeleton_args(), *self._leg_domain_args())  # v1.11.2 (#102): stitch DOES route -- _bridge_island draws track bridges (<=8 mm, <=2 vias) on the Grid; the v1.11.0 'vias only' exclusion rationale was wrong
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
