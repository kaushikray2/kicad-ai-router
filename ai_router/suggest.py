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

"""Deterministic Suggest-candidates detector (Pre-Exam Polish,
plan.md Section C).

Reads a board's net/pad structure and proposes markings the user
probably wants to declare -- differential pairs, power rails,
crystal load caps, RF feed chains, indexed bus groups, and
conservative HV candidates -- each with a confidence score for a
read-only checklist UI.

Hard rules (the plan's own): the detector NEVER writes anything
(it has no sidecar path at all), and it NEVER invents a current
or a voltage: power-rail candidates carry loads='unknown' (the
power tree's \"unknown\": true semantics) and HV candidates carry
peak_v='' for the user to fill in.

Placement note: plan.md names markings_model.py /
markings_dialog.py, which live on the engineered-nets branches;
on main the detector is this standalone module over the same
board data, ready to be wired into the markings dialog when that
line merges. The detection logic is pure functions over a plain
data dict (see load_board_data), so it is unit-testable without
pcbnew.

Data shape:
    {'nets': {net: [[ref, pin], ...]},
     'footprints': {ref: {'value': str, 'name': str}}}
"""
import os
import re


def load_board_data(board_path):
    """Board data via pcbnew, or None on ANY failure (same
    contract as board_facts.load_facts: detection is an aid,
    never a gate; pcbnew.LoadBoard segfaults on a bad path, so
    existence is checked first)."""
    try:
        if not board_path or not os.path.exists(board_path):
            return None
        import pcbnew
        b = pcbnew.LoadBoard(board_path)
        if b is None:
            return None
        nets = {}
        footprints = {}
        for fp in b.GetFootprints():
            ref = fp.GetReference()
            try:
                name = str(fp.GetFPID().GetLibItemName())
            except Exception:
                name = ''
            footprints[ref] = {'value': fp.GetValue() or '', 'name': name}
            for pad in fp.Pads():
                net = pad.GetNetname()
                if not net:
                    continue
                nets.setdefault(net, []).append([ref, pad.GetNumber()])
        return {'nets': nets, 'footprints': footprints}
    except Exception:
        return None


def _refs_of(endpoints):
    return set(r for r, _pin in endpoints)


def _is_ground_net(name):
    return 'GND' in (name or '')


def _nets_by_ref(data):
    out = {}
    for net, eps in data['nets'].items():
        for ref in _refs_of(eps):
            out.setdefault(ref, set()).add(net)
    return out


# ----------------------------------------------------------------
# Differential pairs: _P/_N (and _DP/_DN) nets sharing a stem and
# >= 2 endpoint components -- two nets that merely share a name
# shape but land on different parts are not a pair. #240: the
# USB spellings D+/D- and DP/DM pair too.
_DIFF_SUFFIXES = (('_DP', '_DN'), ('_DP', '_DM'), ('_P', '_N'),
                  ('+', '-'))


def detect_diff_pairs(data):
    nets = data['nets']
    out = []
    seen = set()
    for pos_sfx, neg_sfx in _DIFF_SUFFIXES:
        for name in nets:
            if not name.endswith(pos_sfx):
                continue
            stem = name[:-len(pos_sfx)]
            neg = stem + neg_sfx
            if neg not in nets or (name, neg) in seen:
                continue
            seen.add((name, neg))
            shared = sorted(_refs_of(nets[name]) & _refs_of(nets[neg]))
            if len(shared) < 2:
                continue
            conf = min(0.90 + 0.02 * (len(shared) - 2), 0.98)
            out.append({'kind': 'differential_pair',
                        'label': 'Differential pair %s / %s' % (name, neg),
                        'nets': [name, neg],
                        'shared_endpoints': shared,
                        'confidence': round(conf, 2)})
    return out


# ----------------------------------------------------------------
# Power rails: strongly-named supply nets feeding several parts.
# The candidate proposes the rail for the power tree with its
# loads UNKNOWN -- no current is ever suggested.
_RAIL_HEAD = re.compile(r'^(\+|VCC|VDD|VBAT|VBUS|VIN|VSYS)')
_RAIL_ANY = re.compile(r'(^|_)(VCC|VDD|VBAT|VBUS)(_|$)')
# #240: rails named by their voltage -- 3V3, 5V, 12V, 1V8 --
# carry no +/VCC/VDD head at all.
_RAIL_VOLT = re.compile(r'^\d+V\d*(?:_|$)')


