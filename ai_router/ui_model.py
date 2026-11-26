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

"""Pure dialog model (v1.12, #168): config-string <-> control-state
mapping, board-fact classification and smart-default rules.

No wx, no pcbnew -- everything here is unit-testable headless. The
dialog turns these into widgets; the pipeline keeps consuming the
exact config strings it consumes today (no_tracks_on / layer_cost),
so existing config.json files map losslessly in both directions.
"""

# The measured / useful factor range offered per layer (proposal
# decision 6 recommendation). 'Off' (factor 1, i.e. unpriced) is
# represented by None in the maps below.
COST_PRESETS = [1.25, 1.5, 2.0, 3.0, 5.0, 10.0, 25.0, 100.0]

# Ground-net family for plane detection (proposal section 4.2/4.4:
# GND-class). Power nets (3V3, +5V, ...) deliberately do NOT make a
# layer a default ban -- on the Nomad the 3V3 layer is a pour the
# pipeline's own escalation policy manages, not a hard default law.
GND_FAMILY = {'GND', 'AGND', 'DGND', 'VSS', 'PGND', 'EGND', 'SGND',
              'GNDA', 'GNDD', 'GNDP', 'GNDS'}


def parse_ban_list(spec):
    # 'In1.Cu, In2.Cu' -> ['In1.Cu', 'In2.Cu']; '' / None -> [].
    if not spec:
        return []
    return [x.strip() for x in str(spec).split(',') if x.strip()]


def _factor_ok(tok):
    # Mirror of pipeline._parse_factor (v1.11.5, #121): a number
    # 1..1000 with up to two decimal places. Kept local so this
    # module stays importable without the pipeline; the pipeline
    # remains the authority at run time and rejects identically.
    if not tok or tok.count('.') > 1:
        return None
    whole, dot, frac = tok.partition('.')
    if not whole.isdigit():
        return None
    if dot and (not frac.isdigit() or len(frac) > 2):
        return None
    f = float(tok)
    return f if 1 <= f <= 1000 else None


def parse_cost_map(spec):
    # 'In1.Cu=10,In2.Cu=1.5' -> ({'In1.Cu': 10.0, 'In2.Cu': 1.5}, None).
    # Same whole-spec discipline as pipeline.parse_layer_cost: any
    # malformed token rejects the spec -> (None, reason), and the
    # caller treats cost as off (which is also what the pipeline
    # does with it at run time, so the mapping loses nothing).
    if spec is None:
        return {}, None
    spec = str(spec).strip()
    if not spec:
        return {}, None
    out, seen = {}, set()
    for tok in spec.split(','):
        tok = tok.strip()
        if not tok:
            continue
        if '=' not in tok:
            return None, 'token %r is not LAYER=FACTOR' % tok
        name, _eq, fac = tok.partition('=')
        name = name.strip(); fac = fac.strip()
        if not name:
            return None, 'token %r has an empty layer name' % tok
        f = _factor_ok(fac)
        if f is None:
            return None, 'factor %r for layer %r is not a number 1..1000 (up to two decimal places)' % (fac, name)
        if name.lower() in seen:
            return None, 'layer %r is priced twice' % name
        seen.add(name.lower())
        out[name] = f
    if not out:
        return None, 'spec %r names no layers' % spec
    return out, None


def format_factor(f):
    # 10.0 -> '10', 1.5 -> '1.5', 7.25 -> '7.25' (<= 2 dp by the
    # parse rule, so %g-style trimming is exact).
    s = ('%.2f' % float(f)).rstrip('0').rstrip('.')
    return s if s else '0'


def format_cost_spec(cost_map, layer_order):
    # {canonical: factor} -> 'In1.Cu=10,In2.Cu=1.5' in board stack
    # order; layers outside layer_order (not on this board) keep
    # their entries, appended in name order -- the mapping never
    # silently drops a priced layer.
    parts, done = [], set()
    for name in layer_order:
        if name in cost_map:
            parts.append('%s=%s' % (name, format_factor(cost_map[name])))
            done.add(name)
    for name in sorted(cost_map):
        if name not in done:
            parts.append('%s=%s' % (name, format_factor(cost_map[name])))
    return ','.join(parts)


