"""#67 Step 2 integration: rfgeom facts -> rfcheck findings, and the
pipeline treatments (return vias, smoothing) admitted through the gate.

The fixture is a synthetic 4-layer board built with pcbnew: a GND pour
on every layer, the In1.Cu reference split by a 4 mm gap under the
feed, a 50 ohm-style RF feed with two vias (no return vias), a 90 deg
corner pair, a 4 mm dangling stub and a thermal-relief ground pad --
the planted defects the checks must report, and the treatments must
partly repair (return vias yes; the plane gap is a layout defect and
must still be reported afterwards).
"""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

WORKER = os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py')
STACKUP = '''(stackup
      (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01) (material "EPDM") (epsilon_r 3.3) (loss_tangent 0.02))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.2) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01) (material "EPDM") (epsilon_r 3.3) (loss_tangent 0.02))
      (copper_finish "ENIG")
      (dielectric_constraints no)
    )
    '''
CTX = {'band': 'MID', 'freq_hz': 2.44e9, 'stub_limit_mm': 2.75,
       'return_ceiling_mm': 2.54, 'width_mm': 0.35, 'w_over_lambda_g': 0.006,
       'est_loss_db': 0.15, 'length_mm': 60.0, 'finish': 'ENIG',
       'refs': {'F.Cu': {'ref_layer': 'In1.Cu', 'pour_nets': ['GND']},
                'B.Cu': {'ref_layer': 'In2.Cu', 'pour_nets': ['GND']}}}


def _pcbnew_available():
    try:
        r = subprocess.run([sys.executable, '-c', 'import pcbnew'],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _nm(x):
    return int(round(x * 1e6))


def _build_board(path):
    import pcbnew
    def V(x, y):
        return pcbnew.VECTOR2I(_nm(x), _nm(y))
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(4)
    nets = {}
    for name in ('GND', 'RF'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    for (x1, y1, x2, y2) in [(2, 2, 58, 2), (58, 2, 58, 38), (58, 38, 2, 38), (2, 38, 2, 2)]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x1, y1)); s.SetEnd(V(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        b.Add(s)
    def zone(layer, x0, x1):
        z = pcbnew.ZONE(b)
        ls = pcbnew.LSET(); ls.AddLayer(layer)
        z.SetLayerSet(ls)
        z.SetNetCode(nets['GND'].GetNetCode())
        z.SetLocalClearance(_nm(0.2))
        out = z.Outline()
        out.NewOutline()
        chain = out.Outline(0)
        for (x, y) in [(x0, 2), (x1, 2), (x1, 38), (x0, 38)]:
            chain.Append(_nm(x), _nm(y))
        b.Add(z)
    zone(pcbnew.F_Cu, 2, 58)
    zone(pcbnew.In1_Cu, 2, 28)      # reference gap x in (28, 32)
    zone(pcbnew.In1_Cu, 32, 58)
    zone(pcbnew.In2_Cu, 2, 58)
    zone(pcbnew.B_Cu, 2, 58)
    def smd_fp(ref, pads):
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference(ref)
        for (num, net, x, y) in pads:
            p = pcbnew.PAD(fp)
            p.SetNumber(num)
            p.SetPosition(V(x, y))
            p.SetSize(V(1.0, 1.0))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
            p.SetLayerSet(ls)
            p.SetNetCode(nets[net].GetNetCode())
            fp.Add(p)
        b.Add(fp)
        return fp
    u1 = smd_fp('U1', [('1', 'RF', 5, 20), ('2', 'GND', 5, 23)])
    smd_fp('J1', [('1', 'RF', 55, 14)])
    for p in u1.Pads():
        if p.GetNumber() == '2':
            p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    def seg(layer, x1, y1, x2, y2):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2))
        t.SetWidth(_nm(0.35)); t.SetLayer(layer)
        t.SetNetCode(nets['RF'].GetNetCode())
        b.Add(t)
    def via(x, y):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(V(x, y))
        v.SetWidth(_nm(0.6)); v.SetDrill(_nm(0.3))
        v.SetNetCode(nets['RF'].GetNetCode())
        b.Add(v)
    seg(pcbnew.F_Cu, 5, 20, 20, 20)
    via(20, 20)
    seg(pcbnew.B_Cu, 20, 20, 24, 20)
    via(24, 20)
    seg(pcbnew.F_Cu, 24, 20, 40, 20)
    seg(pcbnew.F_Cu, 40, 20, 40, 14)
    seg(pcbnew.F_Cu, 40, 14, 55, 14)
    seg(pcbnew.F_Cu, 32, 20, 32, 24)   # 4 mm dangling stub
    pcbnew.SaveBoard(path, b)
    src = open(path).read()
    i = src.find('(setup')
    src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
    open(path, 'w').write(src)


