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

"""Pure model layer for the engineered-nets GUI (dialog Screen 1
chips, the Markings editor, and the post-run verdict bar / report).

No wx, no pcbnew: the dialogs bind controls to the form dicts and
functions defined here, so the whole layer is unit-testable headless
and the acceptance runs can author a sidecar through exactly the
code path the dialog uses.  Every save goes through
markings.save_markings() (which validates first), so the editor
cannot write a sidecar the pipeline would reject.

The three-tier taxonomy is the branch's real enforcement picture:
impedance / differential / HV are ENFORCED (solved or spaced before
and during routing), length groups are POST-CHECK (measured and
reported after routing, never enforced), power and quiet are
CONDITIONAL (power sizes only when a schematic netlist AND a
defined stackup are both present; quiet keepouts apply only where
an entry's Materialise is on).
"""
import copy, html, os

try:
    from . import markings
    from . import naming
    from . import hvdomains
except ImportError:
    import markings
    import naming
    import hvdomains

SECTIONS = markings.SECTIONS
SECTION_TITLES = {'impedance': 'Impedance', 'differential': 'Differential',
                  'power': 'Power', 'length_groups': 'Length groups',
                  'hv_domains': 'HV domains', 'quiet': 'Quiet nets'}
SECTION_TAGS = {'impedance': 'ENFORCED', 'differential': 'ENFORCED',
                'hv_domains': 'ENFORCED', 'length_groups': 'POST-CHECK',
                'power': 'CONDITIONAL', 'quiet': 'CONDITIONAL'}
HONESTY_LINE = ('Impedance, differential and HV spacing are applied before routing. '
                'Length groups are checked after routing and reported, never enforced. '
                'Power sizing needs a schematic netlist and a defined stackup; '
                'quiet-net keepouts apply only where Materialise is on.')
SAME_DOMAIN_WARNING = ("Nets in the SAME domain get no enforced spacing between them. "
                       "If two nets must keep apart (mains L and N, a primary/secondary "
                       "pair), put them in different domains \u2014 the spacing is then set "
                       "by the difference between the domains' peak voltages.")
VERDICT_ORDER = ('PASS', 'FAIL', 'WARN', 'LIB GAP', 'CONDITIONAL')


# ------------------------------------------------------------ sidecar state
def sidecar_path(board_path):
    return markings.markings_path_for(board_path)


def load_sidecar_state(board_path):
    # -> {'state': 'valid'|'none'|'invalid', 'path':..., 'data': dict|None,
    #     'error': str|None}.  Mirrors the pipeline: load_markings
    # returns None when absent and raises MarkingsError when present
    # but malformed -- and on MarkingsError the pipeline logs and
    # routes UNMARKED, which is what the Screen 1 banner states.
    path = sidecar_path(board_path)
    try:
        mk = markings.load_markings(board_path)
    except markings.MarkingsError as e:
        return {'state': 'invalid', 'path': path, 'data': None, 'error': str(e)}
    if mk is None:
        return {'state': 'none', 'path': path, 'data': None, 'error': None}
    return {'state': 'valid', 'path': path, 'data': mk.data, 'error': None}


def section_counts(data):
    data = data or {}
    return {s: len(data.get(s) or []) for s in SECTIONS}


def evaluate_conditions(board_path, data):
    # Live condition status for the CONDITIONAL tiers, evaluated
    # against the actual project folder the way the pipeline looks:
    # _eng_power_netlist accepts <stem>.net beside the board, else
    # exports from the sibling <stem>.kicad_sch; the stackup check
    # reads the board file's own (setup (stackup ...)) block, which
    # is exactly what the worker's cmd_stackup parses.
    netlist = bool(board_path) and os.path.exists(
        naming.project_sidecar_path(board_path, '.net'))
    schematic = bool(board_path) and os.path.exists(
        naming.project_sidecar_path(board_path, '.kicad_sch'))
    stackup = False
    if board_path and os.path.exists(board_path):
        try:
            with open(board_path, 'r', encoding='utf-8', errors='replace') as fh:
                stackup = '(stackup' in fh.read()
        except OSError:
            stackup = False
    quiet = (data or {}).get('quiet') or []
    mat = sum(1 for e in quiet if e.get('materialise'))
    return {'netlist': netlist, 'schematic': schematic, 'stackup': stackup,
            'power_ready': bool(netlist or schematic) and stackup,
            'quiet_materialise': (mat, len(quiet))}


def chip_text(section, count, conditions):
    # One Screen 1 chip line: '<Title> x<count> -- <TAG>' plus the
    # live condition status for the CONDITIONAL tiers.
    base = '%s \u00d7%d \u2014 %s' % (SECTION_TITLES[section], count, SECTION_TAGS[section])
    if section == 'power':
        if conditions.get('power_ready'):
            base += ' \u00b7 sizes (netlist \u2713 \u00b7 stackup \u2713)'
        elif not (conditions.get('netlist') or conditions.get('schematic')):
            base += ' \u00b7 records only (no netlist beside the board)'
        else:
            base += ' \u00b7 records only (no stackup defined)'
    elif section == 'quiet':
        m, t = conditions.get('quiet_materialise', (0, 0))
        base += ' \u00b7 materialise %d/%d' % (m, t)
    return base


# ------------------------------------------------------------ units
def mm_to_ps(mm, er_eff):
    # Propagation delay: c = 0.299792458 mm/ps in vacuum.
    return mm * (float(er_eff) ** 0.5) / 0.299792458


