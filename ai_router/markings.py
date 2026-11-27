"""Engineered-net markings: the sidecar store shared by #67-#71.

Markings (impedance targets, power trees, length groups, HV domains,
quiet nets) are user design intent that KiCad's own files cannot hold
faithfully -- the project file is rewritten by KiCad and foreign keys
risk being dropped.  Until the persistence ruling lands, the working
assumption is a sidecar JSON file beside the board:

    <board-stem>.ai-router-markings.json

This module is the ONLY place that knows the storage: a narrow
load/save interface over a versioned dict, so switching storage later
(project-file keys, board fields) touches this file alone.

Schema v1:

    {
      "version": 1,
      "board": "<board file name>",
      "impedance":     [ {net, z0_ohm, freq_hz?, dk?, dk_basis?,
                          mask?, layers?, gap_mm?, tol_pct?,
                          role?, loss_budget_db?, pi?, fence?,
                          smooth?} ],   # Step 2: EMC treatment fields
      "power":         [ ... ],   # #68, carried, not enforced yet
      "length_groups": [ ... ],   # #69 matched groups (schema in _check_length_group)
      "hv_domains":    [ ... ],   # #70 voltage domains (schema in _check_hv_domain)
      "quiet":         [ ... ]    # #71, carried, not enforced yet
    }

Only the impedance section is validated field-by-field in this draft
(it is the one Step 1 enforces); the other sections are checked to be
lists of objects and carried verbatim, unknown keys included, so a
later step never loses data written by an earlier one.
"""

import json
import os

try:
    from . import naming as _naming
except ImportError:
    import naming as _naming

SCHEMA_VERSION = 1
SIDECAR_SUFFIX = '.ai-router-markings.json'
SECTIONS = ('impedance', 'differential', 'power', 'length_groups', 'hv_domains', 'quiet')


class MarkingsError(ValueError):
    """The sidecar exists but is malformed or fails validation."""


def markings_path_for(board_path):
    """Sidecar path for a board file path -- #182 project-stem
    resolution (#204): a generated result board (timestamped
    name) finds the project's sidecar, not a namesake of its own."""
    return _naming.project_sidecar_path(board_path, SIDECAR_SUFFIX)


