"""Deterministic pipeline tools for the AI Router plugin.

Every tool works on FILES in a work directory; the user's open board is
never modified. The gate is kicad-cli DRC after a zone refill — the same
gate used to validate the method by hand."""
import hashlib, json, os, re, shutil, subprocess, sys, time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, 'worker.py')

class PipelineError(Exception):
    pass

# DRC classes a finish/heal step can plausibly ADD with its copper;
# the gate guard reverts a step that increases any of them (v1.2.3:
# widened from the original 3 -- hole_clearance and tracks_crossing
# are real finisher failure modes the old guard could not see).
FAULT_CLASSES = ('shorting_items', 'clearance', 'items_not_allowed',
                 'tracks_crossing', 'hole_clearance', 'hole_to_hole',
                 'copper_edge_clearance', 'annular_width', 'via_diameter',
                 'drill_out_of_range', 'track_width', 'solder_mask_bridge',
                 'track_dangling', 'via_dangling', 'copper_sliver')  # v1.9.6 (#39): sliver added -- clean-pass counts it, so the accept key must see it
# Copper-layer DRC classes for reporting (excludes silk/text/library
# noise and unconnected_items, which is tracked separately).
COPPER_CLASSES = FAULT_CLASSES  # v1.9.6 (#39): identical since sliver joined FAULT_CLASSES (kept as a name for reporting call sites)

try:
    from config import DEFAULTS as _CFG_DEFAULTS
except Exception:
    _CFG_DEFAULTS = {}

# Engineered nets (#67 Step 1, branch-only): markings sidecar + TL
# calculator.  Defensive import in the config pattern -- a run without
# markings must behave byte-identically to before, so any failure here
# simply disables the feature.
try:
    from ai_router import markings as _markings
    from ai_router import tlcalc as _tlcalc
    from ai_router import rfcheck as _rfcheck
except Exception:
    try:
        import markings as _markings
        import tlcalc as _tlcalc
        import rfcheck as _rfcheck
    except Exception:
        _markings = None
        _tlcalc = None
        _rfcheck = None

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
    return (fault_count(g), 10 ** 9 if (g or {}).get('unconnected_total') is None else int(g['unconnected_total']),
            len((g or {}).get('split_nets') or []))

