"""Deterministic pipeline tools for the AI Router plugin.

Every tool works on FILES in a work directory; the user's open board is
never modified. The gate is kicad-cli DRC after a zone refill — the same
gate used to validate the method by hand."""
import json, os, re, shutil, subprocess, sys, time
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
                 'track_dangling', 'via_dangling')
# Copper-layer DRC classes for reporting (excludes silk/text/library
# noise and unconnected_items, which is tracked separately).
COPPER_CLASSES = FAULT_CLASSES + ('copper_sliver',)

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
        if cfg.get('strip_existing', True):
            self.base = os.path.join(self.workdir, 'base.kicad_pcb')
            r = self._worker('strip', '--board', src_copy, '--out', self.base)
            self.log('Stripped existing routing: %s items removed' % r.get('removed_tracks_vias'))
        else:
            self.base = src_copy
        self.current = self.base
        self._record('setup', {'base': self.base})

    # ------------------------------------------------------------ internals
    def _record(self, step, data):
        entry = {'t': time.strftime('%H:%M:%S'), 'step': step, 'data': data}
        self.ledger.append(entry)
        with open(os.path.join(self.workdir, 'ledger.json'), 'w') as f:
            json.dump(self.ledger, f, indent=1)

    def _worker(self, cmd, *args):
        py = shutil.which('python3') or sys.executable
        self._wseq = getattr(self, '_wseq', 0) + 1
        result_path = os.path.join(self.workdir, 'worker-%s-%d.json' % (cmd, self._wseq))
        cmdline = [py, WORKER, cmd] + list(args) + ['--result', result_path]
        p = subprocess.run(cmdline, capture_output=True, text=True, timeout=1800)
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

    def tool_scorecard(self):
        r = self._worker('scorecard', '--board', self.base)
        self._record('scorecard', r)
        return r

    def tool_route(self, first_nets=None, work=None, from_current=False):
        self.pass_no += 1
        inp = self.current if from_current else self.base
        outp = os.path.join(self.workdir, 'pass%d.kicad_pcb' % self.pass_no)
        advice = os.path.join(self.workdir, 'advice%d.json' % self.pass_no)
        first_nets = list(first_nets or [])
        if len(first_nets) > 64:  # TraceMaker rejects advice files with >64 first_nets
            self.log('first_nets capped at 64 (was %d)' % len(first_nets))
            first_nets = first_nets[:64]
        with open(advice, 'w') as f:
            json.dump({'version': 1, 'first_nets': first_nets}, f)
        log_path = os.path.join(self.workdir, 'tm-pass%d.log' % self.pass_no)
        if getattr(self, '_copper_layers', None) is None:
            try: self._copper_layers = self._worker('summary', '--board', inp).get('copper_layers') or []
            except Exception: self._copper_layers = []
        banned = [b.strip() for b in str(self.cfg.get('no_tracks_on', 'In1.Cu,In2.Cu')).split(',') if b.strip()]
        if self._copper_layers: banned = [b for b in banned if b in self._copper_layers]  # TraceMaker rejects ban names that are not copper layers of THIS board (a 2-layer board has no In1.Cu); the worker's canonical stack is the authority
        cmdline = [self.cfg['tracemaker_path'], 'route', inp, '-o', outp,
                   '--json', os.path.join(self.workdir, 'route%d.json' % self.pass_no),
                   '--stuck-report', os.path.join(self.workdir, 'stuck%d.json' % self.pass_no),
                   '--advice', advice, '--soft-zones', '--keep-vias-off-pads',
                   '--diff-pairs', '--si-rules', '--critical-auto']
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
        text = open(log_path).read()
        unrouted = []
        for m in re.finditer(r'unrouted:\s+(\S+):\s+(\S+)\s+->\s+(\S+)\s+\(([^)]*)\)', text):
            unrouted.append({'net': m.group(1), 'from': m.group(2), 'to': m.group(3), 'note': m.group(4)})
        m = re.search(r'routed (\d+)/(\d+) connections', text)
        routed = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        self.current = outp
        self.last_route = {'pass': self.pass_no, 'routed': routed, 'unrouted': unrouted, 'board': outp}
        self._record('route', self.last_route)
        self.log('Pass %d: routed %s/%s connections; %d unrouted' % (self.pass_no, routed[0], routed[1], len(unrouted)))
        return self.last_route

    def tool_gate(self):
        drc_path = os.path.join(self.workdir, 'gate%d.drc.txt' % (self.pass_no or 0))
        subprocess.run([self.cfg.get('kicad_cli', 'kicad-cli'), 'pcb', 'drc', '--refill-zones',
                        '--output', drc_path, self.current], capture_output=True, text=True, timeout=900)
        d = open(drc_path).read()
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
        summ = self._worker('summary', '--board', self.current)
        # v1.3.2 layer census: any track on a cfg-banned layer is a
        # layer-law fault, counted into fault_count via the gate dict.
        _tbl = summ.get('tracks_by_layer') or {}
        _banned_names = [x.strip() for x in str(self.cfg.get('no_tracks_on', 'In1.Cu')).split(',') if x.strip()]
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
                          'violations_excl_unconnected': total - classes.get('unconnected_items', 0),
                          'copper_violations': sum(classes.get(k, 0) for k in COPPER_CLASSES),
                          'vias': summ.get('vias'), 'wirelength_mm': summ.get('wirelength_mm'),
                          'classes': classes, 'pour_items': pour, 'split_signal_nets': split_signal_nets, 'split_pour_nets': split_pour_nets,
                          'signal_items': signal, 'split_nets': split_nets,
                          'pour_nets': pour_nets, 'tracks_by_layer': _tbl, 'banned_layer_tracks': banned_layer_tracks, 'banned_exempt_own_plane': banned_exempt_own_plane, 'board': self.current}
        self._record('gate', self.last_gate)
        self.log('Gate: %d unconnected (%d pour, %d signal), %d violations' % (unc, pour, len(signal), total))
        return self.last_gate

    def tool_finish_leg(self, net):
        outp = os.path.join(self.workdir, 'finish-%s.kicad_pcb' % re.sub(r'[^A-Za-z0-9_+-]', '_', net))
        # The finisher self-verifies connectivity only; its grid planner
        # can still introduce copper faults (a via 0.18 mm from a track
        # it displaced around, a leg through a keepout). Gate-verify:
        # a finish that adds shorts/clearance/keepout faults is worse
        # than the split net it fixed, so revert it.
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        fb = fault_count(before)
        extra = ['--attempt-select'] if self.cfg.get('attempt_select') else []
        r = self._worker('finish_leg', '--board', self.current, '--out', outp, '--net', net, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')), *extra)
        if r.get('status') in ('routed', 'already_connected') and os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if fault_count(after) > fb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('finish_leg %s reverted: copper/keepout faults %d -> %d' % (net, fb, fault_count(after)))
        self._record('finish_leg', r)
        self.log('finish_leg %s: %s' % (net, r.get('status')))
        return r

    def tool_long_finish(self, net):
        # v1.4 phase (c) Stage B-0: large-window hard-A* finish for
        # far-apart split signal nets. Same gate-verify discipline as
        # tool_finish_leg: a long leg that adds copper faults is
        # reverted, however good its connectivity looks.
        outp = os.path.join(self.workdir, 'longfinish-%s.kicad_pcb' % re.sub(r'[^A-Za-z0-9_+-]', '_', net))
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        fb = fault_count(before)
        r = self._worker('long_leg', '--board', self.current, '--out', outp, '--net', net, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')))
        if r.get('status') in ('routed', 'already_connected') and os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if fault_count(after) > fb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('long_finish %s reverted: copper/keepout faults %d -> %d' % (net, fb, fault_count(after)))
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
        outp = os.path.join(self.workdir, 'ilprepair.kicad_pcb')
        before = self.last_gate if self.last_gate is not None else self.tool_gate()
        fb = fault_count(before)
        r = self._worker('ilp_repair', '--board', self.current, '--out', outp, '--nets', ','.join(nets), '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')))
        if r.get('routed') and os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if fault_count(after) > fb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('ilp_repair reverted: copper/keepout faults %d -> %d' % (fb, fault_count(after)))
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
            outp = os.path.join(self.workdir, 'healed.kicad_pcb')
            fb = fault_count(cur_gate)
            args = ['--board', self.current, '--out', outp, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu'))]
            if net: args += ['--nets', net]
            r = self._worker('heal', *args)
            agg['per_net'][net or '*'] = r.get('status')
            if r.get('bridges') and os.path.exists(outp):
                prev = self.current
                self.current = outp
                after = self.tool_gate()
                if fault_count(after) > fb:
                    self.current = prev
                    self.last_gate = cur_gate
                    r = dict(r); r['status'] = 'reverted_faults'
                    agg['per_net'][net or '*'] = 'reverted_faults'
                    self.log('heal[%s] reverted: copper/keepout faults %d -> %d' % (net, fb, fault_count(after)))
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
        fb = fault_count(before)
        outp = os.path.join(self.workdir, 'stitched.kicad_pcb')
        r = self._worker('stitch', '--board', self.current, '--out', outp, '--no-tracks-on', str(self.cfg.get('no_tracks_on', 'In1.Cu')))
        if os.path.exists(outp):
            prev = self.current
            self.current = outp
            after = self.tool_gate()
            if fault_count(after) > fb:
                self.current = prev
                self.last_gate = before
                r = dict(r); r['status'] = 'reverted_faults'
                self.log('stitch reverted: copper/keepout faults %d -> %d' % (fb, fault_count(after)))
        self._record('stitch', r)
        self.log('stitch: %d clusters stitched, %d failed' % (len(r.get('stitched', [])), len(r.get('failed', []))))
        return r

    def tool_prune(self):
        outp = os.path.join(self.workdir, 'pruned.kicad_pcb')
        r = self._worker('prune', '--board', self.current, '--out', outp)
        if r.get('pruned') and os.path.exists(outp):
            self.current = outp
        self._record('prune', r)
        self.log('prune: %s dead copper items removed' % r.get('pruned'))
        return r

    def finalize(self):
        final = os.path.join(self.workdir, 'final.kicad_pcb')
        self._worker('refill', '--board', self.current, '--out', final)
        pro = os.path.join(self.workdir, 'input.kicad_pro')
        if os.path.exists(pro):
            shutil.copy(pro, os.path.join(self.workdir, 'final.kicad_pro'))
        self._record('finalize', {'final': final})
        return final