def ps_to_mm(ps, er_eff):
    return ps * 0.299792458 / (float(er_eff) ** 0.5)


# ------------------------------------------------------------ editor model
class PendingEdits:
    """Un-applied form-edit tracking for the markings editor
    (#205).  The dialog snapshots the active form's dict when the
    form is shown; any later difference is a pending edit that
    Save / Close / selection changes must account for instead of
    silently dropping.  wx-free so the state machine is
    unit-testable."""

    def __init__(self):
        self._snapshot = None

    def shown(self, form_dict):
        self._snapshot = copy.deepcopy(form_dict)

    def cleared(self):
        self._snapshot = None

    def pending(self, current_dict):
        return self._snapshot is not None and current_dict != self._snapshot


class MarkingsEditorModel:
    """Working copy of one sidecar against one board index.

    board_index: {'nets': [names], 'footprints': [{'ref', 'value',
    'pads': {pad: net}, 'properties': {name: text}}], 'layers': [...]}.
    The dialog builds it via board_index.load_index(); tests inject a
    plain dict.  `data` is held in markings.validate() normalised
    shape (load_markings already normalises), so a load -> edit ->
    save round trip diffs only the intended fields.
    """

    def __init__(self, data, board_index):
        self._original = copy.deepcopy(data) if data else {}
        self.data = copy.deepcopy(self._original)
        self.board_index = board_index or {'nets': [], 'footprints': [], 'layers': []}

    # -- state -----------------------------------------------------
    @property
    def dirty(self):
        return self.data != self._original

    def board_nets(self):
        return list(self.board_index.get('nets') or [])

    def footprint_refs(self):
        return [f['ref'] for f in self.board_index.get('footprints') or []]

    def nets_for_ref(self, ref):
        for f in self.board_index.get('footprints') or []:
            if f['ref'] == ref:
                return sorted({n for n in (f.get('pads') or {}).values() if n})
        return []

    def diff_pair_names(self):
        return [e.get('name') for e in self.data.get('differential') or []]

    def diff_pair_nets(self, name):
        for e in self.data.get('differential') or []:
            if e.get('name') == name:
                return list(e.get('nets') or [])
        return []

    # -- entries ---------------------------------------------------
    def entries(self, section):
        return list(self.data.get(section) or [])

    def add_entry(self, section, entry):
        lst = self.data.setdefault(section, [])
        lst.append(copy.deepcopy(entry))
        return len(lst) - 1

    def update_entry(self, section, index, entry):
        self.data[section][index] = copy.deepcopy(entry)

    def remove_entry(self, section, index):
        del self.data[section][index]
        if not self.data[section]:
            del self.data[section]

    def entry_label(self, section, entry):
        if section in ('impedance', 'power'):
            return entry.get('net') or '(no net)'
        if section == 'quiet':
            return entry.get('net') or entry.get('ref') or '(unset)'
        return entry.get('name') or '(unnamed)'

    def entry_nets(self, section, entry):
        # Every board net an entry refers to (pair references and
        # quiet refs resolved), for marked-net counting and the
        # unmatched check.
        if section in ('impedance', 'power'):
            return [entry['net']] if entry.get('net') else []
        if section == 'differential':
            return list(entry.get('nets') or [])
        if section == 'hv_domains':
            return list(entry.get('nets') or [])
        if section == 'length_groups':
            if entry.get('nets'):
                return list(entry['nets'])
            if entry.get('pair'):
                return self.diff_pair_nets(entry['pair'])
            return []
        if section == 'quiet':
            if entry.get('net'):
                return [entry['net']]
            if entry.get('ref'):
                return self.nets_for_ref(entry['ref'])
            return []
        return []

    def marked_nets(self):
        # {net: [(section, index), ...]} -- a net with several
        # markings appears once, carrying all of them.
        out = {}
        for s in SECTIONS:
            for i, e in enumerate(self.data.get(s) or []):
                for n in self.entry_nets(s, e):
                    out.setdefault(n, []).append((s, i))
        return out

    def issues(self):
        # Entries referring to nets the board does not have -- the
        # editor's amber Unmatched bucket.  (markings.validate
        # cannot see the board, so this is an editor-level check;
        # the pipeline treats such entries as recorded-but-unmatched
        # too -- see the impedance report's first_nets_extra.)
        have = set(self.board_nets())
        out = []
        for s in SECTIONS:
            for i, e in enumerate(self.data.get(s) or []):
                missing = [n for n in self.entry_nets(s, e) if n not in have]
                if missing:
                    out.append({'section': s, 'index': i,
                                'label': self.entry_label(s, e),
                                'missing': missing})
        return out

    def rebind(self, section, index, old_net, new_net):
        # Rewrite every reference to old_net inside one entry.
        e = self.data[section][index]
        if section in ('impedance', 'power', 'quiet') and e.get('net') == old_net:
            e['net'] = new_net
        if 'nets' in e and isinstance(e['nets'], list):
            e['nets'] = [new_net if n == old_net else n for n in e['nets']]
        return e

    # -- board-level ------------------------------------------------
    def power_corners(self):
        return dict(self.data.get('power_corners') or {})

    def set_power_corners(self, corners):
        corners = {k: v for k, v in (corners or {}).items() if v is not None}
        if corners:
            self.data['power_corners'] = corners
        elif 'power_corners' in self.data:
            del self.data['power_corners']

    def rule_footprints(self):
        # #42 read-only display: footprints carrying AIRule*
        # properties, with the values in use.
        out = []
        for f in self.board_index.get('footprints') or []:
            props = {k: v for k, v in (f.get('properties') or {}).items()
                     if k.startswith('AIRule') and v}
            if props:
                out.append({'ref': f['ref'], 'properties': props})
        return out

    def connected_parts(self, net):
        # 'Import connected parts on <net>': one row per footprint
        # with a pad on the net.
        out = []
        for f in self.board_index.get('footprints') or []:
            pins = sorted(p for p, n in (f.get('pads') or {}).items() if n == net)
            if pins:
                out.append({'ref': f['ref'], 'value': f.get('value') or '',
                            'pins': pins})
        return out

    # -- validation / save ------------------------------------------
    def validate_working(self):
        # None when the working copy is a sidecar the pipeline
        # accepts, else the MarkingsError text to surface.
        try:
            markings.validate(copy.deepcopy(self.data))
        except markings.MarkingsError as e:
            return str(e)
        return None

    def save(self, path):
        markings.save_markings(path, self.data)
        self._original = copy.deepcopy(
            markings.validate(copy.deepcopy(self.data)))
        self.data = copy.deepcopy(self._original)
        return path


