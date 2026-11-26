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

"""Issue #119: cmd_strip (the default first step of every run) used
to remove EVERY track, arc and via, locked or not -- hand-routed
critical nets, stitching vias and thermal-via arrays silently
vanished from the delivered board, and neither DRC nor the gate
could see the loss. Strip must remove only unlocked tracks/arcs
plus the vias that removal leaves dangling; locked items and free
vias (stitching / thermal arrays) survive, and the summary census
(locked_items / free_vias) must report them for the gate's
invariance check.
Run: python3 tests/test_strip_keeps_locked.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

def NM(v): return int(round(v * 1e6))

d = tempfile.mkdtemp()
b = pcbnew.BOARD()
def net(name):
    n = pcbnew.NETINFO_ITEM(b, name); b.Add(n); return n.GetNetCode()
GND = net('GND'); SIG = net('SIG'); SIG2 = net('SIG2')

# GND zone on F.Cu (the free-via fallback: a via whose net has a zone
# on one of its layers counts as free even without the free flag)
z = pcbnew.ZONE(b); z.SetLayer(pcbnew.F_Cu); z.SetNetCode(GND)
out = z.Outline(); out.NewOutline()
for (x, y) in ((5, 5), (35, 5), (35, 35), (5, 35)): out.Append(NM(x), NM(y))
b.Add(z)

# QFN-ish footprint with an exposed GND pad at (20,20)
fp = pcbnew.FOOTPRINT(b); fp.SetPosition(pcbnew.VECTOR2I(NM(20), NM(20))); b.Add(fp)
pad = pcbnew.PAD(fp); pad.SetShape(pcbnew.PAD_SHAPE_RECT); pad.SetSize(pcbnew.VECTOR2I(NM(5), NM(5)))
pad.SetPosition(pcbnew.VECTOR2I(NM(20), NM(20))); _ls = pcbnew.LSET(); _ls.AddLayer(pcbnew.F_Cu); pad.SetLayerSet(_ls); pad.SetNetCode(GND)
fp.Add(pad)

def via(code, x, y, free=False, locked=False):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y))); v.SetDrill(NM(0.3)); v.SetWidth(NM(0.6))
    v.SetNetCode(code); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    if free: v.SetIsFree(True)
    if locked: v.SetLocked(True)
    b.Add(v); return v

def track(code, x1, y1, x2, y2, locked=False):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(NM(x1), NM(y1))); t.SetEnd(pcbnew.VECTOR2I(NM(x2), NM(y2)))
    t.SetWidth(NM(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(code)
    if locked: t.SetLocked(True)
    b.Add(t); return t

# 3x3 thermal-via array under the exposed pad, marked free
for dx in (-1.5, 0, 1.5):
    for dy in (-1.5, 0, 1.5):
        via(GND, 20 + dx, 20 + dy, free=True)
via(GND, 30, 30)                       # unflagged, but GND has an F.Cu zone -> free by fallback
track(SIG, 10, 40, 30, 40, locked=True)  # locked hand routing
via(SIG, 30, 40)                       # not free, but sits on the locked track's end -> connected, kept
track(SIG, 40, 40, 55, 40)             # unlocked routing -> removed
via(SIG, 55, 40)                       # only touched the unlocked track -> dangling after removal
via(SIG2, 60, 15)                      # touches nothing, no zone -> dangling
via(SIG2, 15, 55, locked=True)         # locked via -> kept

inp = os.path.join(d, 'in.kicad_pcb'); outp = os.path.join(d, 'out.kicad_pcb')
pcbnew.SaveBoard(inp, b)
res = w.cmd_strip(inp, outp)
b2 = pcbnew.LoadBoard(outp)
tracks2 = list(b2.GetTracks())
kept_locked = [t for t in tracks2 if t.IsLocked()]
kept_vias = [t for t in tracks2 if t.Type() == pcbnew.PCB_VIA_T]
kept_tracks = [t for t in tracks2 if t.Type() != pcbnew.PCB_VIA_T]
via_pos = {(round(t.GetPosition().x / 1e6, 2), round(t.GetPosition().y / 1e6, 2)) for t in kept_vias}

assert len(kept_tracks) == 1 and kept_tracks[0].IsLocked(), 'locked track must survive, got %d tracks' % len(kept_tracks)
assert len(kept_locked) == 2, 'locked track + locked via must survive, got %d locked items' % len(kept_locked)
thermal = {(20 + dx, 20 + dy) for dx in (-1.5, 0, 1.5) for dy in (-1.5, 0, 1.5)}
assert thermal <= via_pos, 'thermal array must survive: missing %s' % (thermal - via_pos)
assert (30.0, 30.0) in via_pos, 'zone-fallback free via must survive'
assert (30.0, 40.0) in via_pos, 'via on the kept locked track must survive'
assert (55.0, 40.0) not in via_pos, 'via left dangling by the removal must be removed'
assert (60.0, 15.0) not in via_pos, 'isolated non-free via must be removed'
assert res.get('kept_locked') == 2, 'strip must report kept_locked=2, got %r' % res
assert res.get('kept_free_vias') == 10, 'strip must report kept_free_vias=10 (9 flagged + 1 zone fallback), got %r' % res
assert res.get('removed_tracks_vias') == 3, 'exactly the unlocked track + 2 dangling vias are removed, got %r' % res

summ = w.cmd_summary(outp, fast=True)
assert summ.get('locked_items') == 2, 'summary census locked_items must be 2, got %r' % summ.get('locked_items')
assert summ.get('free_vias') == 10, 'summary census free_vias must be 10, got %r' % summ.get('free_vias')
# prune is the other removal path: a locked item in a dead component
# (touching no pad or zone) must still survive it
b3 = pcbnew.BOARD()
n3 = pcbnew.NETINFO_ITEM(b3, 'NET3'); b3.Add(n3); c3 = n3.GetNetCode()
def track3(x1, y1, x2, y2, locked=False):
    t = pcbnew.PCB_TRACK(b3)
    t.SetStart(pcbnew.VECTOR2I(NM(x1), NM(y1))); t.SetEnd(pcbnew.VECTOR2I(NM(x2), NM(y2)))
    t.SetWidth(NM(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(c3)
    if locked: t.SetLocked(True)
    b3.Add(t); return t
track3(10, 10, 20, 10, locked=True)
track3(30, 10, 40, 10)
v3 = pcbnew.PCB_VIA(b3); v3.SetPosition(pcbnew.VECTOR2I(NM(50), NM(10))); v3.SetDrill(NM(0.3)); v3.SetWidth(NM(0.6))
v3.SetNetCode(c3); v3.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v3.SetLocked(True); b3.Add(v3)
inp3 = os.path.join(d, 'prune-in.kicad_pcb'); outp3 = os.path.join(d, 'prune-out.kicad_pcb')
pcbnew.SaveBoard(inp3, b3)
res3 = w.cmd_prune(inp3, outp3)
b4 = pcbnew.LoadBoard(outp3)
left = list(b4.GetTracks())
assert res3.get('pruned') == 1, 'only the unlocked dead track is pruned, got %r' % res3
assert len(left) == 2 and all(t.IsLocked() for t in left), 'locked track + locked via survive prune, got %d items' % len(left)
print('PASS test_strip_keeps_locked')