def detect_power_rails(data):
    out = []
    for name, eps in data['nets'].items():
        if name == 'GND' or name.startswith('GND'):
            continue  # pours serve ground; the power tree is for rails
        if len(_refs_of(eps)) < 3:
            continue  # a rail feeds several loads; 1-2 endpoints is a stub
        if _RAIL_HEAD.match(name):
            conf = 0.90 if name.startswith('+') else 0.85
        elif _RAIL_VOLT.match(name):
            conf = 0.85
        elif _RAIL_ANY.search(name):
            conf = 0.80
        else:
            continue
        out.append({'kind': 'power_rail',
                    'label': 'Power rail %s (loads unknown)' % name,
                    'net': name,
                    'loads': 'unknown',
                    'endpoints': len(_refs_of(eps)),
                    'confidence': conf})
    return out


# ----------------------------------------------------------------
# Crystal + pF load caps: a crystal footprint whose nets are
# shared with small-pF capacitors -- the classic two-cap load
# arrangement (one cap still counts, at lower confidence).
_PF_VALUE = re.compile(r'(\d+(?:\.\d+)?)\s*pF')
_PF_BARE = re.compile(r'^(\d+(?:\.\d+)?)p$')
_MHZ_VALUE = re.compile(r'\d+(?:\.\d+)?\s*MHz', re.IGNORECASE)
_XTAL_WORD = re.compile(r'crystal|xtal', re.IGNORECASE)


def _pf_value(text):
    m = _PF_VALUE.search(text or '') or _PF_BARE.match((text or '').strip())
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if 0 < v <= 100.0 else None


def _is_crystal(ref, fp):
    text = '%s %s' % (fp.get('value', ''), fp.get('name', ''))
    if _XTAL_WORD.search(text):
        return True
    return ref.startswith('Y') and bool(_MHZ_VALUE.search(fp.get('value', '')))


def detect_crystals(data):
    by_ref = _nets_by_ref(data)
    out = []
    for ref, fp in sorted(data['footprints'].items()):
        if not _is_crystal(ref, fp):
            continue
        # Signal nets only: a crystal's ground pads (case / shield)
        # otherwise make every GND-shunted pF cap on the board a
        # false 'load cap' -- the STM32-RF measurement's C7/C9 were
        # exactly that (a VDD_PA decoupler and an RF shunt).
        xtal_nets = set(n for n in by_ref.get(ref, set())
                        if not _is_ground_net(n))
        if not xtal_nets:
            continue
        caps = []
        for cref, cfp in sorted(data['footprints'].items()):
            if not cref.startswith('C'):
                continue
            if _pf_value(cfp.get('value', '')) is None:
                continue
            if by_ref.get(cref, set()) & xtal_nets:
                caps.append(cref)
        if not caps:
            continue
        out.append({'kind': 'crystal_load_caps',
                    'label': 'Crystal %s with pF load cap(s) %s' % (ref, ', '.join(caps)),
                    'ref': ref,
                    'caps': caps,
                    'confidence': 0.90 if len(caps) >= 2 else 0.80})
    return out


# ----------------------------------------------------------------
# RF feed chains: an ANT-named net reaching an IC net through
# <= 2 series R/L/C links. The series parts plus any shunt
# R/L/C from the path nets to ground are the candidate pi /
# matching refs. Deliberately narrow: no ANT net, no candidate.
_ANT_NET = re.compile(r'(^|_)ANT|ANTENNA', re.IGNORECASE)


def _series_links(data):
    by_ref = _nets_by_ref(data)
    links = {}
    for ref, nets in by_ref.items():
        if ref[:1] not in 'RLC' or len(nets) != 2:
            continue
        a, b = sorted(nets)
        links.setdefault(a, []).append((ref, b))
        links.setdefault(b, []).append((ref, a))
    return links