def _check_impedance_entry(entry, idx):
    where = 'impedance[%d]' % idx
    if not isinstance(entry, dict):
        raise MarkingsError('%s: entry must be an object' % where)
    net = entry.get('net')
    if not isinstance(net, str) or not net:
        raise MarkingsError('%s: "net" must be a non-empty string' % where)
    z0 = entry.get('z0_ohm')
    if not isinstance(z0, (int, float)) or isinstance(z0, bool) or z0 <= 0:
        raise MarkingsError('%s: "z0_ohm" must be a positive number' % where)
    out = dict(entry)
    out['z0_ohm'] = float(z0)
    if entry.get('freq_hz') is not None:
        f = entry['freq_hz']
        if not isinstance(f, (int, float)) or isinstance(f, bool) or f <= 0:
            raise MarkingsError('%s: "freq_hz" must be a positive number' % where)
        out['freq_hz'] = float(f)
    if entry.get('dk') is not None:
        dk = entry['dk']
        if not isinstance(dk, (int, float)) or isinstance(dk, bool) or dk < 1.0:
            raise MarkingsError('%s: "dk" must be a number >= 1' % where)
        out['dk'] = float(dk)
    if entry.get('dk_basis') is not None and entry['dk_basis'] not in (
            'design', 'process', '1mhz', 'stackup'):
        raise MarkingsError('%s: "dk_basis" must be design|process|1mhz|stackup' % where)
    if entry.get('mask') is not None and entry['mask'] not in ('covered', 'open'):
        raise MarkingsError('%s: "mask" must be covered|open' % where)
    if entry.get('model') is not None and entry['model'] not in (
            'microstrip', 'stripline', 'coplanar'):
        raise MarkingsError('%s: "model" must be microstrip|stripline|coplanar' % where)
    if entry.get('gap_mm') is not None:
        g = entry['gap_mm']
        if not _num(g) or g <= 0:
            raise MarkingsError('%s: "gap_mm" must be a positive number' % where)
        out['gap_mm'] = float(g)
    if entry.get('model') == 'coplanar' and out.get('gap_mm') is None:
        raise MarkingsError('%s: model="coplanar" requires a positive "gap_mm"' % where)
    if entry.get('layers') is not None:
        layers = entry['layers']
        if (not isinstance(layers, list)
                or not all(isinstance(x, str) for x in layers)):
            raise MarkingsError('%s: "layers" must be a list of layer names' % where)
    # Step 2 (EMC treatment) optional fields.  `role` scales the loss
    # warnings (WARN-1: GNSS/LNA feeds warn at 0.2 dB) and the 2nd-
    # harmonic stub check (WARN-8, TX feeds); `pi` declares the
    # pi-match footprints the schematic calls for (GATE-7 checks
    # presence on the net; WARN-6 judges displacement against the
    # declared radio end); `fence` / `smooth` switch the generation
    # treatments per net (defaults: fence coplanar nets, smooth all).
    if entry.get('role') is not None and entry['role'] not in (
            'tx', 'rx', 'gnss_lna', 'lna_input', 'generic'):
        raise MarkingsError(
            '%s: "role" must be tx|rx|gnss_lna|lna_input|generic' % where)
    if entry.get('loss_budget_db') is not None:
        lb = entry['loss_budget_db']
        if not isinstance(lb, (int, float)) or isinstance(lb, bool) or lb <= 0:
            raise MarkingsError('%s: "loss_budget_db" must be a positive number' % where)
        out['loss_budget_db'] = float(lb)
    if entry.get('pi') is not None:
        pi = entry['pi']
        if not isinstance(pi, dict):
            raise MarkingsError('%s: "pi" must be an object' % where)
        for key in pi:
            if key not in ('radio', 'antenna', 'radio_ref'):
                raise MarkingsError('%s: "pi" key %r not radio|antenna|radio_ref' % (where, key))
            v = pi[key]
            if key == 'radio_ref':
                if not isinstance(v, str) or not v:
                    raise MarkingsError('%s: "pi.radio_ref" must be a footprint reference' % where)
            elif (not isinstance(v, list) or not v
                    or not all(isinstance(x, str) and x for x in v)):
                raise MarkingsError('%s: "pi.%s" must be a non-empty list of footprint references' % (where, key))
    for sw in ('fence', 'smooth'):
        if entry.get(sw) is not None and not isinstance(entry[sw], bool):
            raise MarkingsError('%s: "%s" must be a boolean' % (where, sw))
    return out


class Markings:
    """A loaded sidecar.  `data` is the full versioned dict."""

    def __init__(self, data, path=None):
        self.data = data
        self.path = path

    @property
    def impedance(self):
        return self.data.get('impedance', [])

    def impedance_nets(self):
        return [e['net'] for e in self.impedance]

    @property
    def power(self):
        return self.data.get('power', [])

    def power_corners(self):
        return self.data.get('power_corners') or {}

    def power_for(self, net):
        for e in self.power:
            if e['net'] == net:
                return e
        return None

    @property
    def quiet(self):
        return self.data.get('quiet', [])

    @property
    def hv_domains(self):
        return self.data.get('hv_domains', [])

    @property
    def length_groups(self):
        return self.data.get('length_groups', [])

    @property
    def differential(self):
        return self.data.get('differential', [])

    def differential_legs(self):
        return [n for p in self.differential for n in p['nets']]

    def impedance_for(self, net):
        for e in self.impedance:
            if e['net'] == net:
                return e
        return None

    def section(self, name):
        return self.data.get(name, [])

    def save(self, path=None):
        save_markings(path or self.path, self.data)


