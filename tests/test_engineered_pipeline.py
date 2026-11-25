"""#67 Step 1 integration: markings -> stackup sizing -> netclass width
in the working copy -> first_nets -> verification report.

Needs pcbnew (the worker subprocess) and the repo's selftest board;
skips silently where pcbnew is unavailable.  The board fixture is the
selftest hbridge board with a stackup block injected as text (the
flat KiCad 10 form: epsilon_r / loss_tangent are layer children).
"""
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SELFTEST = os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest',
                        'hbridge-driver.kicad_pcb')
SELFTEST_PRO = os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest',
                            'hbridge-driver.kicad_pro')
STACKUP = '''(stackup
      (layer "F.SilkS" (type "Top Silk Screen"))
      (layer "F.Paste" (type "Top Solder Paste"))
      (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01) (material "EPDM") (epsilon_r 3.3) (loss_tangent 0.02))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.53) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01) (material "EPDM") (epsilon_r 3.3) (loss_tangent 0.02))
      (layer "B.Paste" (type "Bottom Solder Paste"))
      (layer "B.SilkS" (type "Bottom Silk Screen"))
      (copper_finish "None")
      (dielectric_constraints no)
    )
    '''

def _pcbnew_available():
    # #130 (B5): the probe used to ignore the return code and so
    # always reported pcbnew available
    try:
        r = subprocess.run([sys.executable, '-c', 'import pcbnew'],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False

def _make_board(tmp, with_stackup=True, with_markings=True):
    src = open(SELFTEST).read()
    if with_stackup:
        i = src.find('(setup')
        src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
    board = os.path.join(tmp, 'demo.kicad_pcb')
    open(board, 'w').write(src)
    shutil.copy(SELFTEST_PRO, os.path.join(tmp, 'demo.kicad_pro'))
    if with_markings:
        json.dump({'version': 1, 'impedance': [{'net': 'IN1', 'z0_ohm': 50}]},
                  open(os.path.join(tmp, 'demo.ai-router-markings.json'), 'w'))
    return board

def _fake_engine(tmp):
    path = os.path.join(tmp, 'fake-tm')
    with open(path, 'w') as f:
        f.write('#!/bin/sh\n'
                'inp="$2"\n'
                'while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then out="$2"; fi; shift; done\n'
                'cp "$inp" "$out"\n'
                'echo "routed 1/1 connections"\n')
    os.chmod(path, 0o755)
    return path

def test_pipeline_sizes_marks_and_reports():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    from ai_router.pipeline import Pipeline
    with tempfile.TemporaryDirectory() as tmp:
        board = _make_board(tmp)
        pipe = Pipeline({'tracemaker_path': _fake_engine(tmp)},
                        board, lambda m: None)
        eng = pipe._eng
        assert isinstance(eng, dict), eng
        rec = eng['nets']['IN1']
        # 50 ohm on 1.53 mm FR4 (er 4.4), 35 um copper: ~2.89 mm.
        assert rec['width_mm'] is not None and 2.8 < rec['width_mm'] < 3.0, rec
        assert rec['primary_layer'] == 'F.Cu'
        assert rec['applied_class'] == 'AI-IMP-IN1'
        assert rec['width_verified'] is True, rec
        pro = json.load(open(os.path.join(pipe.workdir, 'input.kicad_pro')))
        ai = [c for c in pro['net_settings']['classes']
              if c['name'] == 'AI-IMP-IN1']
        assert ai and abs(ai[0]['track_width'] - rec['width_mm']) < 0.001
        # marked net leads first_nets in the engine advice
        pipe.tool_route(first_nets=['+5V'])
        adv = json.load(open(os.path.join(pipe.workdir, 'advice1.json')))
        assert adv['first_nets'][0] == 'IN1' and '+5V' in adv['first_nets']
        # report shape + the S11 disclaimer
        rep = pipe.engineered_report(pipe.base)
        assert 'does not predict S11' in rep['scope']
        e = rep['nets']['IN1']
        assert e['target_z0_ohm'] == 50.0 and e['width_verified'] is True
        assert e['computed_width_mm'] == rec['width_mm']

def test_pipeline_without_markings_is_untouched():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    from ai_router.pipeline import Pipeline
    with tempfile.TemporaryDirectory() as tmp:
        board = _make_board(tmp, with_markings=False)
        pipe = Pipeline({'tracemaker_path': _fake_engine(tmp)},
                        board, lambda m: None)
        assert pipe._eng is False
        assert pipe._engineered_first_nets() == []
        assert pipe.engineered_report(pipe.base) is None
        pro_path = os.path.join(pipe.workdir, 'input.kicad_pro')
        pro = json.load(open(pro_path))
        assert not any(c['name'].startswith('AI-IMP')
                       for c in pro['net_settings']['classes'])

def test_pipeline_markings_without_stackup_still_prioritises():
    if not _pcbnew_available():
        print('skip: pcbnew unavailable')
        return
    from ai_router.pipeline import Pipeline
    with tempfile.TemporaryDirectory() as tmp:
        board = _make_board(tmp, with_stackup=False)
        pipe = Pipeline({'tracemaker_path': _fake_engine(tmp)},
                        board, lambda m: None)
        assert pipe._eng['stackup_defined'] is False
        assert pipe._eng['first_nets'] == ['IN1']
        assert 'no_stackup_in_board_file' in pipe._eng['placeholder_reasons']