def format_ban_spec(ban_names, layer_order):
    # Ban names -> comma string in board stack order; names not on
    # this board are preserved after the board layers, in their
    # original relative order (lossless for cross-board configs).
    parts, done = [], set()
    want = list(ban_names)
    for name in layer_order:
        if name in want:
            parts.append(name); done.add(name)
    for name in want:
        if name not in done:
            parts.append(name); done.add(name)
    return ','.join(parts)


def alias_map(layers):
    # layers: [{'canonical': 'In1.Cu', 'name': 'GND'}, ...] ->
    # {lowercased board name OR canonical name: canonical}, the
    # resolve_ban_names discipline (#49) for dialog-side mapping.
    out = {}
    for lay in layers:
        out[lay['name'].strip().lower()] = lay['canonical']
        out[lay['canonical'].strip().lower()] = lay['canonical']
    return out


def resolve_names(names, aliases):
    # -> (resolved canonical names in input order, unresolved names
    # in input order). Unresolved names are preserved by the caller,
    # never dropped.
    res, unres = [], []
    for n in names:
        c = (aliases or {}).get(str(n).strip().lower())
        if c is None:
            unres.append(n)
        elif c not in res:
            res.append(c)
    return res, unres


def classify_layers(layers, zones_by_layer):
    # Per-layer classification from the zone map (proposal 4.2, with
    # the 4.3 worked example as the normative case):
    #   plane  -- INNER layer whose zones carry exactly one distinct
    #             net and that net is GND-class. Surface layers are
    #             never planes (the Nomad's F.Cu carries GND pours
    #             and is a signal layer), and a sole power net (3V3)
    #             makes a pour, not a plane.
    #   pour   -- one or more pour nets have zones on the layer.
    #   signal -- no zones.
    # Returns {canonical: {'kind':..., 'nets': [...], 'note': str}}.
    out = {}
    last = len(layers) - 1
    for i, lay in enumerate(layers):
        canon = lay['canonical']
        nets = sorted(set(zones_by_layer.get(canon) or []))
        inner = 0 < i < last
        if not nets:
            kind, note = 'signal', ''
        elif inner and len(nets) == 1 and nets[0].strip().upper() in GND_FAMILY:
            kind = 'plane'
            note = '%s pour, whole layer' % nets[0]
        else:
            kind = 'pour'
            if len(nets) == 1:
                note = '%s pour -- routing here cuts the pour' % nets[0]
            else:
                note = 'Pours: %s' % ', '.join(nets)
        out[canon] = {'kind': kind, 'nets': nets, 'note': note}
    return out


def recommended_ban(classif):
    # The default law: plane layers only, canonical names (4.2).
    return {c for c, info in classif.items() if info['kind'] == 'plane'}


def law_differs(saved_ban, saved_cost, classif):
    # True when the saved layer law is not the recommended one for
    # this board (different ban set, or anything priced). saved_ban /
    # saved_cost are canonical-resolved (dialog resolves first).
    if set(saved_ban) != recommended_ban(classif):
        return True
    return bool(saved_cost)


def valid_http_url(u):
    # Live validation for URL fields: http(s), a host, and a port
    # (both known backends and the LLM endpoints are host:port).
    if not u:
        return False
    try:
        from urllib.parse import urlparse
        p = urlparse(str(u).strip())
        return p.scheme in ('http', 'https') and bool(p.hostname) and p.port is not None
    except Exception:
        return False


# Keys the dialog sends to the backend as job overrides: the five
# the dialog has always sent, plus max_tool_calls (proposal decision
# 12 recommendation -- run_job.build_cfg already forwards it from
# overrides, pinned by tests/test_build_cfg.py) and the Phase 2 run
# actions deterministic / route_open_only (v1.12.2, #171 -- the
# same forwarding path, pinned the same way). The remaining
# Advanced keys govern local runs from the saved config; on backend
# runs they come from the server's own configuration.
OVERRIDE_KEYS = ('mode', 'max_passes', 'no_tracks_on', 'layer_cost',
                 'strip_existing', 'max_tool_calls',
                 'deterministic', 'route_open_only')