# ------------------------------------------------------------ form <-> entry
# The wx forms edit plain dicts; these conversions are the only
# path from form values to sidecar entries, and they produce the
# exact shapes markings.validate() checks (numbers as numbers,
# absent optionals omitted).  Bad numbers raise ValueError with a
# field-named message for the form to show.
def _req_float(form, key, label):
    v = form.get(key)
    if v is None or v == '':
        raise ValueError('%s is required' % label)
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % label)


def _opt_float(form, key, label):
    v = form.get(key)
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % label)


def _csv_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(',') if x.strip()]


def _freq_mhz_to_hz(mhz):
    hz = float(mhz) * 1e6
    return int(hz) if hz == int(hz) else hz


# -- impedance ------------------------------------------------------
def impedance_form(entry):
    pi = entry.get('pi') or {}
    return {'net': entry.get('net') or '', 'z0': entry.get('z0_ohm'),
            'role': entry.get('role') or '', 'model': entry.get('model') or '',
            'gap_mm': entry.get('gap_mm'), 'dk_basis': entry.get('dk_basis') or '',
            'mask': entry.get('mask') or '',
            'freq_mhz': (entry['freq_hz'] / 1e6) if entry.get('freq_hz') else None,
            'fence': bool(entry.get('fence')), 'smooth': bool(entry.get('smooth')),
            'pi_radio': list(pi.get('radio') or []),
            'pi_antenna': list(pi.get('antenna') or []),
            'pi_radio_ref': pi.get('radio_ref') or '',
            'dk': entry.get('dk'),
            'layers': list(entry.get('layers') or []),
            'loss_budget_db': entry.get('loss_budget_db'),
            'tol_pct': entry.get('tol_pct')}


def impedance_entry(form):
    net = (form.get('net') or '').strip()
    if not net:
        raise ValueError('net is required')
    e = {'net': net, 'z0_ohm': _req_float(form, 'z0', 'Target Z0')}
    if form.get('role'):
        e['role'] = form['role']
    if form.get('model'):
        e['model'] = form['model']
        if form['model'] == 'coplanar':
            gap = _opt_float(form, 'gap_mm', 'Coplanar gap')
            if gap is None or gap <= 0:
                # The pipeline refuses a coplanar marking without
                # gap_mm (it cannot solve the geometry); refuse it
                # at entry construction instead of at run time
                # (#201).
                raise ValueError('coplanar topology needs a positive gap (mm)')
            e['gap_mm'] = gap
    if form.get('dk_basis'):
        e['dk_basis'] = form['dk_basis']
    if form.get('mask'):
        e['mask'] = form['mask']
    dk = _opt_float(form, 'dk', 'Dk override')
    if dk is not None:
        e['dk'] = dk
    layers = _csv_list(form.get('layers'))
    if layers:
        e['layers'] = layers
    loss = _opt_float(form, 'loss_budget_db', 'Loss budget')
    if loss is not None:
        e['loss_budget_db'] = loss
    if form.get('tol_pct') is not None:
        # Declared in the markings.py schema header but consumed
        # nowhere yet: carried verbatim so an edit never deletes
        # it (#201).  Deliberately not surfaced in the form.
        e['tol_pct'] = form['tol_pct']
    mhz = _opt_float(form, 'freq_mhz', 'Frequency')
    if mhz is not None:
        e['freq_hz'] = _freq_mhz_to_hz(mhz)
    if form.get('fence'):
        e['fence'] = True
    if form.get('smooth'):
        e['smooth'] = True
    pi = {}
    if _csv_list(form.get('pi_radio')):
        pi['radio'] = _csv_list(form.get('pi_radio'))
    if _csv_list(form.get('pi_antenna')):
        pi['antenna'] = _csv_list(form.get('pi_antenna'))
    if (form.get('pi_radio_ref') or '').strip():
        pi['radio_ref'] = form['pi_radio_ref'].strip()
    if pi:
        e['pi'] = pi
    return e


