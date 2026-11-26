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

"""Build the engineered-nets GUI fixture board (run once on a
KiCad 10 host; the outputs are committed as test fixtures).

fixture.kicad_pcb: a small 2-layer board whose nets exercise every
engineered-nets section -- RF_IN (impedance, passes), RF_FAIL
(impedance, deliberately missed target), CLK_P/CLK_N (differential),
HV_A/HV_B (two HV domains), LEN_A/LEN_B (length group), QUIET_X
(quiet), VCC5/GND (power tree; U17 also carries an AIRuleDecapMm
property for the #42 display).  The stackup block is spliced in
verbatim from a KiCad 10 demo board (a real KiCad-saved 2-layer
stackup) because pcbnew cannot author one headless.

Usage: python3 build_fixture.py [demo_board.kicad_pcb]
"""
import os, sys
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fixture.kicad_pcb')
MM = 1e6

# (ref, [(pad_no, net, x, y, w, h), ...])
PARTS = [
    ('U1', [('1', 'RF_IN', 12, 10, 1.6, 1.6)]),
    ('U2', [('1', 'RF_IN', 34, 10, 1.6, 1.6)]),
    ('U3', [('1', 'RF_FAIL', 12, 17, 1.6, 1.6)]),
    ('U4', [('1', 'RF_FAIL', 34, 17, 1.6, 1.6)]),
    ('U5', [('1', 'CLK_P', 12, 26, 1.2, 1.2), ('2', 'CLK_N', 12, 28.6, 1.2, 1.2)]),
    ('U6', [('1', 'CLK_P', 34, 26, 1.2, 1.2), ('2', 'CLK_N', 34, 28.6, 1.2, 1.2)]),
    ('U7', [('1', 'LEN_A', 12, 36, 1.6, 1.6)]),
    ('U8', [('1', 'LEN_A', 30, 36, 1.6, 1.6)]),
    ('U9', [('1', 'LEN_B', 12, 41, 1.6, 1.6)]),
    ('U10', [('1', 'LEN_B', 38, 41, 1.6, 1.6)]),
    ('U11', [('1', 'HV_A', 48, 10, 1.6, 1.6)]),
    ('U12', [('1', 'HV_A', 66, 10, 1.6, 1.6)]),
    ('U13', [('1', 'HV_B', 48, 13.5, 1.6, 1.6)]),  # 1.9 mm pad-edge gap to HV_A: inside the 400 V requirement -- the deliberate HV failure
    ('U14', [('1', 'HV_B', 66, 13.5, 1.6, 1.6)]),
    ('U15', [('1', 'QUIET_X', 48, 28, 1.6, 1.6)]),
    ('U16', [('1', 'QUIET_X', 58, 28, 1.6, 1.6)]),
    ('U17', [('1', 'VCC5', 48, 38, 1.6, 1.6), ('2', 'GND', 48, 41, 1.6, 1.6)]),
    ('U18', [('1', 'VCC5', 66, 38, 1.6, 1.6), ('2', 'GND', 66, 41, 1.6, 1.6)]),
]


def get_net(board, name):
    net = board.FindNet(name)
    if net is None:
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
    return net


