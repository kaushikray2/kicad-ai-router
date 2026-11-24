"""#67 Step 3 integration: a differential marking is sized from the
stackup, written into the working project as an AI-DIFF-* netclass
(track width + diff-pair width/gap on both legs), verified by
readback, prioritised in first_nets, and reported -- with skew as a
measured fact only (length matching is a separate item).
"""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

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


def _pcbnew_available():
    try:
        r = subprocess.run([sys.executable, '-c', 'import pcbnew'],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _build_board(path):
    import pcbnew
    def nm(x):
        return int(round(x * 1e6))
    def V(x, y):
        return pcbnew.VECTOR2I(nm(x), nm(y))
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(4)
    nets = {}
    for name in ('GND', 'DP', 'DN'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n
    def smd_fp(ref, pads):
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference(ref)
        for (num, net, x, y) in pads:
            p = pcbnew.PAD(fp)
            p.SetNumber(num)
            p.SetPosition(V(x, y))
            p.SetSize(V(0.8, 0.8))
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
            p.SetLayerSet(ls)
            p.SetNetCode(nets[net].GetNetCode())
            fp.Add(p)
        b.Add(fp)
    smd_fp('J2', [('1', 'DP', 5, 20), ('2', 'DN', 5, 21.5)])
    smd_fp('U2', [('1', 'DP', 40, 20), ('2', 'DN', 40, 21.5)])
    pcbnew.SaveBoard(path, b)
    src = open(path).read()
    i = src.find('(setup')
    src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
    open(path, 'w').write(src)


def test_pipeline_sizes_diff_pair():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    from ai_router import tlcalc
    from ai_router.pipeline import Pipeline
    with tempfile.TemporaryDirectory() as tmp:
        board = os.path.join(tmp, 'pair.kicad_pcb')
        _build_board(board)
        json.dump({'version': 1, 'differential': [
            {'name': 'USB', 'nets': ['DP', 'DN'], 'zdiff_ohm': 90}]},
            open(os.path.join(tmp, 'pair.ai-router-markings.json'), 'w'))
        pipe = Pipeline({'strip_existing': False,
                         'tracemaker_path': '/bin/true'}, board, lambda m: None)
        eng = pipe._eng
        assert isinstance(eng, dict), eng
        rec = eng['diff']['USB']
        assert rec['primary_layer'] == 'F.Cu', rec
        assert rec['width_mm'] and rec['gap_mm'], rec
        # the solved geometry really is 90 ohm in the layer's own model
        chk = tlcalc.diff_microstrip(rec['width_mm'], rec['gap_mm'], 0.2, 0.035, 4.4)
        assert abs(chk.zdiff - 90.0) < 0.15, (rec, chk.zdiff)
        # written into the working project on both legs, read back verified
        assert rec['applied_class'] == 'AI-DIFF-USB', rec
        assert rec['verified'] is True, rec
        pro = json.load(open(os.path.join(pipe.workdir, 'input.kicad_pro')))
        cls = [c for c in pro['net_settings']['classes'] if c['name'] == 'AI-DIFF-USB']
        assert cls and abs(cls[0]['diff_pair_gap'] - rec['gap_mm']) < 0.001
        assert abs(cls[0]['diff_pair_width'] - rec['width_mm']) < 0.001
        pats = {p['pattern']: p['netclass'] for p in pro['net_settings']['netclass_patterns']}
        assert pats.get('DP') == 'AI-DIFF-USB' and pats.get('DN') == 'AI-DIFF-USB'
        assert set(eng['first_nets']) == {'DP', 'DN'}
        rep = pipe.engineered_report(pipe.current)
        d = rep['differential'][0]
        assert d['name'] == 'USB' and d['verified'] is True
        assert d['skew_mm'] == 0.0 and 'authority' in d['note']