def build_overrides(cfg):
    return {k: cfg[k] for k in OVERRIDE_KEYS if k in cfg}


def capped_open_size(want_w, want_h, client_w, client_h):
    """#169: the dialog's opening size -- the natural size capped at
    the display's usable client area (95% x 93%), with a modest
    floor so the window never opens unusably small (the floor
    itself yields to a tiny display). The cap is a ceiling, not a
    shrink-to-fit: on a large display the natural size passes
    through unchanged. Returns (w, h, min_w, min_h)."""
    max_w = max(320, int(client_w * 0.95))
    max_h = max(320, int(client_h * 0.93))
    min_w = min(560, max_w)
    min_h = min(400, max_h)
    w = max(min(int(want_w), max_w), min_w)
    h = max(min(int(want_h), max_h), min_h)
    return w, h, min_w, min_h


# ------------------------------------------------------- backend servers
# Custom backend servers (#199): user-added entries for the Run on
# group's Backend URL combo, persisted in the plugin config as
# cfg['backend_servers'] = [{'name':..., 'url':..., 'token':...}].
# The built-in presets stay a plain URL list in dialog.py; a custom
# server's identity is its normalised URL -- the same value the
# combo holds while the server is selected -- so dedupe, lookup,
# token sync and removal all key on it. Every function here takes
# and returns plain lists of fresh dicts; inputs are never mutated.

def normalize_server_url(url):
    # The form every comparison uses: stripped, no trailing slash
    # (the same normalisation on_test applies before probing).
    return str(url or '').strip().rstrip('/')


def sanitize_servers(raw):
    # Config-file input is untrusted: keep only well-formed entries
    # (dicts with a non-empty name and URL), token defaulting to ''.
    out = []
    for e in raw or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get('name') or '').strip()
        url = normalize_server_url(e.get('url'))
        if not name or not url:
            continue
        out.append({'name': name, 'url': url,
                    'token': str(e.get('token') or '')})
    return out


def find_server(servers, url):
    want = normalize_server_url(url)
    for e in servers or []:
        if normalize_server_url(e.get('url')) == want:
            return e
    return None


def validate_new_server(name, url, token, preset_urls, servers):
    # -> (entry, None) or (None, reason). The add-server dialog
    # shows the reason inline and does not save on error.
    name = str(name or '').strip()
    url = normalize_server_url(url)
    if not name:
        return None, 'Give the server a name.'
    if not url:
        return None, 'Enter the backend URL.'
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        ok = p.scheme in ('http', 'https') and bool(p.hostname)
    except Exception:
        ok = False
    if not ok:
        return None, 'URL must look like http://host or https://host - got: %s' % url
    if any(normalize_server_url(u) == url for u in preset_urls or []):
        return None, 'That URL is already a built-in preset.'
    if find_server(servers, url) is not None:
        return None, 'A custom server with that URL already exists.'
    return {'name': name, 'url': url, 'token': str(token or '')}, None


def add_server(servers, entry):
    # -> new list with the (validated) entry appended.
    return sanitize_servers(servers) + [dict(entry)]


def remove_server(servers, url):
    # -> new list without the entry whose URL matches.
    want = normalize_server_url(url)
    return [e for e in sanitize_servers(servers)
            if normalize_server_url(e['url']) != want]


def sync_server_token(servers, url, token):
    # The token field was edited while a custom server was the
    # current selection: fold the edit back into its entry, so the
    # next selection of that server restores the edited token.
    # No match (a preset or a hand-typed URL) -> unchanged list.
    out = sanitize_servers(servers)
    e = find_server(out, url)
    if e is not None:
        e['token'] = str(token or '')
    return out


def server_combo_labels(preset_urls, servers):
    # Combo choices in display order: the preset URLs (unchanged
    # from before custom servers existed), then each custom server
    # shown by name. Pair with server_combo_entries.
    return list(preset_urls or []) + [e['name'] for e in sanitize_servers(servers)]


def server_combo_entries(preset_urls, servers):
    # Parallel to server_combo_labels: the custom entry behind a
    # choice index, or None for a built-in preset choice.
    return [None] * len(preset_urls or []) + sanitize_servers(servers)
