"""Issue #84: tool_power keyed its layer/thickness dicts by
cmd_stackup's CANONICAL names (F.Cu/In1.Cu/B.Cu) while
cmd_powergeom reports the board's actual layer names.  On a board
with renamed copper layers the power plan died (KeyError /
missing attribution).  Seam test: a real Pipeline whose stackup
worker reports a bottom layer renamed to 'GND' (board_name) and
whose powergeom facts use that board name throughout.
Needs pcbnew (Pipeline construction); skips silently without it."""
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SELFTEST = os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest',
                        'hbridge-driver.kicad_pcb')
SELFTEST_PRO = os.path.join(os.path.dirname(__file__), '..', 'docker', 'selftest',
                            'hbridge-driver.kicad_pro')

NETLIST = ('(export (version E) (components '
           '(comp (ref U1) (value REG) (libsource (lib F) (part REG)) (sheetpath (names "/") (tstamps "x"))) '
           '(comp (ref U2) (value MCU) (libsource (lib F) (part MCU)) (sheetpath (names "/") (tstamps "x")))) '
           '(libparts '
           '(libpart (lib F) (part REG) (pins (pin (num "2") (name o) (type power_out)) (pin (num "3") (name g) (type power_in)))) '
           '(libpart (lib F) (part MCU) (pins (pin (num "1") (name v) (type power_in)) (pin (num "2") (name g) (type power_in))))) '
           '(nets '
           '(net (code 1) (name "V5") (node (ref U1) (pin "2")) (node (ref U2) (pin "1"))) '
           '(net (code 2) (name "GND") (node (ref U1) (pin "3")) (node (ref U2) (pin "2")))))')

STACKUP = {'copper': [{'name': 'F.Cu', 'board_name': 'F.Cu', 'thickness_mm': 0.035},
                      {'name': 'B.Cu', 'board_name': 'GND', 'thickness_mm': 0.035}],
           'board_thickness_mm': 1.6}
FACTS = {'V5': {'tracks': [{'uuid': 't1', 'layer': 'GND', 'x1': 0, 'y1': 0,
                            'x2': 10, 'y2': 0, 'w': 0.3}],
                'vias': [],
                'pads': [{'ref': 'U1', 'pin': '2', 'x': 0, 'y': 0, 'layers': ['GND']},
                         {'ref': 'U2', 'pin': '1', 'x': 10, 'y': 0, 'layers': ['GND']}],
                'zones': []},
         'GND': {'tracks': [], 'vias': [],
                 'pads': [{'ref': 'U1', 'pin': '3', 'x': 0, 'y': 1, 'layers': ['F.Cu']},
                          {'ref': 'U2', 'pin': '2', 'x': 10, 'y': 1, 'layers': ['F.Cu']}],
                 'zones': [{'layer': 'F.Cu', 'polys': [[(-5, -5), (30, -5), (30, 20), (-5, 20)]]}]}}


def test_power_plan_with_renamed_layers():
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
        json.dump({'version': 1, 'power': [
            {'net': 'V5', 'loads': {'U2': {'peak_a': 1.0, 'sustained_a': 0.5,
                                           'kind': 'constant_current'}}}]},
            open(os.path.join(tmp, 'demo.ai-router-markings.json'), 'w'))
        netlist = os.path.join(tmp, 'demo.net')
        open(netlist, 'w').write(NETLIST)
        fake = os.path.join(tmp, 'fake-tm')
        open(fake, 'w').write('#!/bin/sh\ncp "$2" "$4"\n')
        os.chmod(fake, 0o755)
        pipe = Pipeline({'tracemaker_path': fake}, board, lambda m: None)
        pipe._eng_power_netlist = lambda: netlist
        orig_worker = pipe._worker
        def stub(cmd, *a, **k):
            if cmd == 'stackup': return STACKUP
            if cmd == 'powergeom': return FACTS
            return orig_worker(cmd, *a, **k)
        pipe._worker = stub
        pipe._eng_gate_try = lambda *a, **k: {'status': 'stubbed'}
        res = pipe.tool_power()
        assert res.get('status') == 'ok', res
        plan = pipe._eng_power_plan
        seg = plan['rails']['V5']['segments'][0]
        assert seg['i_peak_a'] == 1.0, seg
        assert 'required_width_mm' in seg, seg

if __name__ == '__main__':
    test_power_plan_with_renamed_layers(); print('power layer names OK')
