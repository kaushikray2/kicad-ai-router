"""Power-tree construction for #68 (P2.9) from a KiCad schematic
netlist export (kicad-cli sch export netlist, kicadsexpr format).

Model (spike-proven extraction + the ticket's WCCA rules + the
sizing annex on #68): nets are nodes; a component spanning two
power nets is an edge (regulator = transformer, conduit = resistive
with a rating check, protective device = floor); loads hang off
their net; sources inject at their net.  Pin types classify:
power_out = source, power_in = load, output on a power net =
candidate source (flagged), everything else conduit.  The return
net (GND by default) is excluded from the rail set; dangling
power_in nets are flagged, never dropped; a rail with no typed
source takes the root declared in its marking.

Design currents are EVA sums per operating mode: a load's `modes`
tag lists the modes it is active in (untagged = all modes), and the
design value is the max over modes -- mutually exclusive branches
are therefore never summed.  Load kinds corner as: constant-current
as declared; resistive scaled to Vmax; constant-power at Vmin.
A load with no declared current is UNKNOWN: the rail reports its
known sum plus the unknown list, and no current is invented.

Regulator types are buck|ldo|switch.  A BOOST is declared as
'buck': the edge current is a power balance
(Pout / (eta * Vin_min), with Vout at its MAXIMUM), which
holds for a boost exactly as for a buck (#136).
"""
import math


class PowerTreeError(Exception):
    pass


# ---------------------------------------------------------------- S-expr
def _tokenise(text):
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
        elif c in '()':
            toks.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == '\\' and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            toks.append(('str', ''.join(buf)))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()':
                j += 1
            toks.append(text[i:j])
            i = j
    return toks


def parse_sexpr(text):
    toks = _tokenise(text)
    pos = [0]

    def _parse():
        t = toks[pos[0]]
        pos[0] += 1
        if t == '(':
            out = []
            while pos[0] < len(toks) and toks[pos[0]] != ')':
                out.append(_parse())
            pos[0] += 1  # consume ')'
            return out
        if isinstance(t, tuple):
            return t[1]
        return t
    return _parse()


def _children(node, head):
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _child(node, head):
    for c in _children(node, head):
        return c
    return None


def _val(node, head, default=None):
    c = _child(node, head)
    return c[1] if c is not None and len(c) > 1 else default


def parse_netlist(text):
    """Parse a kicadsexpr netlist export into components, libpart pin
    types and net membership."""
    root = parse_sexpr(text)
    if not (isinstance(root, list) and root and root[0] == 'export'):
        raise PowerTreeError('not a kicad netlist export')
    components = {}
    for comp in _children(_child(root, 'components') or [], 'comp'):
        ref = _val(comp, 'ref')
        if not ref:
            continue
        libsrc = _child(comp, 'libsource') or []
        sheet = _child(comp, 'sheetpath') or []
        components[ref] = {
            'value': _val(comp, 'value'),
            'lib': _val(libsrc, 'lib'), 'part': _val(libsrc, 'part'),
            'sheetpath': _val(sheet, 'names')}
    libparts = {}
    for lp in _children(_child(root, 'libparts') or [], 'libpart'):
        key = (_val(lp, 'lib'), _val(lp, 'part'))
        pins = {}
        pins_node = _child(lp, 'pins') or []
        for pin in _children(pins_node, 'pin'):
            num = _val(pin, 'num')
            if num is not None:
                pins[str(num)] = _val(pin, 'type')
        libparts[key] = pins
    nets = {}
    for net in _children(_child(root, 'nets') or [], 'net'):
        name = _val(net, 'name')
        if name is None:
            continue
        members = []
        for node in _children(net, 'node'):
            members.append((_val(node, 'ref'), str(_val(node, 'pin'))))
        nets[name] = members
    return {'components': components, 'libparts': libparts, 'nets': nets}


# ---------------------------------------------------------------- tree
def _pin_type(parsed, ref, pin):
    comp = parsed['components'].get(ref) or {}
    pins = parsed['libparts'].get((comp.get('lib'), comp.get('part'))) or {}
    return pins.get(str(pin)) or 'unknown'


