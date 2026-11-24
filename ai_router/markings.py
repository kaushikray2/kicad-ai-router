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
      "length_groups": [ ... ],   # #69, carried, not enforced yet
      "hv_domains":    [ ... ],   # #70, carried, not enforced yet
      "quiet":         [ ... ]    # #71, carried, not enforced yet
    }

Only the impedance section is validated field-by-field in this draft
(it is the one Step 1 enforces); the other sections are checked to be
lists of objects and carried verbatim, unknown keys included, so a
later step never loses data written by an earlier one.
"""

import json
import os

SCHEMA_VERSION = 1
SIDECAR_SUFFIX = '.ai-router-markings.json'
SECTIONS = ('impedance', 'differential', 'power', 'length_groups', 'hv_domains', 'quiet')


class MarkingsError(ValueError):
    """The sidecar exists but is malformed or fails validation."""


def markings_path_for(board_path):
    """Sidecar path for a board file path."""
    root, _ext = os.path.splitext(os.path.abspath(board_path))
    return root + SIDECAR_SUFFIX


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
        else:
            for e in entries:
                if not isinstance(e, dict):
                    raise MarkingsError('"%s" entries must be objects' % name)
            out[name] = entries
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


def load_markings(board_path):
    """Load the sidecar for a board.  None when no sidecar exists;
    MarkingsError when one exists but is malformed."""
    path = markings_path_for(board_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
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