def detect_rf_chains(data):
    by_ref = _nets_by_ref(data)
    links = _series_links(data)
    ic_nets = set()
    for ref, nets in by_ref.items():
        if ref.startswith('U'):
            ic_nets |= nets
    def _radio_net(n):
        return n in ic_nets and not (_RAIL_HEAD.match(n) or _RAIL_ANY.search(n))
    out = []
    for ant in sorted(n for n in data['nets'] if _ANT_NET.search(n)):
        if ant in ic_nets:
            continue  # the radio sits on the antenna net itself: no chain
        path_refs, path_nets = None, None
        for ref1, n1 in links.get(ant, []):
            if _radio_net(n1):
                path_refs, path_nets = [ref1], [ant, n1]
                break
            for ref2, n2 in links.get(n1, []):
                if _radio_net(n2) and n2 != ant:
                    path_refs, path_nets = [ref1, ref2], [ant, n1, n2]
                    break
            if path_refs:
                break
        if not path_refs:
            continue
        pi_refs = list(path_refs)
        for net in path_nets[:-1]:
            for ref, other in links.get(net, []):
                if 'GND' in other and ref not in pi_refs:
                    pi_refs.append(ref)
        out.append({'kind': 'rf_chain',
                    'label': 'RF chain %s -> %s via %s' % (ant, path_nets[-1], ', '.join(path_refs)),
                    'antenna_net': ant,
                    'path_nets': path_nets,
                    'pi_candidate_refs': pi_refs,
                    'confidence': 0.80 if len(path_refs) == 1 else 0.75})
    return out


# ----------------------------------------------------------------
# Indexed bus groups: >= 3 nets sharing an alphabetic base and a
# numeric tail (ADDR0..7, DQ0..3, ...). Bases may not contain
# digits, which keeps connector/pad pseudo-nets out.
_BUS_TAIL = re.compile(r'^([A-Za-z][A-Za-z_]*?)(\d+)$')


def detect_bus_groups(data):
    groups = {}
    for name in data['nets']:
        m = _BUS_TAIL.match(name)
        if not m or len(m.group(1)) < 2:
            continue
        groups.setdefault(m.group(1), {})[int(m.group(2))] = name
    out = []
    for base, by_idx in sorted(groups.items()):
        if len(by_idx) < 3:
            continue
        nets = [by_idx[i] for i in sorted(by_idx)]
        # #240: indexed lookalikes on unrelated parts are not
        # a bus -- require at least one endpoint ref shared by
        # two or more of the group's nets (a real bus fans out
        # of, or into, at least one common device; LED1..LED4
        # each sitting on a different part share nothing).
        counts = {}
        for n in nets:
            for r in _refs_of(data['nets'][n]):
                counts[r] = counts.get(r, 0) + 1
        if not any(c >= 2 for c in counts.values()):
            continue
        out.append({'kind': 'bus_length_group',
                    'label': 'Bus group %s (%d nets)' % (base, len(nets)),
                    'base': base,
                    'nets': nets,
                    'confidence': 0.85})
    return out


# ----------------------------------------------------------------
# HV candidates: conservative name signals ONLY (HV token,
# MAINS, AC_L/AC_N, or an explicit >= 60 V in the net name).
# peak_v is always left blank -- the detector never invents a
# voltage; the user confirms and fills it in.
_HV_TOKEN = re.compile(r'(^|_)HV(_|$)|MAINS|(^|_)AC_[LN](_|$)')
# #240: 2-4 digits anchored at a non-digit boundary -- the
# old 2-3 digit window matched only the inner '000V' of
# '1000V_DC' (0 < 60) and missed kilovolt-class nets.
_VOLT_IN_NAME = re.compile(r'(?<![0-9])(\d{2,4})\s?V(?![0-9])')


def detect_hv_candidates(data):
    out = []
    for name in sorted(data['nets']):
        strong = bool(_HV_TOKEN.search(name))
        if not strong:
            m = _VOLT_IN_NAME.search(name)
            strong = bool(m) and int(m.group(1)) >= 60
        if not strong:
            continue
        out.append({'kind': 'hv_candidate',
                    'label': 'Possible HV net %s (peak voltage not declared)' % name,
                    'net': name,
                    'peak_v': '',
                    'confidence': 0.70})
    return out


_DETECTORS = (detect_diff_pairs, detect_power_rails, detect_crystals,
              detect_rf_chains, detect_bus_groups, detect_hv_candidates)


def suggest_candidates(data):
    """All candidates, best confidence first. Read-only: this
    function returns suggestions and writes nothing."""
    if not data:
        return []
    out = []
    for fn in _DETECTORS:
        out.extend(fn(data))
    out.sort(key=lambda c: (-c['confidence'], c['kind'], c['label']))
    return out