def build_tree(parsed, power_entries, return_net='GND'):
    """power_entries: the markings 'power' list (validated dicts).
    Returns the tree dict described in the module docstring."""
    by_net = {e['net']: e for e in power_entries or []}

    def _declared(ref, sect):
        for e in power_entries or []:
            if ref in (e.get(sect) or {}):
                return e[sect][ref]
        return None

    def _is_edge_ref(ref):
        if ref in declared_edge_refs:
            return True
        src_nets = {n for n, pins in net_pins.items()
                    if any(r == ref and t == 'power_out' for (r, _p, t) in pins)}
        load_nets = {n for n, pins in net_pins.items()
                     if any(r == ref and t == 'power_in' for (r, _p, t) in pins)}
        return bool(src_nets) and bool(load_nets - src_nets)

    # classify pins per net
    net_pins = {}
    for name, members in parsed['nets'].items():
        net_pins[name] = [(ref, pin, _pin_type(parsed, ref, pin))
                          for (ref, pin) in members]
    power_nets = [n for n, pins in net_pins.items()
                  if n != return_net
                  and any(t in ('power_in', 'power_out') for (_r, _p, t) in pins)]
    # closure: a pure conduit net (an unfused/fused input stub carries
    # no typed pins at all) joins the set when a component DECLARED as
    # regulator/conduit/protective in the markings joins it to a
    # power net -- the fuse's upstream net is part of the power path.
    declared_edge_refs = set()
    for e in power_entries or []:
        for sect in ('regulators', 'conduits', 'protective'):
            declared_edge_refs.update((e.get(sect) or {}).keys())
    declared_reg_refs = set()
    for e in power_entries or []:
        declared_reg_refs.update((e.get('regulators') or {}).keys())
    changed = True
    while changed:
        changed = False
        members = set(power_nets)
        for n, pins in net_pins.items():
            if n in members or n == return_net:
                continue
            join = False
            for (r, _p, t) in pins:
                if r not in declared_edge_refs:
                    continue
                # #135 (B10): a declared regulator must not pull a net
                # into the power set through a NON-power pin (FB, EN,
                # SW, BOOT, PG) -- those nets are not rails, and once
                # they joined, the regulator spanned 3+ 'rails' and
                # got no edge at all.  Declared conduits/protective
                # keep the old behaviour: their pins are passive by
                # nature (the unfused input stub this closure is for).
                if r in declared_reg_refs and t not in ('power_in', 'power_out'):
                    continue
                if any(m in members for m in
                       {nn for nn, pp in net_pins.items()
                        if any(rr == r for (rr, _pp, _tt) in pp)}):
                    join = True
                    break
            if join:
                power_nets.append(n)
                changed = True
    flags = []
    rails = {}
    for n in power_nets:
        pins = net_pins[n]
        entry = by_net.get(n) or {}
        sources = [{'ref': r, 'pin': p} for (r, p, t) in pins if t == 'power_out']
        cand = [{'ref': r, 'pin': p} for (r, p, t) in pins if t == 'output']
        load_refs = sorted({r for (r, _p, t) in pins if t == 'power_in'})
        # a component that is an edge (declared regulator/conduit/
        # protective, or a source on one rail and a load on another)
        # is not a load even though it has power_in pins here -- the
        # LDO's input pin is the classic case.
        load_refs = [r for r in load_refs if not _is_edge_ref(r)]
        declared_loads = entry.get('loads') or {}
        loads, unknown = {}, []
        for ref in load_refs:
            spec = declared_loads.get(ref)
            comp = parsed['components'].get(ref) or {}
            if spec is None:
                unknown.append(ref)
            else:
                loads[ref] = {'peak_a': spec['peak_a'],
                              'sustained_a': spec['sustained_a'],
                              'kind': spec.get('kind', 'constant_current'),
                              'modes': spec.get('modes'),
                              'duty': spec.get('duty', 1.0),
                              'source': spec.get('source', 'user'),
                              'citation': spec.get('citation'),
                              'sheetpath': comp.get('sheetpath')}
        for ref in declared_loads:
            if ref not in load_refs:
                flags.append({'kind': 'declared_load_without_supply_pin',
                              'net': n, 'ref': ref})
        rails[n] = {'sources': sources, 'candidate_sources': cand,
                    'loads': loads, 'unknown_loads': unknown,
                    'root': entry.get('root'),
                    'voltage': entry.get('voltage'),
                    'drop_budget_v': entry.get('drop_budget_v'),
                    'complete': not unknown}
        if cand and not sources:
            flags.append({'kind': 'candidate_source_only', 'net': n,
                          'refs': [c['ref'] for c in cand]})
    # nets that carry power_in pins but no power net status (return net
    # excluded by construction): nets whose ONLY content is one ref's
    # power_in pins and nothing else are pseudo-rails (a regulator cap
    # pin, a dangling supply pin) -- flag them, never silently absorb.
    for n, pins in net_pins.items():
        if n in rails or n == return_net:
            continue
        if any(t == 'power_in' for (_r, _p, t) in pins):
            refs = {r for (r, _p, _t) in pins}
            if len(refs) <= 1:
                flags.append({'kind': 'dangling_power_in', 'net': n,
                              'refs': sorted(refs)})
    # edges: components spanning two rails
    comp_nets = {}
    for n in power_nets:
        for (r, _p, _t) in net_pins[n]:
            comp_nets.setdefault(r, set()).add(n)
    edges = []
    # #135 (B10): a DECLARED regulator's edge is built from its input
    # and output rails ONLY -- the nets where it has power_in /
    # power_out pins.  Its other nets (FB, EN, SW, BOOT, PG) never
    # take part.  A declared regulator that yields no unique in->out
    # pair is a fail-closed flag, and every rail it was expected to
    # feed or draw from is marked incomplete: before this, such a
    # regulator silently vanished from the tree while its input rail
    # still reported complete with the input current missing.
    reg_handled = set()
    for ref in sorted(declared_reg_refs):
        spec = _declared(ref, 'regulators')
        in_rails = [n for n in power_nets
                    if any(r == ref and t == 'power_in'
                           for (r, _p, t) in net_pins[n])]
        out_rails = [n for n in power_nets
                     if any(r == ref and t == 'power_out'
                            for (r, _p, t) in net_pins[n])]
        pairs = [(a, b) for a in in_rails for b in out_rails if a != b]
        if len(pairs) == 1:
            a, b = pairs[0]
            edges.append({'ref': ref, 'kind': spec['type'], 'spec': spec,
                          'between': sorted([a, b]), 'from': a, 'to': b})
        else:
            flags.append({'kind': 'regulator_without_edge', 'ref': ref,
                          'in_rails': in_rails, 'out_rails': out_rails})
            for n in set(in_rails) | set(out_rails):
                rails[n]['complete'] = False
        reg_handled.add(ref)
    pin_count = {}
    for _pins in net_pins.values():
        for (r, _p, _t) in _pins:
            pin_count[r] = pin_count.get(r, 0) + 1
    for ref, ns in sorted(comp_nets.items()):
        if ref in reg_handled:
            continue
        if len(ns) != 2:
            continue
        a, b = sorted(ns)
        reg = _declared(ref, 'regulators')
        prot = _declared(ref, 'protective')
        cond = _declared(ref, 'conduits')
        if reg:
            kind = reg['type']
            spec = reg
        elif prot:
            kind, spec = 'protective', prot
        elif cond:
            kind, spec = 'conduit', cond
        else:
            src_on = [n for n in (a, b)
                      if any(r == ref and t == 'power_out'
                             for (r, _p, t) in net_pins[n])]
            load_on = [n for n in (a, b)
                       if any(r == ref and t == 'power_in'
                              for (r, _p, t) in net_pins[n])]
            if src_on and load_on and src_on[0] != load_on[0]:
                kind, spec = 'switch', {}
                flags.append({'kind': 'regulator_type_undeclared', 'ref': ref,
                              'note': 'pass-through (switch) assumed'})
            else:
                # #136 (B11): infer 'conduit' only for a two-pin
                # passive (ferrite, 0-ohm, a divider resistor) --
                # the shape that really is a pass-through.  Any
                # other undeclared part spanning two rails (an MCU
                # with VDD+VDDA, a level shifter, a dual-supply
                # op-amp) is NOT a conduit: flag it and build no
                # edge, instead of silently double-counting the
                # downstream current upstream and seeding the
                # part's pin as a source in powerplan.
                if pin_count.get(ref) == 2:
                    kind, spec = 'conduit', {}
                else:
                    flags.append({'kind': 'undeclared_multi_rail_part',
                                  'ref': ref, 'nets': sorted(ns),
                                  'note': 'no conduit inferred; declare '
                                          'the part or split its supplies'})
                    continue
        # direction from pin evidence where it exists
        frm = to = None
        src_on = [n for n in (a, b) if any(r == ref and t == 'power_out'
                                           for (r, _p, t) in net_pins[n])]
        load_on = [n for n in (a, b) if any(r == ref and t == 'power_in'
                                            for (r, _p, t) in net_pins[n])]
        if src_on and load_on and src_on[0] != load_on[0]:
            frm, to = load_on[0], src_on[0]
        edges.append({'ref': ref, 'kind': kind, 'spec': spec,
                      'between': [a, b], 'from': frm, 'to': to})
    # roots + BFS direction for undecided edges
    roots = {n for n, r in rails.items()
             if r['sources'] or r['candidate_sources'] or r['root']}
    adj = {n: set() for n in rails}
    for e in edges:
        a, b = e['between']
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    dist = {n: 0 for n in roots}
    queue = list(roots)
    while queue:
        cur = queue.pop(0)
        for nb in adj.get(cur, ()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                queue.append(nb)
    for e in edges:
        if e['from'] is None:
            a, b = e['between']
            da, db = dist.get(a), dist.get(b)
            if da is not None and db is not None and da != db:
                e['from'], e['to'] = (a, b) if da < db else (b, a)
            elif da is not None or db is not None:
                e['from'], e['to'] = (a, b) if db is None else (b, a)
            else:
                flags.append({'kind': 'edge_direction_unknown', 'ref': e['ref']})
    for n, r in rails.items():
        refs = set(r['loads']) | set(r['unknown_loads'])
        if (len(refs) <= 1 and not r['sources'] and not r['candidate_sources']
                and not r['root']
                and not any(n in e['between'] for e in edges)):
            flags.append({'kind': 'dangling_power_in', 'net': n, 'refs': sorted(refs)})
    fed = {e['to'] for e in edges if e['from'] is not None}
    for n, r in rails.items():
        if not r['sources'] and not r['candidate_sources'] and not r['root'] and n not in fed:
            flags.append({'kind': 'rail_without_source', 'net': n})
    for n, r in rails.items():
        pins = net_pins[n]
        r['load_pins'] = {ref: [(rr, p) for (rr, p, t) in pins if rr == ref]
                          for ref in set(r['loads']) | set(r['unknown_loads'])}
        src_refs = {s['ref'] for s in r['sources']} | {s['ref'] for s in r['candidate_sources']}
        r['source_pins'] = {ref: [(rr, p) for (rr, p, t) in pins if rr == ref]
                            for ref in src_refs}
    for e in edges:
        for key, net in (('in_pins', e['from']), ('out_pins', e['to'])):
            e[key] = ([(r, p) for (r, p, t) in net_pins[net] if r == e['ref']]
                      if net else [])
    return_pins = {}
    for (r, p, t) in net_pins.get(return_net, []):
        return_pins.setdefault(r, []).append((r, p))
    return {'rails': rails, 'edges': edges, 'flags': flags,
            'return_net': return_net, 'return_pins': return_pins,
            'components': parsed['components']}


# ---------------------------------------------------------------- currents
def _cornered(load, rail_voltage, which):
    """One load's current at the WCCA corner for its kind."""
    i = load['peak_a'] if which == 'peak_a' else load['sustained_a']
    if which == 'sustained_a':
        # #136 (B11): heating goes with I^2 R, so the thermal
        # equivalent of a duty-cycled load is I*sqrt(D), not the
        # I*D average (4 A at 25% duty heats like 2 A, not 1 A).
        i = i * math.sqrt(load.get('duty', 1.0))
    kind = load.get('kind', 'constant_current')
    if kind == 'constant_current' or not rail_voltage:
        return i
    vnom = rail_voltage['nominal_v']
    if kind == 'resistive':
        return i * rail_voltage['max_v'] / vnom
    if kind == 'constant_power':
        return i * vnom / rail_voltage['min_v']
    return i


def compute_currents(tree):
    """Fill design currents (peak + sustained) per rail and per edge,
    per mode and as the max-over-modes design value.  Mutates and
    returns the tree."""
    rails, edges = tree['rails'], tree['edges']
    modes = set()
    for r in rails.values():
        for load in r['loads'].values():
            modes.update(load.get('modes') or [])
    mode_list = [None] + sorted(modes)
    out_edges = {}
    for e in edges:
        if e['from'] is not None:
            out_edges.setdefault(e['from'], []).append(e)
    flags = tree['flags']

    def rail_total(net, mode, which, stack):
        if net in stack:
            flags.append({'kind': 'cycle', 'net': net})
            return 0.0
        r = rails[net]
        total = 0.0
        for ref, load in r['loads'].items():
            lm = load.get('modes')
            if lm is not None and (mode is None or mode not in lm):
                continue  # tagged loads are inactive outside their modes
            if not r['voltage'] and load.get('kind') not in (None, 'constant_current'):
                flags.append({'kind': 'voltage_undeclared_kind_treated_cc',
                              'net': net, 'ref': ref})
            total += _cornered(load, r['voltage'], which)
        for e in out_edges.get(net, []):
            total += edge_input(e, mode, which, stack | {net})
        return total

    def edge_input(e, mode, which, stack):
        down = rail_total(e['to'], mode, which, stack) if e['to'] in rails else 0.0
        kind = e['kind']
        if kind == 'protective':
            return max(down, e['spec'].get('rating_a', 0.0))
        if kind == 'ldo':
            return down + (e['spec'].get('iq_a') or 0.0)
        if kind == 'buck':
            vin = (rails.get(e['from']) or {}).get('voltage')
            vout = (rails.get(e['to']) or {}).get('voltage')
            if not vin or not vout:
                flags.append({'kind': 'buck_voltage_undeclared', 'ref': e['ref'],
                              'note': 'pass-through assumed'})
                return down
            # #136 (B11): worst-case input current is at MAXIMUM
            # output voltage, not nominal.
            return vout['max_v'] * down / (e['spec']['eta_min'] * vin['min_v'])
        return down  # switch / conduit: pass-through

    for net, r in rails.items():
        r['design'] = {}
        for which in ('peak_a', 'sustained_a'):
            by_mode = {('BASE' if m is None else m): rail_total(net, m, which, frozenset())
                       for m in mode_list}
            r['design'][which] = {'by_mode': {k: round(v, 6) for k, v in by_mode.items()},
                                  'design_a': round(max(by_mode.values()), 6) if by_mode else 0.0}
        r['loads'] = {ref: dict(load,
                                peak_cornered_a=round(_cornered(load, r['voltage'], 'peak_a'), 6),
                                sustained_cornered_a=round(_cornered(load, r['voltage'], 'sustained_a'), 6))
                      for ref, load in r['loads'].items()}
    for e in edges:
        e['design'] = {}
        if e['from'] is None or e['to'] not in rails:
            e['design'] = {'peak_a': None, 'sustained_a': None}
            continue
        per_kind = {}
        for which in ('peak_a', 'sustained_a'):
            vals = {('BASE' if m is None else m): edge_input(e, m, which, frozenset())
                    for m in mode_list}
            per_kind[which] = vals
            e['design'][which] = round(max(vals.values()), 6) if vals else 0.0
        e['by_mode'] = {m: {'peak_a': round(per_kind['peak_a'][m], 6),
                            'sustained_a': round(per_kind['sustained_a'][m], 6)}
                        for m in per_kind['peak_a']}
    tree['modes'] = ['BASE' if m is None else m for m in mode_list]
    # de-duplicate flags raised once per mode/kind pass
    seen, uniq = set(), []
    for f in flags:
        k = tuple(sorted((kk, str(vv)) for kk, vv in f.items()))
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    tree['flags'] = uniq
    return tree
