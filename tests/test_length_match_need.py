"""Issue #99: tool_length_match's if/elif need computation starved
delay-only failures.  A member failing ONLY on delay in a group
declaring BOTH tolerances got need = delta_mm - tolerance_mm <= 0
from the first branch and was skipped -- no meander ever requested.
Seam test: real Pipeline, stubbed member stats and gate."""
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SELFTEST = os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest',
                        'hbridge-driver.kicad_pcb')
SELFTEST_PRO = os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest',
                            'hbridge-driver.kicad_pro')
STATS = {'NA': {'length_mm': 50.0, 'delay_ps': 300.0},
         'NB': {'length_mm': 49.8, 'delay_ps': 250.0}}   # NB: mm ok, 50 ps short


def test_delay_only_failure_requests_meander():
    try:
        subprocess.run([sys.executable, '-c', 'import pcbnew'],
                       capture_output=True, timeout=60)
    except Exception:
        print('skip: pcbnew unavailable'); return
    from ai_router.pipeline import Pipeline
    with tempfile.TemporaryDirectory() as tmp:
        board = os.path.join(tmp, 'demo.kicad_pcb')
        shutil.copy(SELFTEST, board)
        shutil.copy(SELFTEST_PRO, os.path.join(tmp, 'demo.kicad_pro'))
        json.dump({'version': 1, 'length_groups': [
            {'name': 'G', 'nets': ['NA', 'NB'], 'target_mode': 'longest',
             'tolerance_mm': 0.5, 'tolerance_ps': 5.0}]},
            open(os.path.join(tmp, 'demo.ai-router-markings.json'), 'w'))
        fake = os.path.join(tmp, 'fake-tm')
        open(fake, 'w').write('#!/bin/sh\ncp "$2" "$4"\n')
        os.chmod(fake, 0o755)
        pipe = Pipeline({'tracemaker_path': fake}, board, lambda m: None)
        pipe._eng_layer_delay_info = lambda: {}
        pipe._eng_member_stats = lambda nets, layers: dict(STATS)
        calls = []
        def gate(cmd, args, tag):
            calls.append((cmd, args))
            return {'adopted': False, 'status': 'stubbed'}
        pipe._eng_gate_try = gate
        pipe.tool_length_match()
        meanders = [a for (c, a) in calls if c == 'meander']
        assert len(meanders) == 1, calls
        assert meanders[0][1] == 'NB', meanders
        add = float(meanders[0][meanders[0].index('--add-mm') + 1])
        assert abs(add - 8.964) < 0.01, add   # (50-5) ps at NB's 5.02 ps/mm

if __name__ == '__main__':
    test_delay_only_failure_requests_meander(); print('length-match need OK')