def build():
    board = pcbnew.BOARD()
    for x1, y1, x2, y2 in ((5, 4, 71, 4), (71, 4, 71, 45), (71, 45, 5, 45), (5, 45, 5, 4)):
        sh = pcbnew.PCB_SHAPE(board)
        sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
        sh.SetStart(pcbnew.VECTOR2I(int(x1 * MM), int(y1 * MM)))
        sh.SetEnd(pcbnew.VECTOR2I(int(x2 * MM), int(y2 * MM)))
        sh.SetLayer(pcbnew.Edge_Cuts)
        sh.SetWidth(int(0.1 * MM))
        board.Add(sh)
    for ref, pads in PARTS:
        fp = pcbnew.FOOTPRINT(board)
        fp.SetReference(ref)
        fp.SetValue('FIXTURE')
        fp.SetPosition(pcbnew.VECTOR2I(0, 0))
        for num, net_name, x, y, w, h in pads:
            pad = pcbnew.PAD(fp)
            pad.SetNumber(num)
            pad.SetShape(pcbnew.PAD_SHAPE_RECT)
            pad.SetSize(pcbnew.VECTOR2I(int(w * MM), int(h * MM)))
            pad.SetPosition(pcbnew.VECTOR2I(int(x * MM), int(y * MM)))
            ls = pcbnew.LSET()
            ls.AddLayer(pcbnew.F_Cu)
            ls.AddLayer(pcbnew.F_Mask)
            ls.AddLayer(pcbnew.F_Paste)
            pad.SetLayerSet(ls)
            pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            pad.SetNet(get_net(board, net_name))
            fp.Add(pad)
        # courtyard: pads' bbox + 0.5 mm (a footprint without one
        # draws a missing_courtyard DRC warning per part)
        xs = [p[2] for p in pads]; ys = [p[3] for p in pads]
        ws = [p[4] for p in pads]; hs = [p[5] for p in pads]
        x0 = min(x - w / 2 for x, w in zip(xs, ws)) - 0.5
        x1 = max(x + w / 2 for x, w in zip(xs, ws)) + 0.5
        y0 = min(y - h / 2 for y, h in zip(ys, hs)) - 0.5
        y1 = max(y + h / 2 for y, h in zip(ys, hs)) + 0.5
        cy = pcbnew.PCB_SHAPE(fp)
        cy.SetShape(pcbnew.SHAPE_T_RECT)
        cy.SetStart(pcbnew.VECTOR2I(int(x0 * MM), int(y0 * MM)))
        cy.SetEnd(pcbnew.VECTOR2I(int(x1 * MM), int(y1 * MM)))
        cy.SetLayer(pcbnew.F_CrtYd)
        cy.SetWidth(int(0.05 * MM))
        fp.Add(cy)
        board.Add(fp)
    pcbnew.SaveBoard(OUT, board)
    print('saved', OUT)


def _block(text, start):
    # text[start] == '(' -> (block_text, end_index) by paren balance,
    # ignoring parens inside quoted strings.
    depth = 0; i = start; instr = False
    while i < len(text):
        c = text[i]
        if instr:
            if c == '\\':
                i += 2; continue
            if c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return text[start:i + 1], i + 1
        i += 1
    raise ValueError('unbalanced')


def inject_stackup(demo_path):
    demo = open(demo_path).read()
    di = demo.index('(stackup')
    block, _ = _block(demo, di)
    text = open(OUT).read()
    if '(stackup' in text:
        print('stackup already present')
        return
    si = text.index('(setup')
    # insert the block right after the '(setup' token
    text = text[:si + len('(setup')] + '\n    ' + block + text[si + len('(setup'):]
    open(OUT, 'w').write(text)
    print('stackup injected from', demo_path)


def inject_rule_property():
    # U17 gains (property "AIRuleDecapMm" "3.0" ...) before its
    # first pad -- the #42 read-only display + audit fixture.
    text = open(OUT).read()
    anchor = '(footprint'
    idx = text.index('"U17"')
    # find the footprint block containing the U17 reference property
    start = text.rindex('(footprint', 0, idx)
    pad_at = text.index('(pad', start)
    prop = ('(property "AIRuleDecapMm" "3.0" (at 0 0 0) '
            '(layer "F.SilkS") (uuid "11111111-2222-3333-4444-555555555555") '
            '(effects (font (size 1 1) (thickness 0.15))))\n    ')
    text = text[:pad_at] + prop + text[pad_at:]
    open(OUT, 'w').write(text)
    print('AIRuleDecapMm injected on U17')


if __name__ == '__main__':
    build()
    inject_stackup(sys.argv[1] if len(sys.argv) > 1 else '/usr/share/kicad/demos/ecc83/ecc83-pp.kicad_pcb')
    inject_rule_property()
    b = pcbnew.LoadBoard(OUT)
    nets = sorted({p.GetNetname() for fp in b.GetFootprints() for p in fp.Pads() if p.GetNetname()})
    print('verify: footprints=%d nets=%s' % (len(b.GetFootprints()), nets))
