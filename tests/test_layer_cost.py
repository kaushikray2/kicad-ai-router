"""v1.11/#73: layer_cost plumbing -- spec parsing, name resolution,
engine/leg command-line construction, the conflict rule (a priced
layer is not banned), the gate census keying, auto-escalation
disable, backend carry-through, and worker Grid pricing.
Run: python3 tests/test_layer_cost.py  (needs pcbnew for the Grid
test; the rest stub the worker + engine, after test_stuck_tag.py)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'docker'))
import pipeline as pl
from pipeline import parse_layer_cost, resolve_cost_names, format_layer_cost

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']

# ------------------------------------------------------------ parsing
def test_parse_valid():
    assert parse_layer_cost('') == ([], None)
    assert parse_layer_cost(None) == ([], None)
    assert parse_layer_cost('   ') == ([], None)
    assert parse_layer_cost('In1.Cu=10,In2.Cu=10') == ([('In1.Cu', 10), ('In2.Cu', 10)], None)
    assert parse_layer_cost('In1.Cu=1') == ([('In1.Cu', 1)], None)        # bounds are inclusive
    assert parse_layer_cost('In1.Cu=1000') == ([('In1.Cu', 1000)], None)
    assert parse_layer_cost(' In2.Cu = 4 ,') == ([('In2.Cu', 4)], None)   # whitespace/stray comma tolerated

def test_parse_rejects_malformed():
    for bad in ('In1.Cu=0', 'In1.Cu=1001', 'In1.Cu=-3', 'In1.Cu=x', 'In1.Cu=10.5',
                'In1.Cu=+10', 'In1.Cu', 'In1.Cu=', '=10', 'In1.Cu=10,In1.Cu=5',
                'In1.Cu=10,in1.cu=5', ',', 'In1.Cu==10', 'In1.Cu=1e3'):
        pairs, err = parse_layer_cost(bad)
        assert pairs is None and err, (bad, pairs, err)

def test_resolve_cost_names():
    canon, unk = resolve_cost_names([('GND', 5), ('In2.Cu', 10), ('Nowhere.Cu', 3)], ALIASES)
    assert canon == [('In1.Cu', 5), ('In2.Cu', 10)] and unk == ['Nowhere.Cu']
    canon, unk = resolve_cost_names([(' 3V3 ', 7)], ALIASES)
    assert canon == [('In2.Cu', 7)] and unk == []
    canon, _ = resolve_cost_names([('GND', 5), ('In1.Cu', 9)], ALIASES)
    assert canon == [('In1.Cu', 5)]  # two spellings of one layer: first wins
    assert format_layer_cost(canon) == 'In1.Cu=5'
    assert format_layer_cost([]) == ''

# ------------------------------------------------------------ bare pipe
def _bare_pipe(cfg, aliases=ALIASES, layers=LAYERS):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = cfg; pipe.log = lambda *a: None
    pipe._copper_layers = list(layers)
    def fake_worker(cmd, *args, **kw):
        if cmd == 'summary':
            return {'copper_layers': list(layers), 'layer_aliases': dict(aliases)}
        return {}
    pipe._worker = fake_worker
    return pipe

def _capture_run(pipe):
    seen = []
    real_run = pl.subprocess.run
    def fake_run(cmdline, stdout=None, **kw):
        seen.append(list(cmdline))
        outp = cmdline[cmdline.index('-o') + 1]
        open(outp, 'w').write('board')
        if stdout: stdout.write('routed 1/2 connections\n')
        class R: returncode = 0
        return R()
    pl.subprocess.run = fake_run
    return seen, real_run

def test_cmdline_route_and_variant_carry_cost():
    cfg = {'tracemaker_path': '/tm/tracemaker', 'no_tracks_on': 'In1.Cu,In2.Cu',
           'layer_cost': 'In2.Cu=10', 'work': 1000}
    pipe = _bare_pipe(cfg)
    seen, real_run = _capture_run(pipe)
    try:
        pipe.tool_route()
        pipe._route_variant('tm_relax', os.path.join(pipe.workdir, 'var.kicad_pcb'))
    finally:
        pl.subprocess.run = real_run
    assert len(seen) == 2, seen
    for argv in seen:
        assert argv[argv.index('--layer-cost') + 1] == 'In2.Cu=10', argv
        # conflict rule on the same command line: In2 is priced, so
        # only In1 remains banned
        assert argv[argv.index('--no-tracks-on') + 1] == 'In1.Cu', argv
    pol = [e for e in pipe.ledger if e['step'] == 'layer_policy']
    assert len(pol) == 1, pipe.ledger
    assert pol[0]['data']['layer_cost'] == 'In2.Cu=10'
    assert pol[0]['data']['no_tracks_on_effective'] == ['In1.Cu']
    assert pol[0]['data']['no_tracks_on_configured'] == ['In1.Cu', 'In2.Cu']

def test_cmdline_unset_is_byte_identical():
    cfg = {'tracemaker_path': '/tm/tracemaker', 'no_tracks_on': 'In1.Cu', 'work': 1000}
    pipe = _bare_pipe(cfg)
    seen, real_run = _capture_run(pipe)
    try:
        pipe.tool_route()
    finally:
        pl.subprocess.run = real_run
    d = pipe.workdir
    expected = ['/tm/tracemaker', 'route', pipe.base, '-o', os.path.join(d, 'pass1.kicad_pcb'),
                '--json', os.path.join(d, 'route1.json'), '--stuck-report', os.path.join(d, 'stuck1.json'),
                '--advice', os.path.join(d, 'advice1.json'), '--soft-zones', '--keep-vias-off-pads',
                '--diff-pairs', '--si-rules', '--critical-auto', '--cut-report',
                '--no-tracks-on', 'In1.Cu', '--work', '1000', '--time', '3600']
    assert seen == [expected], seen
    assert not any('--layer-cost' in a for a in seen[0])

def test_cmdline_reroute_window_carries_cost():
    cfg = {'tracemaker_path': '/tm/tracemaker', 'no_tracks_on': 'In1.Cu',
           'layer_cost': 'In1.Cu=4,In2.Cu=4', 'work': 1000, 'reroute_accept': 'faults'}
    pipe = _bare_pipe(cfg)
    gate = {'violations_total': 0, 'unconnected_total': 1, 'classes': {}, 'split_nets': []}
    pipe.last_gate = dict(gate)
    pipe.tool_gate = lambda board=None: dict(gate, board=board or pipe.current)
    real_worker = pipe._worker
    def worker2(cmd, *args, **kw):
        if cmd == 'lock_window': return {'status': 'locked', 'window': [0, 0, 1, 1]}
        return real_worker(cmd, *args, **kw)
    pipe._worker = worker2
    seen, real_run = _capture_run(pipe)
    try:
        pipe.tool_reroute_window('TESTNET')
    finally:
        pl.subprocess.run = real_run
    assert len(seen) == 1, seen
    argv = seen[0]
    assert '--reroute' in argv
    assert argv[argv.index('--layer-cost') + 1] == 'In1.Cu=4,In2.Cu=4', argv
    # both priced layers were configured banned (default In1) or not
    # banned at all (In2): the effective ban set is empty
    assert '--no-tracks-on' not in argv, argv

def test_leg_args_and_conflict_rule():
    cfg = {'no_tracks_on': 'In1.Cu,In2.Cu', 'layer_cost': 'In2.Cu=10'}
    pipe = _bare_pipe(cfg)
    assert pipe._banned_for(pipe.base) == ['In1.Cu']
    assert pipe._leg_layer_args() == ['--layer-cost', 'In2.Cu=10']
    cfg2 = {'no_tracks_on': 'In1.Cu,In2.Cu'}
    pipe2 = _bare_pipe(cfg2)
    assert pipe2._banned_for(pipe2.base) == ['In1.Cu', 'In2.Cu']
    assert pipe2._leg_layer_args() == []
    # invalid spec: treated as off, bans untouched, nothing raises
    cfg3 = {'no_tracks_on': 'In1.Cu', 'layer_cost': 'In1.Cu=0'}
    pipe3 = _bare_pipe(cfg3)
    assert pipe3._banned_for(pipe3.base) == ['In1.Cu']
    assert pipe3._leg_layer_args() == []
    # unknown cost name: dropped, the rest still prices
    cfg4 = {'no_tracks_on': 'In1.Cu', 'layer_cost': 'Nowhere.Cu=5,In2.Cu=3'}
    pipe4 = _bare_pipe(cfg4)
    assert pipe4._layer_cost_spec(pipe4.base) == 'In2.Cu=3'

def test_gate_census_keys_off_effective_bans():
    summ = {'nets': [], 'tracks_by_layer': {'In1.Cu': 3, 'In2.Cu': 5},
            'tracks_by_layer_net': {}, 'pour_layers': {}, 'pour_nets': [],
            'layer_aliases': dict(ALIASES), 'copper_layers': list(LAYERS),
            'vias': 0, 'wirelength_mm': 0.0}
    def mk(cfg):
        pipe = _bare_pipe(cfg)
        open(pipe.base, 'w').write('board')  # tool_gate hashes the board file
        pipe._drc_measure = lambda board: (0, 0, {}, 0, [])
        pipe._worker = lambda cmd, *a, **k: dict(summ) if cmd == 'summary' else {}
        return pipe
    g = mk({'no_tracks_on': 'In1.Cu,In2.Cu', 'layer_cost': 'In2.Cu=10'}).tool_gate()
    assert g['banned_layer_tracks'] == 3, g['banned_layer_tracks']  # In2 priced -> its 5 tracks are legal
    g2 = mk({'no_tracks_on': 'In1.Cu,In2.Cu'}).tool_gate()
    assert g2['banned_layer_tracks'] == 8, g2['banned_layer_tracks']

# ------------------------------------------------------------ orchestrator
def test_auto_escalation_disabled_by_layer_cost():
    import orchestrator
    orig = orchestrator._run_auto_once
    orchestrator._run_auto_once = lambda cfg, pipe, log: {'sentinel': True}
    class Pipe:
        def tool_board_summary(self): raise AssertionError('policy probe must not run')
    try:
        out = orchestrator.run_auto({'layer_cost': 'In1.Cu=10', 'no_tracks_on': 'In1.Cu'}, Pipe(), lambda *a: None)
        assert out == {'sentinel': True}
        # invalid spec = off: the probe path IS entered (and raises here)
        try:
            orchestrator.run_auto({'layer_cost': 'In1.Cu=0', 'no_tracks_on': 'In1.Cu'}, Pipe(), lambda *a: None)
            raise SystemExit('expected the policy probe to run')
        except AssertionError:
            pass
    finally:
        orchestrator._run_auto_once = orig

# ------------------------------------------------------------ backend
def test_backend_build_cfg_carries_layer_cost():
    import run_job
    assert run_job.build_cfg({}, {}, '/tm')['layer_cost'] == ''
    cfg = run_job.build_cfg({'layer_cost': 'In1.Cu=10,In2.Cu=10'}, {}, '/tm')
    assert cfg['layer_cost'] == 'In1.Cu=10,In2.Cu=10'

# ------------------------------------------------------------ worker Grid
def test_worker_grid_pricing():
    try:
        import pcbnew
        import worker as w
    except Exception:
        print('SKIP worker Grid test: no pcbnew'); return
    b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
    try:
        w.BANNED_LAYER_IDS = {pcbnew.In1_Cu}
        w.LAYER_COST_BY_ID = {pcbnew.In1_Cu: 5, pcbnew.In2_Cu: 10}
        g = w.Grid(b, (0.0, 10.0, 0.0, 10.0))
        # stack order F, In1, In2, B -> factors per stack position
        assert g._lfac == [1.0, 5.0, 10.0, 1.0], g._lfac
        # price wins over ban: In1 is priced, so it stays routable
        assert 1 in g.routable and 2 in g.routable
        assert g.banned_idx == set(), g.banned_idx
        blocked, via_block = g.build(set())
        simp, _c = g.astar(blocked, via_block, (1.0, 1.0), (9.0, 1.0), (0, 2), (0, 2))
        assert simp and simp[0][0] == 0 and simp[-1][0] == 0  # the free layer wins over the 10x layer
    finally:
        w.BANNED_LAYER_IDS = None; w.LAYER_COST_BY_ID = {}; w.LAYER_COST_UNRESOLVED = []

if __name__ == '__main__':
    test_parse_valid(); test_parse_rejects_malformed(); test_resolve_cost_names()
    test_cmdline_route_and_variant_carry_cost(); test_cmdline_unset_is_byte_identical()
    test_cmdline_reroute_window_carries_cost(); test_leg_args_and_conflict_rule()
    test_gate_census_keys_off_effective_bans(); test_auto_escalation_disabled_by_layer_cost()
    test_backend_build_cfg_carries_layer_cost(); test_worker_grid_pricing()
    print('layer cost OK')
