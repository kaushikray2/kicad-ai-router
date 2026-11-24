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
                          mask?, layers?, gap_mm?, tol_pct?} ],
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
SECTIONS = ('impedance', 'power', 'length_groups', 'hv_domains', 'quiet')


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
        else:
            for e in entries:
                if not isinstance(e, dict):
                    raise MarkingsError('"%s" entries must be objects' % name)
            out[name] = entries
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