# -- differential -----------------------------------------------------
def differential_form(entry):
    nets = entry.get('nets') or []
    return {'name': entry.get('name') or '',
            'net_a': nets[0] if len(nets) > 0 else '',
            'net_b': nets[1] if len(nets) > 1 else '',
            'zdiff': entry.get('zdiff_ohm'),
            'freq_mhz': (entry['freq_hz'] / 1e6) if entry.get('freq_hz') else None,
            'gap_mm': entry.get('gap_mm'), 'width_mm': entry.get('width_mm'),
            'dk': entry.get('dk'), 'layers': list(entry.get('layers') or [])}


def differential_entry(form):
    name = (form.get('name') or '').strip()
    a = (form.get('net_a') or '').strip()
    b = (form.get('net_b') or '').strip()
    if not name:
        raise ValueError('pair name is required')
    if not a or not b:
        raise ValueError('both pair nets are required')
    if a == b:
        raise ValueError('pair nets must be two different nets')
    e = {'name': name, 'nets': [a, b], 'zdiff_ohm': _req_float(form, 'zdiff', 'Target Zdiff')}
    mhz = _opt_float(form, 'freq_mhz', 'Frequency')
    if mhz is not None:
        e['freq_hz'] = _freq_mhz_to_hz(mhz)
    for key, label in (('gap_mm', 'Pair gap'), ('width_mm', 'Conductor width'), ('dk', 'Dk override')):
        v = _opt_float(form, key, label)
        if v is not None:
            e[key] = v
    if _csv_list(form.get('layers')):
        e['layers'] = _csv_list(form.get('layers'))
    return e


# -- power -------------------------------------------------------------
def _load_form(ref, spec):
    # Sidecar loads are an OBJECT keyed by ref; the form edits row
    # dicts, so the ref rides the row and the spec stays separate.
    kb = spec.get('kb') or {}
    return {'ref': ref, 'kind': spec.get('kind') or '',
            'peak_a': spec.get('peak_a'), 'sustained_a': spec.get('sustained_a'),
            'duty': spec.get('duty'), 'mpn': kb.get('mpn') or '',
            'datasheet_rev': kb.get('datasheet_rev') or '',
            'modes': ', '.join(spec.get('modes') or [])}


def _load_entry(row):
    # -> (ref, spec): the sidecar keys loads/regulators/conduits/
    # protective by ref, so the ref is NOT part of the spec dict.
    ref = (row.get('ref') or '').strip()
    if not ref:
        raise ValueError('load ref is required')
    ld = {}
    if row.get('kind'):
        ld['kind'] = row['kind']
    for key, label in (('peak_a', 'Peak current'), ('sustained_a', 'Sustained current'), ('duty', 'Duty cycle')):
        v = _opt_float(row, key, label)
        if v is not None:
            ld[key] = v
    kb = {}
    if (row.get('mpn') or '').strip():
        kb['mpn'] = row['mpn'].strip()
    if (row.get('datasheet_rev') or '').strip():
        kb['datasheet_rev'] = row['datasheet_rev'].strip()
    if kb:
        ld['kb'] = kb
    modes = _csv_list(row.get('modes'))
    if modes:
        ld['modes'] = modes
    return ref, ld


def load_is_unknown(load_spec):
    # The wireframe's UNKNOWN state: no measured current and no
    # datasheet (KB) ref.  markings.validate rejects such a load,
    # so the editor flags it and saving stays blocked until it is
    # resolved or the row is removed.
    return 'peak_a' not in load_spec and 'kb' not in load_spec


def power_form(entry):
    v = entry.get('voltage') or {}
    root = entry.get('root') or {}
    return {'net': entry.get('net') or '', 'has_voltage': bool(entry.get('voltage')),
            'nominal_v': v.get('nominal_v'), 'min_v': v.get('min_v'), 'max_v': v.get('max_v'),
            'root_ref': root.get('ref') or '', 'root_pin': root.get('pin') or '',
            'drop_budget_v': entry.get('drop_budget_v'),
            'loads': [_load_form(r, sp) for r, sp in (entry.get('loads') or {}).items()],
            'regulators': [dict(sp, ref=r) for r, sp in (entry.get('regulators') or {}).items()],
            'conduits': [dict(sp, ref=r) for r, sp in (entry.get('conduits') or {}).items()],
            'protective': [dict(sp, ref=r) for r, sp in (entry.get('protective') or {}).items()]}