def validate(data):
    """Validate + normalise a parsed sidecar dict.  Raises MarkingsError."""
    if not isinstance(data, dict):
        raise MarkingsError('sidecar root must be a JSON object')
    version = data.get('version', SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise MarkingsError('unsupported markings version: %r' % (version,))
    out = dict(data)
    out['version'] = SCHEMA_VERSION
    for name in SECTIONS:
        entries = data.get(name, [])
        if not isinstance(entries, list):
            raise MarkingsError('"%s" must be a list' % name)
        if name == 'impedance':
            out[name] = [_check_impedance_entry(e, i) for i, e in enumerate(entries)]
        elif name == 'differential':
            out[name] = [_check_differential_entry(e, i) for i, e in enumerate(entries)]
        elif name == 'power':
            out[name] = [_check_power_entry(e, i) for i, e in enumerate(entries)]
        elif name == 'quiet':
            out[name] = [_check_quiet_entry(e, i)
                         for i, e in enumerate(entries)]
        elif name == 'hv_domains':
            out[name] = [_check_hv_domain(e, i)
                         for i, e in enumerate(entries)]
        elif name == 'length_groups':
            out[name] = [_check_length_group(e, i) for i, e in enumerate(entries)]
        else:
            for e in entries:
                if not isinstance(e, dict):
                    raise MarkingsError('"%s" entries must be objects' % name)
            out[name] = entries
    if data.get('power_corners') is not None:
        pc = data['power_corners']
        if not isinstance(pc, dict):
            raise MarkingsError('"power_corners" must be an object')
        for key in ('ambient_c', 'delta_t_c', 'drop_budget_v',
                    'width_increment_mm', 'etch_tol_mm', 'foil_tol_frac',
                    'plating_um'):
            if pc.get(key) is not None:
                v = pc[key]
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                    raise MarkingsError('"power_corners.%s" must be a positive number' % key)
        out['power_corners'] = dict(pc)
    return out


def _check_differential_entry(entry, idx):
    # #67 Step 3: a differential pair marking.  The pair is two nets and
    # a target differential impedance; width and gap are solved from
    # the stackup unless the designer pins one of them.  Length
    # matching is NOT part of this section (it is a separate item).
    where = 'differential[%d]' % idx
    if not isinstance(entry, dict):
        raise MarkingsError('%s: entry must be an object' % where)
    name = entry.get('name')
    if not isinstance(name, str) or not name:
        raise MarkingsError('%s: "name" must be a non-empty string' % where)
    nets = entry.get('nets')
    if (not isinstance(nets, list) or len(nets) != 2
            or not all(isinstance(n, str) and n for n in nets)
            or nets[0] == nets[1]):
        raise MarkingsError('%s: "nets" must be two distinct net names' % where)
    zd = entry.get('zdiff_ohm')
    if not isinstance(zd, (int, float)) or isinstance(zd, bool) or zd <= 0:
        raise MarkingsError('%s: "zdiff_ohm" must be a positive number' % where)
    out = dict(entry)
    out['zdiff_ohm'] = float(zd)
    for key in ('freq_hz', 'gap_mm', 'width_mm'):
        if entry.get(key) is not None:
            v = entry[key]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                raise MarkingsError('%s: "%s" must be a positive number' % (where, key))
            out[key] = float(v)
    if entry.get('dk') is not None:
        dk = entry['dk']
        if not isinstance(dk, (int, float)) or isinstance(dk, bool) or dk < 1.0:
            raise MarkingsError('%s: "dk" must be a number >= 1' % where)
        out['dk'] = float(dk)
    if entry.get('layers') is not None:
        layers = entry['layers']
        if (not isinstance(layers, list)
                or not all(isinstance(x, str) for x in layers)):
            raise MarkingsError('%s: "layers" must be a list of layer names' % where)
    return out


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_quiet_entry(entry, idx):
    p = 'quiet[%d]' % idx
    e = entry
    if not isinstance(e, dict):
        raise MarkingsError('%s: entry must be an object' % p)
    has_net = isinstance(e.get('net'), str) and bool(e.get('net'))
    has_ref = isinstance(e.get('ref'), str) and bool(e.get('ref'))
    if has_net == has_ref:
        raise MarkingsError('%s: declare exactly one of "net" or '
                            '"ref" (a component whose pins are quiet)' % p)
    if e.get('profile') is not None and e['profile'] not in (
            'crystal', 'analog', 'reference'):
        raise MarkingsError('%s: "profile" must be crystal|analog|'
                            'reference' % p)
    if 'isolation_mm' in e and (not _num(e['isolation_mm'])
                                or e['isolation_mm'] <= 0):
        raise MarkingsError('%s: "isolation_mm" must be a number > 0' % p)
    if 'guard_net' in e and not (isinstance(e['guard_net'], str)
                                 and e['guard_net']):
        raise MarkingsError('%s: "guard_net" must be a net name' % p)
    if 'allow_nets' in e and (not isinstance(e['allow_nets'], list)
                              or not all(isinstance(n, str)
                                         for n in e['allow_nets'])):
        raise MarkingsError('%s: "allow_nets" must be a list of net '
                            'names' % p)
    if 'ground_nets' in e and (not isinstance(e['ground_nets'], list)
                               or not all(isinstance(n, str) and n
                                          for n in e['ground_nets'])):
        raise MarkingsError('%s: "ground_nets" must be a list of net '
                            'names' % p)
    if 'materialise' in e and not isinstance(e['materialise'], bool):
        raise MarkingsError('%s: "materialise" must be a boolean' % p)
    return dict(e)


def _check_hv_domain(entry, idx):
    p = 'hv_domains[%d]' % idx
    e = entry
    if not isinstance(e, dict):
        raise MarkingsError('%s: entry must be an object' % p)
    if not isinstance(e.get('name'), str) or not e['name']:
        raise MarkingsError('%s: needs a "name"' % p)
    nets = e.get('nets')
    if (not isinstance(nets, list) or not nets
            or not all(isinstance(n, str) for n in nets)
            or len(set(nets)) != len(nets)):
        raise MarkingsError('%s: "nets" must be a non-empty list of '
                            'distinct net names' % p)
    # #129 (B3): the value is a PEAK working voltage, SIGNED -- a
    # bipolar pair is entered as +15 / -15 (difference 30 V), not
    # as two magnitudes (difference 0, 'no spacing requirement').
    # "peak_v" is the field's name; "voltage_v" stays as a legacy
    # alias and both are normalised into the validated entry.
    _v = e.get('peak_v', e.get('voltage_v'))
    if not _num(_v):
        raise MarkingsError('%s: "peak_v" (peak working voltage vs '
                            'the 0 V reference, signed) is required' % p)
    if 'ac' in e and not isinstance(e['ac'], bool):
        raise MarkingsError('%s: "ac" must be a boolean (the value '
                            'is the AC peak either way)' % p)
    if e.get('coating', 'uncoated') not in ('uncoated', 'polymer', 'conformal'):
        raise MarkingsError('%s: "coating" must be uncoated|polymer|conformal '
                            '(solder mask alone is not a coating)' % p)
    if 'altitude_m' in e and (not _num(e['altitude_m'])
                              or e['altitude_m'] < 0):
        raise MarkingsError('%s: "altitude_m" must be a number >= 0' % p)
    if 'pollution_degree' in e and e['pollution_degree'] not in (1, 2, 3):
        raise MarkingsError('%s: "pollution_degree" must be 1|2|3' % p)
    if 'materialise' in e and not isinstance(e['materialise'], bool):
        raise MarkingsError('%s: "materialise" must be a boolean' % p)
    out = dict(e)
    out['peak_v'] = float(_v)
    out['voltage_v'] = float(_v)
    return out


def _check_length_group(entry, idx):
    where = 'length_groups[%d]' % idx
    if not isinstance(entry, dict):
        raise MarkingsError('%s: entry must be an object' % where)
    out = dict(entry)
    name = entry.get('name')
    if not isinstance(name, str) or not name:
        raise MarkingsError('%s: "name" (string) is required' % where)
    nets = entry.get('nets')
    pair = entry.get('pair')
    if nets is not None:
        if (not isinstance(nets, list) or len(nets) < 2
                or not all(isinstance(n, str) for n in nets)
                or len(set(nets)) != len(nets)):
            raise MarkingsError('%s: "nets" must be a list of >= 2 distinct net names' % where)
    elif not (isinstance(pair, str) and pair):
        raise MarkingsError('%s: needs "nets" (>= 2) or "pair" (a differential pair name)' % where)
    mode = entry.get('target_mode', 'longest')
    if mode not in ('longest', 'length', 'delay'):
        raise MarkingsError('%s: "target_mode" must be longest|length|delay' % where)
    out['target_mode'] = mode
    if mode in ('length', 'delay'):
        v = entry.get('target_value')
        if not _num(v) or v <= 0:
            raise MarkingsError('%s: "target_value" (positive, mm or ps) is required for target_mode=%s' % (where, mode))
        out['target_value'] = float(v)
    if entry.get('tolerance_mm') is None and entry.get('tolerance_ps') is None:
        raise MarkingsError('%s: a tolerance is required ("tolerance_mm" and/or "tolerance_ps")' % where)
    # #152 (B23): the tolerance TYPE must match the target mode -- a
    # length target graded only by tolerance_ps (or a delay target
    # only by tolerance_mm) applies NO check at all and every member
    # 'passes', however far out it is.
    if mode == 'length' and entry.get('tolerance_mm') is None:
        raise MarkingsError('%s: target_mode=length requires "tolerance_mm" ("tolerance_ps" alone grades nothing)' % where)
    if mode == 'delay' and entry.get('tolerance_ps') is None:
        raise MarkingsError('%s: target_mode=delay requires "tolerance_ps" ("tolerance_mm" alone grades nothing)' % where)
    for key in ('tolerance_mm', 'tolerance_ps'):
        if entry.get(key) is not None:
            if not _num(entry[key]) or entry[key] < 0:
                raise MarkingsError('%s: "%s" must be a non-negative number' % (where, key))
            out[key] = float(entry[key])
    return out


def _check_power_entry(entry, idx):
    # #68: one power-rail marking.  Currents are user-entered (or, from
    # Step 3 on, KB-confirmed values merged in the same shape) -- the
    # authority order is user > KB-confirmed > nothing, and a load
    # with no entry here is UNKNOWN, never estimated.
    where = 'power[%d]' % idx
    if not isinstance(entry, dict):
        raise MarkingsError('%s: entry must be an object' % where)
    net = entry.get('net')
    if not isinstance(net, str) or not net:
        raise MarkingsError('%s: "net" must be a non-empty string' % where)
    out = dict(entry)
    if entry.get('root') is not None and not isinstance(entry['root'], dict):
        raise MarkingsError('%s: "root" must be an object (declared rail root)' % where)
    if entry.get('voltage') is not None:
        v = entry['voltage']
        if not isinstance(v, dict) or not _num(v.get('nominal_v')) or v['nominal_v'] <= 0:
            raise MarkingsError('%s: "voltage.nominal_v" must be a positive number' % where)
        vv = {'nominal_v': float(v['nominal_v'])}
        for key in ('min_v', 'max_v'):
            if v.get(key) is not None:
                if not _num(v[key]) or v[key] <= 0:
                    raise MarkingsError('%s: "voltage.%s" must be a positive number' % (where, key))
                vv[key] = float(v[key])
        vv.setdefault('min_v', vv['nominal_v'])
        vv.setdefault('max_v', vv['nominal_v'])
        if not (vv['min_v'] <= vv['nominal_v'] <= vv['max_v']):
            raise MarkingsError('%s: voltage corners must satisfy min <= nominal <= max' % where)
        out['voltage'] = vv
    if entry.get('drop_budget_v') is not None:
        if not _num(entry['drop_budget_v']) or entry['drop_budget_v'] <= 0:
            raise MarkingsError('%s: "drop_budget_v" must be a positive number' % where)
        out['drop_budget_v'] = float(entry['drop_budget_v'])
    for sect, fields in (('loads', ('peak_a',)), ('regulators', ('type',)),
                         ('conduits', ()), ('protective', ('rating_a',))):
        raw = entry.get(sect)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise MarkingsError('%s: "%s" must be an object keyed by ref' % (where, sect))
        norm = {}
        for ref, spec in raw.items():
            if not isinstance(spec, dict):
                raise MarkingsError('%s: "%s.%s" must be an object' % (where, sect, ref))
            s = dict(spec)
            for f in fields:
                if f not in s and not (sect == 'loads' and f == 'peak_a' and 'kb' in s):
                    raise MarkingsError('%s: "%s.%s" needs "%s"' % (where, sect, ref, f))
            if sect == 'loads':
                if 'peak_a' in s:
                    if not _num(s['peak_a']) or s['peak_a'] < 0:
                        raise MarkingsError('%s: loads.%s.peak_a must be a number >= 0' % (where, ref))
                    s['peak_a'] = float(s['peak_a'])
                    if s.get('sustained_a') is not None:
                        if not _num(s['sustained_a']) or s['sustained_a'] < 0:
                            raise MarkingsError('%s: loads.%s.sustained_a must be a number >= 0' % (where, ref))
                        s['sustained_a'] = float(s['sustained_a'])
                    else:
                        s['sustained_a'] = s['peak_a']  # conservative default, stated
                else:
                    # KB-referenced load (#68 Step 3): currents arrive
                    # only via a confirmed KB estimate at run time.
                    kb = s.get('kb')
                    if not (isinstance(kb, dict) and isinstance(kb.get('mpn'), str)
                            and kb.get('mpn') and isinstance(kb.get('datasheet_rev'), str)
                            and kb.get('datasheet_rev')):
                        raise MarkingsError('%s: loads.%s needs "peak_a" or a "kb" {"mpn", "datasheet_rev"} reference' % (where, ref))
                    s['kb'] = {'mpn': kb['mpn'], 'datasheet_rev': kb['datasheet_rev']}
                if s.get('kind') is not None and s['kind'] not in (
                        'constant_current', 'resistive', 'constant_power'):
                    raise MarkingsError('%s: loads.%s.kind must be constant_current|resistive|constant_power' % (where, ref))
                s.setdefault('kind', 'constant_current')
                if s.get('modes') is not None:
                    if (not isinstance(s['modes'], list)
                            or not all(isinstance(m, str) for m in s['modes'])):
                        raise MarkingsError('%s: loads.%s.modes must be a list of strings' % (where, ref))
                if s.get('duty') is not None:
                    if not _num(s['duty']) or not (0 < s['duty'] <= 1):
                        raise MarkingsError('%s: loads.%s.duty must be in (0, 1]' % (where, ref))
                    s['duty'] = float(s['duty'])
            elif sect == 'regulators':
                if s['type'] not in ('buck', 'ldo', 'switch'):
                    raise MarkingsError('%s: regulators.%s.type must be buck|ldo|switch' % (where, ref))
                if s['type'] == 'buck':
                    if not _num(s.get('eta_min')) or not (0 < s['eta_min'] <= 1):
                        raise MarkingsError('%s: regulators.%s (buck) needs eta_min in (0, 1]' % (where, ref))
                    s['eta_min'] = float(s['eta_min'])
                if s['type'] == 'ldo' and s.get('iq_a') is not None:
                    if not _num(s['iq_a']) or s['iq_a'] < 0:
                        raise MarkingsError('%s: regulators.%s.iq_a must be a number >= 0' % (where, ref))
                    s['iq_a'] = float(s['iq_a'])
            elif sect == 'conduits':
                if s.get('r_mohm') is not None:
                    if not _num(s['r_mohm']) or s['r_mohm'] < 0:
                        raise MarkingsError('%s: conduits.%s.r_mohm must be a number >= 0' % (where, ref))
                    s['r_mohm'] = float(s['r_mohm'])
                if s.get('rating_a') is not None:
                    if not _num(s['rating_a']) or s['rating_a'] <= 0:
                        raise MarkingsError('%s: conduits.%s.rating_a must be a positive number' % (where, ref))
                    s['rating_a'] = float(s['rating_a'])
            elif sect == 'protective':
                if not _num(s['rating_a']) or s['rating_a'] <= 0:
                    raise MarkingsError('%s: protective.%s.rating_a must be a positive number' % (where, ref))
                s['rating_a'] = float(s['rating_a'])
            norm[ref] = s
        out[sect] = norm
    return out


def _reject_constant(name):
    raise ValueError('non-finite number %s in markings' % name)


def load_markings(board_path):
    """Load the sidecar for a board.  None when no sidecar exists;
    MarkingsError when one exists but is malformed."""
    path = markings_path_for(board_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            # #145 (B20 row 7): Python's json accepts NaN/Infinity
            # literals, and NaN defeats every range check below
            # (NaN <= 0 is False, so a NaN z0_ohm validated).
            # Reject non-finite numbers at the source.
            data = json.load(fh, parse_constant=_reject_constant)
    except (OSError, ValueError) as exc:
        raise MarkingsError('cannot read %s: %s' % (path, exc))
    return Markings(validate(data), path=path)


def save_markings(path, data):
    """Validate and write a sidecar dict (atomic via a temp file)."""
    data = validate(data)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write('\n')
    os.replace(tmp, path)
    return path