def _make(tmp, with_markings=True):
    board = os.path.join(tmp, 'rf.kicad_pcb')
    _build_board(board)
    if with_markings:
        json.dump({'version': 1, 'impedance': [{
            'net': 'RF', 'z0_ohm': 50, 'freq_hz': 2.44e9,
            'layers': ['F.Cu'], 'role': 'tx', 'loss_budget_db': 0.5,
            'smooth': True}]},
            open(os.path.join(tmp, 'rf.ai-router-markings.json'), 'w'))
    return board


def _worker(cmd, *args):
    out = args[-1] + '.result.json' if False else None
    res = os.path.join(tempfile.mkdtemp(), 'res.json')
    subprocess.run([sys.executable, WORKER, cmd, *args, '--result', res],
                   capture_output=True, timeout=600, check=True)
    return json.load(open(res))


def _facts(board):
    spec = {'nets': {'RF': {'gnd_net': 'GND', 'ceiling_mm': 2.54,
             'refs': {'F.Cu': [{'ref_layer': 'In1.Cu', 'h_mm': 0.2}],
                      'B.Cu': [{'ref_layer': 'In2.Cu', 'h_mm': 0.2}]},
             'pi_refs': []}}}
    sp = os.path.join(tempfile.mkdtemp(), 'spec.json')
    json.dump(spec, open(sp, 'w'))
    return _worker('rfgeom', '--board', board, '--spec', sp)['RF']


def test_rfgeom_rfcheck_chain_reports_planted_defects():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    from ai_router import rfcheck
    with tempfile.TemporaryDirectory() as tmp:
        board = _make(tmp, with_markings=False)
        facts = _facts(board)
        rules = {(f['rule'], f['severity']) for f in rfcheck.evaluate_net({}, CTX, facts)}
        assert ('GATE-1', 'fail') in rules          # severed reference corridor
        assert ('GATE-3', 'fail') in rules          # 4 mm stub over a 2.75 mm limit
        assert ('GATE-4', 'fail') in rules          # vias with no return via
        assert ('GATE-8', 'fail') in rules          # thermal-relief ground pad
        assert ('WARN-2', 'warn') in rules          # ENIG above 1 GHz
        sev = [c for c in facts['corridor']['F.Cu']['severed'] if c['len_mm'] > 3.5]
        assert sev, facts['corridor']


def test_pipeline_treatments_and_report():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    import pcbnew
    from ai_router.pipeline import Pipeline
    with tempfile.TemporaryDirectory() as tmp:
        board = _make(tmp)
        pipe = Pipeline({'strip_existing': False,
                         'tracemaker_path': '/bin/true'}, board, lambda m: None)
        assert isinstance(pipe._eng, dict) and 'RF' in pipe._eng['entries']
        out = pipe.tool_rf_treatments()
        rv = out['return_vias']
        assert rv['adopted'] and len(rv['placed']) >= 2, rv
        facts = _facts(pipe.current)
        assert all(v['within'] >= 1 for v in facts['return_vias']), facts['return_vias']
        sm = pipe.tool_smooth_marked()
        assert sm['per_net']['RF']['adopted'], sm
        assert len(sm['per_net']['RF']['smoothed']) == 2, sm
        b = pcbnew.LoadBoard(pipe.current)
        arcs = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_ARC_T]
        assert len(arcs) == 2
        rep = pipe.engineered_report(pipe.current)
        e = rep['nets']['RF']
        rules = {(f['rule'], f['severity']) for f in e['checks']}
        assert ('GATE-1', 'fail') in rules       # the plane gap is NOT auto-fixed
        assert ('GATE-4', 'fail') not in rules   # return vias were placed
        assert e['check_summary']['fail'] >= 1
        assert rep['prefab_checklist'] and rep['treatments']


def test_step2_noop_without_markings():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    from ai_router.pipeline import Pipeline
    with tempfile.TemporaryDirectory() as tmp:
        board = _make(tmp, with_markings=False)
        pipe = Pipeline({'strip_existing': False,
                         'tracemaker_path': '/bin/true'}, board, lambda m: None)
        assert pipe._eng is False
        assert pipe.tool_rf_treatments()['status'] == 'skipped'
        assert pipe.tool_smooth_marked()['status'] == 'skipped'
        assert pipe.engineered_report(pipe.current) is None