def power_entry(form):
    net = (form.get('net') or '').strip()
    if not net:
        raise ValueError('net is required')
    e = {'net': net}
    if form.get('has_voltage'):
        v = {'nominal_v': _req_float(form, 'nominal_v', 'Nominal voltage')}
        for key, label in (('min_v', 'Min voltage'), ('max_v', 'Max voltage')):
            x = _opt_float(form, key, label)
            if x is not None:
                v[key] = x
        e['voltage'] = v
    if (form.get('root_ref') or '').strip():
        root = {'ref': form['root_ref'].strip()}
        if (form.get('root_pin') or '').strip():
            root['pin'] = form['root_pin'].strip()  # carried, read nowhere (powerplan reads ref only)
        e['root'] = root
    drop = _opt_float(form, 'drop_budget_v', 'Drop budget')
    if drop is not None:
        e['drop_budget_v'] = drop  # rail-level; power_corners is the fallback (powerplan #83)
    loads = {}
    for r in form.get('loads') or []:
        ref, spec = _load_entry(r)
        loads[ref] = spec
    if loads:
        e['loads'] = loads
    regs = {}
    for r in form.get('regulators') or []:
        ref = (r.get('ref') or '').strip()
        if not ref:
            raise ValueError('regulator ref is required')
        rg = {}
        if r.get('type'):
            rg['type'] = r['type']
        for key, label in (('eta_min', 'Min efficiency'), ('iq_a', 'Quiescent current')):
            x = _opt_float(r, key, label)
            if x is not None:
                rg[key] = x
        regs[ref] = rg
    if regs:
        e['regulators'] = regs
    conduits = {}
    for r in form.get('conduits') or []:
        ref = (r.get('ref') or '').strip()
        if not ref:
            raise ValueError('conduit ref (the component it models) is required')
        cd = {}
        for key in ('from', 'to', 'kind'):
            if (r.get(key) or '').strip():
                cd[key] = r[key].strip()
        for key, label in (('length_mm', 'Length'), ('width_mm', 'Width'), ('r_mohm', 'Resistance'), ('rating_a', 'Rating')):
            x = _opt_float(r, key, label)
            if x is not None:
                cd[key] = x
        conduits[ref] = cd
    if conduits:
        e['conduits'] = conduits
    prots = {}
    for r in form.get('protective') or []:
        ref = (r.get('ref') or '').strip()
        if not ref:
            raise ValueError('protective device ref is required')
        pr = {'rating_a': _req_float(r, 'rating_a', 'Protective rating')}
        if r.get('kind'):
            pr['kind'] = r['kind']
        if (r.get('note') or '').strip():
            pr['note'] = r['note'].strip()
        prots[ref] = pr
    if prots:
        e['protective'] = prots
    return e


# -- length groups -------------------------------------------------------
def length_form(entry):
    mode = 'pair' if (entry.get('pair') and not entry.get('nets')) else 'nets'
    if entry.get('tolerance_mm') is not None:
        tol, unit = entry['tolerance_mm'], 'mm'
    else:
        tol, unit = entry.get('tolerance_ps'), 'ps'
    return {'name': entry.get('name') or '', 'members_mode': mode,
            'nets': list(entry.get('nets') or []), 'pair': entry.get('pair') or '',
            'target_mode': entry.get('target_mode') or 'longest',
            'target_value': entry.get('target_value'),
            'tolerance': tol, 'tolerance_unit': unit,
            'meander_nets': list(entry.get('meander_nets') or []),
            'pi_nets': list(entry.get('pi_nets') or [])}


def length_entry(form):
    name = (form.get('name') or '').strip()
    if not name:
        raise ValueError('group name is required')
    e = {'name': name}
    if form.get('members_mode') == 'pair':
        pair = (form.get('pair') or '').strip()
        if not pair:
            raise ValueError('choose the differential pair that defines the members')
        e['pair'] = pair
    else:
        nets = _csv_list(form.get('nets'))
        if len(set(nets)) < 2:
            raise ValueError('a length group needs at least two member nets')
        e['nets'] = nets
    mode = form.get('target_mode') or 'longest'
    e['target_mode'] = mode
    if mode in ('length', 'delay'):
        e['target_value'] = _req_float(form, 'target_value', 'Target value')
    tol = _req_float(form, 'tolerance', 'Tolerance')
    # The tolerance's stored unit follows the target mode (validator
    # #152: length mode requires tolerance_mm, delay tolerance_ps;
    # longest accepts either and the form's unit toggle decides).
    if mode == 'delay' or (mode == 'longest' and form.get('tolerance_unit') == 'ps'):
        e['tolerance_ps'] = tol
    else:
        e['tolerance_mm'] = tol
    if _csv_list(form.get('meander_nets')):
        e['meander_nets'] = _csv_list(form.get('meander_nets'))
    if _csv_list(form.get('pi_nets')):
        e['pi_nets'] = _csv_list(form.get('pi_nets'))
    return e


# -- HV domains ------------------------------------------------------------
def hv_form(entry):
    return {'name': entry.get('name') or '', 'nets': list(entry.get('nets') or []),
            'peak_v': entry.get('peak_v'), 'ac': bool(entry.get('ac')),
            'coating': entry.get('coating') or '',
            'pollution_degree': entry.get('pollution_degree'),
            'altitude_m': entry.get('altitude_m'),
            'materialise': bool(entry.get('materialise'))}


def hv_entry(form):
    name = (form.get('name') or '').strip()
    nets = _csv_list(form.get('nets'))
    if not name:
        raise ValueError('domain name is required')
    if not nets:
        raise ValueError('a domain needs at least one member net')
    e = {'name': name, 'nets': nets, 'peak_v': _req_float(form, 'peak_v', 'Peak voltage'),
         'ac': bool(form.get('ac')), 'materialise': bool(form.get('materialise'))}
    if form.get('coating'):
        e['coating'] = form['coating']
    pd = form.get('pollution_degree')
    if pd not in (None, ''):
        try:
            e['pollution_degree'] = int(pd)
        except (TypeError, ValueError):
            raise ValueError('Pollution degree must be 1, 2 or 3')
    alt = _opt_float(form, 'altitude_m', 'Altitude')
    if alt is not None:
        e['altitude_m'] = alt
    return e


