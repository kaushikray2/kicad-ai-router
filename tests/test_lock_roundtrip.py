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

"""Issue #50: cmd_lock_window must record EVERY item's lock state and
cmd_restore_locks must reapply them -- the delivered board must not
keep the window pass's locks. Regression test for the v1.10.1 first
cut, which keyed the record by str(m_Uuid): in KiCad 10 that is the
SWIG proxy repr (identical for every item), so the record collapsed
to a couple of keys and restore changed nothing. Keys must come from
m_Uuid.AsString().
Run: python3 tests/test_lock_roundtrip.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')  # v1.11.5 (#122): report a skip, not a collection error, outside KiCad
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def mm(v): return int(v * 1e6)
def track(b, code, x1, y1, x2, y2):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(mm(x1), mm(y1))); t.SetEnd(pcbnew.VECTOR2I(mm(x2), mm(y2)))
    t.SetWidth(mm(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(code)
    b.Add(t); return t

d = tempfile.mkdtemp()
b = pcbnew.BOARD()
net = pcbnew.NETINFO_ITEM(b, 'TESTNET'); b.Add(net); code = net.GetNetCode()
ta = track(b, code, 15, 15, 20, 15)          # inside corridor, unlocked
tb = track(b, code, 25, 25, 28, 25)          # inside corridor, USER-locked
tc = track(b, code, 80, 80, 90, 80)          # outside corridor, unlocked
tb.SetLocked(True)
orig = {t.m_Uuid.AsString(): bool(t.IsLocked()) for t in b.GetTracks()}
src = os.path.join(d, 'in.kicad_pcb'); pcbnew.SaveBoard(src, b)

res = w.cmd_lock_window(src, os.path.join(d, 'locked.kicad_pcb'), 'TESTNET', margin=6.0, fx=10.0, fy=10.0, tx=30.0, ty=30.0)
assert res['status'] == 'locked', res
assert res['kept_user_locked'] == 1, res           # the user's lock is never undone
states = json.load(open(res['locks_file']))
assert len(states) == 3, 'record must hold every item, got %d' % len(states)
bl = pcbnew.LoadBoard(os.path.join(d, 'locked.kicad_pcb'))
mid = {t.m_Uuid.AsString(): bool(t.IsLocked()) for t in bl.GetTracks()}
assert mid[tb.m_Uuid.AsString()] is True and mid[tc.m_Uuid.AsString()] is True and mid[ta.m_Uuid.AsString()] is False, mid

res2 = w.cmd_restore_locks(os.path.join(d, 'locked.kicad_pcb'), os.path.join(d, 'restored.kicad_pcb'), res['locks_file'])
assert res2['status'] == 'restored' and res2['changed'] == 1, res2   # only the outside track flips back
br = pcbnew.LoadBoard(os.path.join(d, 'restored.kicad_pcb'))
fin = {t.m_Uuid.AsString(): bool(t.IsLocked()) for t in br.GetTracks()}
assert fin == orig, (fin, orig)
print('lock round-trip OK')