def fault_count(g):
    c = (g or {}).get('classes') or {}
    # v1.3.2: tracks on a banned layer (the layer-law census) count
    # as faults -- DRC cannot see them (the pour refills around a
    # track drawn on its own plane), so the census is the only guard.
    return sum(c.get(k, 0) for k in FAULT_CLASSES) + int((g or {}).get('banned_layer_tracks') or 0)


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
        pro = os.path.splitext(self.board_path)[0] + '.kicad_pro'
        if os.path.exists(pro):
            shutil.copy(pro, os.path.join(self.workdir, 'input.kicad_pro'))
        dru = os.path.splitext(self.board_path)[0] + '.kicad_dru'
        if os.path.exists(dru):
            shutil.copy(dru, os.path.join(self.workdir, 'input.kicad_dru'))
        self._gseq = 0   # v1.8.2 (#15): unique gate report names
        self._oseq = 0   # v1.8.2 (#14): unique step output names
        if cfg.get('strip_existing', True):
            self.base = os.path.join(self.workdir, 'base.kicad_pcb')
            r = self._worker('strip', '--board', src_copy, '--out', self.base)
            self.log('Stripped existing routing: %s items removed' % r.get('removed_tracks_vias'))
        else:
            self.base = src_copy
        self.current = self.base
        self._record('setup', {'base': self.base})
        self._eng = None
        try:
            self._engineered_setup()
        except Exception as e:
            # Markings must never break a route: surface it and continue.
            self._eng = {'error': 'engineered setup failed: %s' % e}
            self.log('Engineered nets: setup failed (%s) -- continuing unmarked' % e)
            self._record('engineered_nets', {'error': str(e)})

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
            return list(eng.get('first_nets') or [])
        return []

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

    def _eng_layer_width(self, entry, cu, ncu):
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
        w = _tlcalc.solve_width(lambda ww: fn(ww).z0, target)
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
            refs = {}
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
                refs[lname] = [{'ref_layer': a, 'h_mm': h} for (a, h) in cand if h]
            band = _tlcalc.band_of(entry.get('freq_hz') or 0)
            sized = (eng.get('nets') or {}).get(net) or {}
            prim = (sized.get('per_layer') or {}).get(sized.get('primary_layer') or '') or {}
            eps_eff = prim.get('eps_eff')
            f_top = self._BAND_TOP_HZ.get(band, 3e9)
            lam_top = _tlcalc.lambda_g_mm(eps_eff, f_top) if eps_eff else None
            lam_f = _tlcalc.lambda_g_mm(eps_eff, entry['freq_hz']) if eps_eff and entry.get('freq_hz') else None
            ceiling = self._BAND_CEILING_MM.get(band, 2.54)
            pi = entry.get('pi') or {}
            pi_refs = list(pi.get('radio') or []) + list(pi.get('antenna') or [])
            spec['nets'][net] = {'gnd_net': 'GND', 'ceiling_mm': ceiling,
                                 'refs': refs, 'pi_refs': pi_refs}
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
                'refs': {ln: {'ref_layer': rl[0]['ref_layer'],
                              'pour_nets': by_name[rl[0]['ref_layer']].get('pour_nets') or []}
                         for ln, rl in refs.items() if rl}}
        return spec, ctxs

    def _eng_gate_try(self, cmd, args, label, extra_ok=None):
        """Run a worker command on a copy of the current board and adopt
        the copy only if the acceptance key does not regress (and an
        optional extra predicate on the after-gate holds)."""
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        kb = _accept_key(before)
        outp = self._out(label)
        shutil.copy(self.current, outp)
        r = self._worker(cmd, '--board', outp, *args)
        adopted = False
        if os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            bad = _accept_key(after) > kb or (extra_ok is not None and not extra_ok(before, after))
            if bad:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
            else:
                self.last_gate = after
                adopted = True
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
        vspec = {'nets': {n: {'gnd_net': 'GND',
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
                    fence_nets[n] = {'gnd_net': 'GND',
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
        nets = list((eng.get('nets') or {}).keys())
        if not nets and not (eng.get('diff') or {}):
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
        cmdline += ['--work', str(work or self.cfg.get('work', 20000000)), '--time', '3600']
        with open(log_path, 'w') as lf:
            p = subprocess.run(cmdline, stdout=lf, stderr=subprocess.STDOUT, timeout=3900)
        if not os.path.exists(outp):
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
        results = []
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
            if winner is None or key < winner[0]:
                winner = (key, outp, g)
        self._record('portfolio', {'engines': list(engines), 'results': results,
                                   'winner': winner[1] if winner else None})
        if winner:
            self.current = winner[1]
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
                   '--soft-zones', '--keep-vias-off-pads', '--diff-pairs', '--si-rules', '--critical-auto']
        if banned: cmdline += ['--no-tracks-on', ','.join(banned)]
        cmdline += ['--work', str(self.cfg.get('work', 20000000)), '--time', '1800']
        log_path = outp + '.tm.log'
        with open(log_path, 'w') as lf:
            _rc = subprocess.run(cmdline, stdout=lf, stderr=subprocess.STDOUT, timeout=2400).returncode
        if _rc != 0:
            self.log('TraceMaker variant %s exited with code %d (log: %s)' % (eng, _rc, log_path))  # v1.9.6 (#40)
        if not os.path.exists(outp):
            raise PipelineError('TraceMaker variant %s produced no board' % eng)
        try: self._worker('refill', '--board', outp, '--out', outp)
        except Exception: pass
        self._sidecars(outp)
        return {'status': 'routed'}

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

    def _layer_info(self, board):
        # v1.10.1 (#49): one cached summary per board for layer data.
        cache = getattr(self, '_layer_info_cache', None)
        if cache is None: cache = self._layer_info_cache = {}
        if board not in cache:
            summ = self._worker('summary', '--board', board, '--fast')
            aliases = summ.get('layer_aliases') or {c.lower(): c for c in (summ.get('copper_layers') or [])}
            cache[board] = {'copper_layers': summ.get('copper_layers') or [], 'layer_aliases': aliases}
        return cache[board]

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
        info = self._layer_info(board)
        raw = [x.strip() for x in str(self.cfg.get('no_tracks_on', 'In1.Cu')).split(',') if x.strip()]
        canon, unknown = resolve_ban_names(raw, info['layer_aliases'])
        if unknown:
            self.log('WARNING: no_tracks_on names not on this board (ban vacuous here): %s (board layers: %s)' % (unknown, info['copper_layers']))
        return canon

    def tool_gate(self, board=None):
        board = board or self.current
        self._sidecars(board)
        # v1.8.2 (#15): a UNIQUE report per gate + the return code is
        # checked. Before, every post-pass gate reused gate<pass>.drc.txt
        # and a failed kicad-cli silently left the PREVIOUS board's
        # report to be parsed as this board's gate.
        total, unc, classes, pour, signal = self._drc_measure(board)
        summ = self._worker('summary', '--board', board, '--fast')  # v1.9.6 (#40): the gate discards crossings/RUDY
        # v1.3.2 layer census: any track on a cfg-banned layer is a
        # layer-law fault, counted into fault_count via the gate dict.
        _tbl = summ.get('tracks_by_layer') or {}
        # v1.10.1 (#49): census against the board-resolved canonical
        # names; unknown names warn here (a gate must always measure)
        # -- the route path fails loudly on them via _banned_for.
        _raw_bans = [x.strip() for x in str(self.cfg.get('no_tracks_on', 'In1.Cu')).split(',') if x.strip()]
        _banned_names, _unknown_bans = resolve_ban_names(_raw_bans, summ.get('layer_aliases') or {c.lower(): c for c in (summ.get('copper_layers') or [])})
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
        self.last_gate = {'violations_total': total, 'unconnected_total': unc,
                          'violations_excl_unconnected': total,  # v1.8.2 (#18): the KiCad header total ALREADY excludes unconnected items (proven on a live report); subtracting them again undercounted
                          'board_sha256': hashlib.sha256(open(board, 'rb').read()).hexdigest(),
                          'copper_violations': sum(classes.get(k, 0) for k in COPPER_CLASSES),
                          'vias': summ.get('vias'), 'wirelength_mm': summ.get('wirelength_mm'),
                          'classes': classes, 'pour_items': pour, 'split_signal_nets': split_signal_nets, 'split_pour_nets': split_pour_nets,
                          'signal_items': signal, 'split_nets': split_nets,
                          'pour_nets': pour_nets, 'tracks_by_layer': _tbl, 'banned_layer_tracks': banned_layer_tracks, 'banned_exempt_own_plane': banned_exempt_own_plane, 'board': board}
        self._record('gate', self.last_gate)
        self.log('Gate: %d unconnected (%d pour, %d signal), %d violations' % (unc, pour, len(signal), total))
        return self.last_gate

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
        cmdline += ['--work', str(self.cfg.get('work', 20000000)), '--time', '1800']
        log_path = os.path.join(self.workdir, 'tm-rw-%d.log' % self._oseq)
        try:
            with open(log_path, 'w') as lf:
                subprocess.run(cmdline, stdout=lf, stderr=subprocess.STDOUT, timeout=1900)
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
            except Exception as e: self.log('WARNING: lock restore failed (%s)' % e)
        self._sidecars(outp)
        after = self.tool_gate(outp)
        if str(self.cfg.get('reroute_accept', _CFG_DEFAULTS.get('reroute_accept', 'faults'))) == 'connectivity':
            ck = lambda g: (10**9 if g.get('unconnected_total') is None else int(g['unconnected_total']), len(g.get('split_nets') or []), fault_count(g))
            kb, ka = ck(before), ck(after)
        else:
            ka = _accept_key(after)
        if ka < kb:
            self.current = outp; self.last_gate = after
            st = 'accepted'
        else:
            self.last_gate = before
            st = 'reverted'
        self._record('reroute_window', {'net': net, 'status': st, 'key_before': kb, 'key_after': ka, 'window': res.get('window'), 'accept': str(self.cfg.get('reroute_accept', _CFG_DEFAULTS.get('reroute_accept', 'faults')))})
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
        r = self._worker('finish_leg', '--board', self.current, '--out', outp, '--net', net, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')), *extra)
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
        r = self._worker('long_leg', '--board', self.current, '--out', outp, '--net', net, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')))
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
        r = self._worker('ilp_repair', '--board', self.current, '--out', outp, '--nets', ','.join(nets), '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')))
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
            args = ['--board', self.current, '--out', outp, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu'))]
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
        r = self._worker('stitch', '--board', self.current, '--out', outp, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')))
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
        pro = os.path.join(self.workdir, 'input.kicad_pro')
        if os.path.exists(pro):
            shutil.copy(pro, os.path.join(self.workdir, 'final.kicad_pro'))
        self._sidecars(final)  # v1.8.2 (#17): also final.kicad_dru when present
        rec = {'final': final}
        if pre is not None:
            after = self.tool_gate(final)
            rec['fault_delta'] = fault_count(after) - fault_count(pre)
            if rec['fault_delta']:
                self.log('finalize: refill changed the fault count by %+d' % rec['fault_delta'])
        self._record('finalize', rec)
        return final