def hv_pairwise_rows(model, extra=None):
    # Screen 3's pairwise preview: declared domains plus the
    # implicit rest-of-board domain at 0 V the check paths add
    # (tool_hv_domains #146).  |delta| drives the required spacing
    # (hvdomains, IPC-2221B Table 6-1, each domain's declared
    # coating/altitude on external layers -- the checker itself
    # evaluates every layer; the preview shows the governing
    # external value); the table shows the pairs the checker will
    # test.  `extra` is the HV form's live (un-applied) domain
    # (#206): it replaces the saved entry of the same name, or is
    # appended when the form is a new domain.
    doms = []
    for e in model.entries('hv_domains'):
        doms.append({'name': e.get('name'), 'peak_v': e.get('peak_v') or 0.0,
                     'coating': e.get('coating') or 'uncoated',
                     'altitude_m': e.get('altitude_m') or 0,
                     'nets': len(e.get('nets') or []), 'implicit': False})
    if extra is not None:
        ex = {'name': extra.get('name'), 'peak_v': extra.get('peak_v') or 0.0,
              'coating': extra.get('coating') or 'uncoated',
              'altitude_m': extra.get('altitude_m') or 0,
              'nets': len(extra.get('nets') or []), 'implicit': False}
        for i, d in enumerate(doms):
            if d['name'] == ex['name']:
                doms[i] = ex
                break
        else:
            doms.append(ex)
    doms.append({'name': 'Rest of board / unmarked nets', 'peak_v': 0.0,
                 'coating': 'uncoated', 'altitude_m': 0,
                 'nets': None, 'implicit': True})
    rows = []
    for i in range(len(doms)):
        for j in range(i + 1, len(doms)):
            dv = abs(doms[i]['peak_v'] - doms[j]['peak_v'])
            cols = {hvdomains.column_for(True, doms[i]['coating'], doms[i]['altitude_m']),
                    hvdomains.column_for(True, doms[j]['coating'], doms[j]['altitude_m'])}
            req = max(hvdomains.required_spacing_mm(dv, c) for c in cols)
            rows.append({'a': doms[i]['name'], 'b': doms[j]['name'],
                         'delta_v': dv, 'required_mm': req})
    return rows


# -- quiet -------------------------------------------------------------------
def quiet_form(entry):
    mode = 'ref' if (entry.get('ref') and not entry.get('net')) else 'net'
    return {'mode': mode, 'net': entry.get('net') or '', 'ref': entry.get('ref') or '',
            'profile': entry.get('profile') or 'crystal',
            'isolation_mm': entry.get('isolation_mm'),
            'guard_net': entry.get('guard_net') or '',
            'allow_nets': list(entry.get('allow_nets') or []),
            'ground_nets': list(entry.get('ground_nets') or []),
            'materialise': bool(entry.get('materialise'))}


def quiet_entry(form):
    e = {}
    if form.get('mode') == 'ref':
        ref = (form.get('ref') or '').strip()
        if not ref:
            raise ValueError('component ref is required')
        e['ref'] = ref
    else:
        net = (form.get('net') or '').strip()
        if not net:
            raise ValueError('net is required')
        e['net'] = net
    e['profile'] = form.get('profile') or 'crystal'
    e['isolation_mm'] = _req_float(form, 'isolation_mm', 'Isolation')
    if (form.get('guard_net') or '').strip():
        e['guard_net'] = form['guard_net'].strip()
    if _csv_list(form.get('allow_nets')):
        e['allow_nets'] = _csv_list(form.get('allow_nets'))
    if _csv_list(form.get('ground_nets')):
        e['ground_nets'] = _csv_list(form.get('ground_nets'))
    e['materialise'] = bool(form.get('materialise'))
    return e


FORM_FOR = {'impedance': impedance_form, 'differential': differential_form,
            'power': power_form, 'length_groups': length_form,
            'hv_domains': hv_form, 'quiet': quiet_form}
ENTRY_FOR = {'impedance': impedance_entry, 'differential': differential_entry,
             'power': power_entry, 'length_groups': length_entry,
             'hv_domains': hv_entry, 'quiet': quiet_entry}


# ------------------------------------------------------------ verdict summary
# Screen 4 renders the pipeline's own engineered report structures
# (Pipeline.engineered_report + the #42 placement audit) -- no
# parallel format is invented.  Each marking becomes one row with a
# verdict from the fixed set PASS / FAIL / WARN / LIB GAP /
# CONDITIONAL, derived by these rules:
#   impedance:  FAIL when the rf check summary has fails, WARN when
#               it has warnings or the applied width is unverified,
#               else PASS.
#   differential: verified True PASS, False FAIL, unknown WARN.
#   length group: any member failed (or a group error) FAIL, else PASS.
#   HV domain:  FAIL when a pair involving it has violations (or the
#               HV check errored), WARN when not_verified, else PASS.
#   power:      the CONDITIONAL tier -- CONDITIONAL whether it sized
#               (conditions met) or only recorded (conditions unmet);
#               FAIL only when the tree build itself failed.
#   quiet:      FAIL when intrusions were found, else CONDITIONAL
#               (checked; enforcement depends on Materialise).
#   #42 audit:  LIB GAP when footprints lack a qualifying neighbour,
#               FAIL when rule checks failed, else PASS.
# The amber DONE (n GATE FAIL) lamp count is the FAIL row count.
def _fmt(x, nd=2):
    return ('%.' + str(nd) + 'f') % x if isinstance(x, (int, float)) else str(x)


