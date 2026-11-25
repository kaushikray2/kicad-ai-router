"""Library-rule extraction for the placement audit (#42).

The rules live in the footprint library -- decided on the
ticket: when a part enters the library, its datasheet's layout
section is reviewed and the board-level rules are encoded in
the footprint.  No run-time datasheet extraction, no KB, no
parallel rule database: the footprints are the single source.

This module reads them back, in two places:

  * from a library footprint file (.kicad_mod, parsed here as
    s-expressions -- the same route the power tree uses for
    netlists), and
  * from the board copy's facts (worker auditgeom), so the audit
    can tell which library rules are already present on the
    board and which must be materialised.

Rule encodings recognised in a footprint:
  * rule-area zones (keepout: tracks / vias / copper pour not
    allowed), with their layers and outline;
  * properties (KiCad footprint fields):
      AIRuleDecapMm          -- this part needs a decoupling cap
                                within this many mm (pad to pad,
                                on a shared supply net)
      AIRuleEdgeClearanceMm  -- this part's courtyard must stay
                                at least this far from the board
                                edge
      AIRuleClass            -- explicit rule-carrying class tag
                                (rf_module, gnss_module, crystal,
                                switcher, ...)

fp-lib-table parsing resolves a footprint's library nickname to
a directory; ${KIPRJMOD} and environment variables expand.
"""
import os
import re

from .powertree import _children, parse_sexpr as _parse_sexpr

RULE_PROPERTIES = ('AIRuleDecapMm', 'AIRuleEdgeClearanceMm', 'AIRuleClass')

# Classes known to carry layout rules (ticket): a part in one of
# these classes whose footprint carries NO rules is a library
# gap.  Matched against the footprint's library name + value when
# no explicit AIRuleClass property exists.
CLASS_PATTERNS = (
    ('gnss_module', re.compile(r'gnss|gps|lc76|sam-m|neo-m|ublox|u-blox', re.I)),
    ('rf_module', re.compile(r'\brf\b|lora|sx12|e22-|ble|wifi|esp32|nrf|antenna', re.I)),  # #77: no bare 'module' -- it classified every module-named part (sensor modules, power modules) as RF
    ('crystal', re.compile(r'crystal|xtal|\bosc\b|resonator', re.I)),
    ('switcher', re.compile(r'buck|boost|switching|smps|dc-dc|dcdc', re.I)),
)


def classify(lib_name, value='', properties=None):
    if properties and properties.get('AIRuleClass'):
        return properties['AIRuleClass']
    text = '%s %s' % (lib_name or '', value or '')
    for cls, pat in CLASS_PATTERNS:
        if pat.search(text):
            return cls
    return None


def parse_footprint_rules(text):
    """Extract the rule encodings from .kicad_mod text.
    Returns {'properties': {...}, 'zones': [{layers, keepout,
    outline}]} with outline points in footprint-local mm."""
    root = _parse_sexpr(text)
    props = {}
    for node in _children(root, 'property'):
        if len(node) >= 3 and isinstance(node[1], str) and isinstance(node[2], str):
            if node[1] in RULE_PROPERTIES:
                props[node[1]] = node[2]
    zones = []
    for node in _children(root, 'zone'):
        keep = {}
        ko = _children(node, 'keepout')
        if ko:
            for item in ko[0][1:]:
                if isinstance(item, list) and len(item) >= 2:
                    keep[item[0]] = (item[1] == 'not_allowed')
        layers = []
        for ln in _children(node, 'layers'):
            layers += [x for x in ln[1:] if isinstance(x, str)]
        if not layers:
            for ln in _children(node, 'layer'):
                layers += [x for x in ln[1:] if isinstance(x, str)]
        outline = []
        for poly in _children(node, 'polygon'):
            for pts in _children(poly, 'pts'):
                for xy in _children(pts, 'xy'):
                    try:
                        outline.append([float(xy[1]), float(xy[2])])
                    except (TypeError, ValueError, IndexError):
                        pass
        # canonical keepout triple (the board-side facts carry the
        # same three); pads/footprints allowances are not routing
        # constraints and would break signature comparison
        keep = {k: bool(keep.get(k)) for k in ('tracks', 'vias', 'copperpour')}
        zones.append({'layers': layers, 'keepout': keep, 'outline': outline})
    return {'properties': props, 'zones': zones}


def parse_fp_lib_table(text, prj_dir, env=None):
    """{nickname: directory} from fp-lib-table text."""
    env = dict(os.environ if env is None else env)
    env.setdefault('KIPRJMOD', prj_dir)
    root = _parse_sexpr(text)
    out = {}
    for lib in _children(root, 'lib'):
        name = uri = None
        for item in lib[1:]:
            if isinstance(item, list) and len(item) >= 2:
                if item[0] == 'name':
                    name = item[1]
                elif item[0] == 'uri':
                    uri = item[1]
        if name and uri:
            path = re.sub(r'\$\{([^}]+)\}', lambda m: env.get(m.group(1), m.group(0)), uri)
            path = re.sub(r'\$(\w+)', lambda m: env.get(m.group(1), m.group(0)), path)
            if path.endswith('.pretty'):
                out[name] = path
    return out


def load_library_rules(board_path, lib_tables=None):
    """Resolve every fp-lib-table in scope for a board and return
    {nickname: dir}.  Project table first, then the user-global
    KiCad tables."""
    prj = os.path.dirname(os.path.abspath(board_path))
    tables = list(lib_tables or [])
    cand = [os.path.join(prj, 'fp-lib-table')]
    for ver in ('10.0', '9.0', '8.0'):
        cand.append(os.path.expanduser('~/.config/kicad/%s/fp-lib-table' % ver))
    tables += [p for p in cand if os.path.exists(p)]
    merged = {}
    for p in tables:
        try:
            for k, v in parse_fp_lib_table(open(p).read(), prj).items():
                merged.setdefault(k, v)
        except Exception:
            continue
    return merged


def library_footprint_path(libs, fpid):
    """fpid is 'Nickname:FootprintName' (or a bare name)."""
    if not fpid or ':' not in fpid:
        return None
    nick, name = fpid.split(':', 1)
    d = libs.get(nick)
    if not d:
        return None
    p = os.path.join(d, name + '.kicad_mod')
    return p if os.path.exists(p) else None
