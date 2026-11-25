"""Audit 2026-11-24 regression fixtures for cmd_rfgeom (#91-#94):
GATE-11 via blindness, arc feeds skipped by the corridor checks,
GATE-2 keep-out endpoint-only testing, GATE-6 via-stitched pours.
Synthetic pcbnew boards in the style of test_engineered_step2."""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

WORKER = os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py')
STACKUP = '''(stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.2) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
    )
    '''


def _pcbnew_available():
    try:
        r = subprocess.run([sys.executable, '-c', 'import pcbnew'],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _nm(x):
    return int(round(x * 1e6))


class _B:
    def __init__(self):
        import pcbnew
        self.p = pcbnew
        b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
        self.b = b
        self.nets = {}
        for name in ('GND', 'RF', 'CLK', 'AUX'):
            n = pcbnew.NETINFO_ITEM(b, name); b.Add(n); self.nets[name] = n
        for (x1, y1, x2, y2) in [(2, 2, 58, 2), (58, 2, 58, 38), (58, 38, 2, 38), (2, 38, 2, 2)]:
            s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(self.V(x1, y1)); s.SetEnd(self.V(x2, y2))
            s.SetLayer(pcbnew.Edge_Cuts); b.Add(s)
    def V(self, x, y):
        return self.p.VECTOR2I(_nm(x), _nm(y))
    def zone(self, net, layer, x0, y0, x1, y1):
        p = self.p; z = p.ZONE(self.b)
        ls = p.LSET(); ls.AddLayer(layer); z.SetLayerSet(ls)
        z.SetNetCode(self.nets[net].GetNetCode()); z.SetLocalClearance(_nm(0.2))
        out = z.Outline(); out.NewOutline(); ch = out.Outline(0)
        for (x, y) in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
            ch.Append(_nm(x), _nm(y))
        self.b.Add(z); return z
    def seg(self, net, layer, x1, y1, x2, y2, w=0.35):
        p = self.p; t = p.PCB_TRACK(self.b)
        t.SetStart(self.V(x1, y1)); t.SetEnd(self.V(x2, y2))
        t.SetWidth(_nm(w)); t.SetLayer(layer)
        t.SetNetCode(self.nets[net].GetNetCode()); self.b.Add(t); return t
    def via(self, net, x, y):
        p = self.p; v = p.PCB_VIA(self.b)
        v.SetPosition(self.V(x, y)); v.SetWidth(_nm(0.6)); v.SetDrill(_nm(0.3))
        v.SetNetCode(self.nets[net].GetNetCode()); self.b.Add(v); return v
    def smd_fp(self, ref, pads):
        p = self.p; fp = p.FOOTPRINT(self.b); fp.SetReference(ref)
        for (num, net, x, y) in pads:
            pad = p.PAD(fp); pad.SetNumber(num); pad.SetPosition(self.V(x, y))
            pad.SetSize(self.V(1.0, 1.0)); pad.SetShape(p.PAD_SHAPE_RECT)
            pad.SetAttribute(p.PAD_ATTRIB_SMD)
            ls = p.LSET(); ls.AddLayer(p.F_Cu); pad.SetLayerSet(ls)
            pad.SetNetCode(self.nets[net].GetNetCode()); fp.Add(pad)
        self.b.Add(fp); return fp
    def save(self, path):
        self.p.SaveBoard(path, self.b)
        src = open(path).read(); i = src.find('(setup')
        src = src[:i + len('(setup')] + '\n    ' + STACKUP + src[i + len('(setup'):]
        open(path, 'w').write(src)


def _facts(board, h=0.2):
    spec = {'nets': {'RF': {'gnd_net': 'GND', 'ceiling_mm': 2.54,
             'refs': {'F.Cu': [{'ref_layer': 'In1.Cu', 'h_mm': h}]}, 'pi_refs': []}}}
    td = tempfile.mkdtemp(); sp = os.path.join(td, 'spec.json'); res = os.path.join(td, 'res.json')
    json.dump(spec, open(sp, 'w'))
    subprocess.run([sys.executable, WORKER, 'rfgeom', '--board', board,
                    '--spec', sp, '--result', res], capture_output=True, timeout=600, check=True)
    return json.load(open(res))['RF']


def test_gate11_foreign_via_on_reference_layer():
    if not _pcbnew_available(): print('skip'); return
    bd = _B(); p = bd.p
    bd.zone('GND', p.In1_Cu, 2, 2, 58, 38)
    bd.seg('RF', p.F_Cu, 5, 20, 30, 20)
    bd.via('CLK', 15, 20.5)                      # foreign via inside the band
    bd.seg('CLK', p.In1_Cu, 15, 10, 15, 19.5)    # foreign track control
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 't.kicad_pcb'); bd.save(path)
        f = _facts(path)
    kinds = {(e['net'], e['kind']) for e in f['foreign_under']}
    assert ('CLK', 'track') in kinds, kinds      # control: seen both ways
    assert ('CLK', 'via') in kinds, kinds        # #91