def summarize_report(rep, audit=None):
    rows = []
    if rep and not rep.get('error'):
        for net, s in sorted((rep.get('nets') or {}).items()):
            cs = s.get('check_summary') or {}
            fails = [c for c in s.get('checks') or [] if c.get('severity') == 'fail']
            if cs.get('fail'):
                verdict = 'FAIL'
            elif cs.get('warn') or s.get('width_verified') is False:
                verdict = 'WARN'
            else:
                verdict = 'PASS'
            parts = []
            if s.get('computed_width_mm') is not None:
                parts.append('solved %s mm on %s' % (_fmt(s['computed_width_mm']), s.get('primary_layer')))
            if s.get('applied_width_mm') is not None:
                parts.append('routed %s mm' % _fmt(s['applied_width_mm']))
            if s.get('band'):
                parts.append(str(s['band']))
            for c in fails[:2]:
                parts.append(c.get('detail') or c.get('rule') or 'check failed')
            for w in (s.get('warnings') or [])[:1]:
                parts.append(str(w))
            at = None
            for c in fails:
                if c.get('at'):
                    at = (c['at'][0], c['at'][1])
                    break
            rows.append({'section': 'impedance', 'marking': 'Impedance \u2014 %s' % net,
                         'verdict': verdict, 'detail': ' \u00b7 '.join(parts), 'at': at})
        for d in rep.get('differential') or []:
            v = d.get('verified')
            verdict = 'PASS' if v is True else ('FAIL' if v is False else 'WARN')
            parts = []
            if d.get('applied_width_mm') is not None:
                parts.append('solved %s/%s mm on %s' % (_fmt(d['applied_width_mm']),
                             _fmt(d.get('applied_gap_mm')), d.get('primary_layer')))
            legs = d.get('leg_lengths_mm') or {}
            if legs:
                parts.append('legs %s mm' % '/'.join(_fmt(x) for x in legs.values()))
            if d.get('skew_mm') is not None:
                parts.append('skew %s mm' % _fmt(d['skew_mm'], 3))
            if d.get('note'):
                parts.append(str(d['note']))
            rows.append({'section': 'differential',
                         'marking': 'Differential \u2014 %s' % d.get('name'),
                         'verdict': verdict, 'detail': ' \u00b7 '.join(parts), 'at': None})
        lg = rep.get('length_groups') or {}
        for g in lg.get('groups') or []:
            members = g.get('members') or {}
            # lengthmatch grades members with within_tolerance; the
            # pipeline's meander pass stores the post-meander grade
            # under 'after' and flags 'failed' only when the
            # after-state still misses -- the top-level values stay
            # at their PRE-meander readings, so grade and delta
            # must be read from 'after' when it exists (#217).
            failed = [n for n, m in members.items()
                      if m.get('failed') or (m.get('after') or m).get('within_tolerance') is False]
            verdict = 'FAIL' if (failed or g.get('error')) else 'PASS'
            parts = []
            if g.get('error'):
                parts.append(str(g['error']))
            for n, m in sorted(members.items()):
                bad = n in failed
                cur = m.get('after') or m
                suffix = ' (outside tolerance)' if bad else ''
                meander = m.get('meander') or {}
                if meander.get('adopted') and meander.get('added_mm') is not None:
                    suffix += ' (tuned +%s mm)' % _fmt(meander['added_mm'])
                if cur.get('delta_mm') is not None:
                    parts.append('%s \u0394%s mm%s' % (n, _fmt(cur['delta_mm']), suffix))
                elif cur.get('delta_ps') is not None:
                    parts.append('%s \u0394%s ps%s' % (n, _fmt(cur['delta_ps'], 1), suffix))
            rows.append({'section': 'length_groups',
                         'marking': 'Length group \u2014 %s' % g.get('name'),
                         'verdict': verdict, 'detail': ' \u00b7 '.join(parts), 'at': None})
        hv = rep.get('hv_domains') or {}
        if hv:
            pairs = hv.get('pairs') or []
            status = hv.get('status')
            for a in hv.get('assumptions') or []:
                if a.get('implicit'):
                    continue
                name = a.get('domain')
                mine = [p for p in pairs if p.get('a') == name or p.get('b') == name]
                viols = [v for p in mine for v in (p.get('violations') or [])]
                if status == 'error' or viols:
                    verdict = 'FAIL'
                elif status == 'not_verified':
                    verdict = 'WARN'
                else:
                    verdict = 'PASS'
                parts = ['peak %s V' % _fmt(a.get('peak_v'), 0)]
                if viols:
                    v0 = viols[0]
                    parts.append('%s on %s: needs %s mm, actual %s mm' % (
                        v0.get('kind'), v0.get('layer'), _fmt(v0.get('required_mm')), _fmt(v0.get('actual_mm'))))
                elif mine:
                    parts.append('worst pair \u0394 %s V clear' % _fmt(max(p.get('delta_v') or 0 for p in mine), 0))
                at = None
                for v in viols:
                    if v.get('at'):
                        at = (v['at'][0], v['at'][1])
                        break
                rows.append({'section': 'hv_domains', 'marking': 'HV domain \u2014 %s' % name,
                             'verdict': verdict, 'detail': ' \u00b7 '.join(parts), 'at': at})
        pw = rep.get('power')
        if pw:
            st = pw.get('status')
            verdict = 'FAIL' if st == 'tree_failed' else 'CONDITIONAL'
            rails = pw.get('rails') or {}
            if st == 'ok':
                detail = 'sized from %s' % (pw.get('netlist') or 'netlist')
                for rn, rail in sorted(rails.items()):
                    seg = ((rail.get('design') or {}).get('peak_a') or {}).get('design_a')
                    drops = rail.get('loop_drops') or []
                    if seg is not None:
                        detail += ' \u00b7 %s %s A peak' % (rn, _fmt(seg))
                    if drops:
                        detail += ', drop %s of %s V budget' % (_fmt(drops[0].get('drop_v'), 3), _fmt(drops[0].get('budget_v')))
                if pw.get('widen'):
                    detail += ' \u00b7 %d nets widened' % len(pw['widen'])
                if pw.get('kb_resolutions'):
                    detail += ' \u00b7 %d KB refs resolved' % len(pw['kb_resolutions'])
            elif st == 'needs_stackup':
                detail = 'recorded only \u2014 no stackup defined, so no widths were computed'
            elif st == 'netlist_unavailable':
                detail = 'recorded only \u2014 no schematic netlist, so currents are unknown'
            else:
                detail = str(pw.get('note') or pw.get('error') or st)
            rail_names = ', '.join(sorted((pw.get('rails') or {}).keys()))
            rows.append({'section': 'power', 'marking': 'Power \u2014 %s' % (rail_names or pw.get('net') or 'rail'),
                         'verdict': verdict, 'detail': detail, 'at': None})
        qn = rep.get('quiet_nets') or {}
        for r in qn.get('results') or []:
            # quietnets.evaluate result shape: name / profile /
            # isolation_mm / intrusions / pass / materialise, plus
            # the pipeline's enforced_in_domains stamp (#219).
            label = r.get('name') or r.get('net') or r.get('ref') or 'quiet net'
            intr = r.get('intrusions') or []
            verdict = 'FAIL' if (intr or r.get('pass') is False) else 'CONDITIONAL'
            parts = ['%s \u00b7 isolation %s mm' % (r.get('profile') or 'crystal',
                     _fmt(r.get('isolation_mm')))]
            if intr:
                parts.append('%d intrusions inside the isolation' % len(intr))
            else:
                parts.append('no intrusions')
            if r.get('enforced_in_domains') or r.get('materialise'):
                parts.append('keepout materialised')
            else:
                parts.append('report-only (not materialised)')
            at = None
            for it in intr:
                if isinstance(it, dict) and it.get('at'):
                    at = (it['at'][0], it['at'][1])
                    break
            rows.append({'section': 'quiet', 'marking': 'Quiet \u2014 %s' % label,
                         'verdict': verdict, 'detail': ' \u00b7 '.join(parts), 'at': at})
    if audit:
        gaps = audit.get('gaps') or []
        fps = audit.get('footprints') or {}
        failed = sum(1 for f in fps.values()
                     for c in (f.get('checks') or [])
                     if c.get('ok') is False or c.get('kind') == 'keepout_intrusion')
        if gaps:
            rows.append({'section': 'library_rules', 'marking': 'Library rules (#42)',
                         'verdict': 'LIB GAP',
                         'detail': '%s: no qualifying neighbour in the library defaults in use' % ', '.join(gaps),
                         'at': None})
        elif failed:
            rows.append({'section': 'library_rules', 'marking': 'Library rules (#42)',
                         'verdict': 'FAIL', 'detail': '%d rule checks failed' % failed, 'at': None})
        elif fps:
            rows.append({'section': 'library_rules', 'marking': 'Library rules (#42)',
                         'verdict': 'PASS', 'detail': 'all rule-carrying footprints satisfied', 'at': None})
    counts = {v: 0 for v in VERDICT_ORDER}
    for r in rows:
        counts[r['verdict']] = counts.get(r['verdict'], 0) + 1
    return {'rows': rows, 'counts': counts}


def verdict_bar_text(counts):
    return ' \u00b7 '.join('%d %s' % (counts.get(v, 0), v) for v in VERDICT_ORDER)


def report_markdown(rows, counts, title='Engineered nets report'):
    lines = ['# %s' % title, '', verdict_bar_text(counts), '',
             '| Marking | Verdict | Detail |', '|---|---|---|']
    for r in rows:
        d = r['detail']
        if r.get('at'):
            d += ' (%s, %s)' % (_fmt(r['at'][0], 1), _fmt(r['at'][1], 1))
        lines.append('| %s | %s | %s |' % (r['marking'], r['verdict'], d.replace('|', '/')))
    return '\n'.join(lines) + '\n'


def report_html(rows, counts, title='Engineered nets report'):
    trs = []
    for r in rows:
        d = html.escape(r['detail'])
        if r.get('at'):
            d += ' (%s, %s)' % (_fmt(r['at'][0], 1), _fmt(r['at'][1], 1))
        trs.append('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            html.escape(r['marking']), html.escape(r['verdict']), d))
    return ('<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>%s</title></head>'
            '<body><h1>%s</h1><p>%s</p><table border="1" cellspacing="0" cellpadding="4">'
            '<tr><th>Marking</th><th>Verdict</th><th>Detail</th></tr>%s</table></body></html>\n'
            % (html.escape(title), html.escape(title), html.escape(verdict_bar_text(counts)), ''.join(trs)))
