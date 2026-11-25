"""KB estimation layer for power-net currents (#68 Step 3).

The knowledge base is a SEPARATE service behind this small
versioned interface (kb-mcp shape).  It supplies structured
electrical values to the deterministic WCCA calculator -- never
layout prose or routing advice.

Authority order (ticket): user-entered values, then KB values
the user has CONFIRMED, then nothing.  Unconfirmed estimates are
never applied to sizing: resolve_loads() fills a load's currents
only from a confirmed estimate, and a load it cannot fill is
removed from the entry so the tree honestly reports the rail
incomplete (resistances-only) instead of sizing on a guess.

Estimate shape (interface version 1):
  {mpn, datasheet_rev, kind: 'peak'|'sustained', value_a,
   conditions, citation: {document, revision, table, page}}

#137 (B12): an estimate is NEVER confirmed by the server.  Any
'confirmed' field in server data is stripped at the cache
boundary; the only confirmation that counts is the LOCAL record
written by confirm() into the cache slot's 'confirmed' map.

The cache key is MPN + datasheet revision -- MPN alone is an
incorrect key (a value extracted from rev A does not describe
rev B silicon).  Confirmations are written back to the endpoint
when it supports it and always recorded in the local cache with
their citation, so a confirmed value keeps tracing to its
datasheet in every later report.
"""
import json
import math
import os
import urllib.request
from urllib.parse import urlparse

INTERFACE_VERSION = 1
_UA = 'kicad-ai-router-kbclient/1'


def cache_key(mpn, datasheet_rev):
    return '%s@%s' % (mpn, datasheet_rev)


class KbCache:
    """Local JSON cache of KB estimates + confirmation records."""

    def __init__(self, path):
        self.path = path
        self.data = {'version': INTERFACE_VERSION, 'entries': {}}
        if path and os.path.exists(path):
            try:
                d = json.load(open(path))
                if isinstance(d, dict) and d.get('version') == INTERFACE_VERSION:
                    self.data = d
            except Exception:
                pass

    def save(self):
        if not self.path:
            return
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def store_estimates(self, estimates):
        for e in estimates:
            # #137 (B12): server data is untrusted at this boundary
            # -- a server-supplied 'confirmed' flag is stripped
            # (confirmation is a local record only), and an
            # estimate whose current is non-numeric, non-finite or
            # negative is dropped outright: it could otherwise be
            # confirmed into a sizing run later.
            e = {k: v for k, v in e.items() if k != 'confirmed'}
            if _valid_current(e.get('value_a')) is None:
                continue
            k = cache_key(e['mpn'], e['datasheet_rev'])
            slot = self.data['entries'].setdefault(k, {'estimates': [], 'confirmed': {}})
            slot['estimates'] = [x for x in slot['estimates']
                                 if x.get('kind') != e.get('kind')]
            slot['estimates'].append(e)
        self.save()

    def confirm(self, mpn, datasheet_rev, kind, confirmed_by='user'):
        k = cache_key(mpn, datasheet_rev)
        slot = self.data['entries'].get(k)
        if not slot:
            return False
        for e in slot['estimates']:
            if e.get('kind') == kind:
                e['confirmed'] = True
                slot['confirmed'][kind] = {'by': confirmed_by,
                                           'citation': e.get('citation')}
                self.save()
                return True
        return False

    def confirmed(self, mpn, datasheet_rev, kind):
        # #137 (B12): the authority is the LOCAL confirmation
        # record in slot['confirmed'] (written by confirm()), never
        # a flag on the estimate itself -- a cache file carrying a
        # server-minted confirmed:true with no local record does
        # not confirm anything.
        slot = self.data['entries'].get(cache_key(mpn, datasheet_rev)) or {}
        rec = (slot.get('confirmed') or {}).get(kind)
        if not rec:
            return None
        for e in slot.get('estimates') or []:
            if e.get('kind') == kind:
                out = dict(e)
                out['confirmed'] = True
                if rec.get('citation'):
                    out['citation'] = rec['citation']
                return out
        return None

    def pending(self, mpn, datasheet_rev):
        slot = self.data['entries'].get(cache_key(mpn, datasheet_rev)) or {}
        done = slot.get('confirmed') or {}
        return [e for e in slot.get('estimates') or []
                if e.get('kind') not in done]


class DictKb:
    """In-process KB (tests, bundled corpora)."""

    def __init__(self, estimates=()):
        self.estimates = list(estimates)
        self.confirmations = []

    def query(self, mpn, condition=None):
        return [dict(e) for e in self.estimates if e.get('mpn') == mpn]

    def write_back_confirmation(self, mpn, datasheet_rev, kind):
        self.confirmations.append((mpn, datasheet_rev, kind))
        for e in self.estimates:
            if (e.get('mpn') == mpn and e.get('datasheet_rev') == datasheet_rev
                    and e.get('kind') == kind):
                e['confirmed'] = True


class HttpKb:
    """kb-mcp-shaped HTTP endpoint: POST {action: 'query', mpn,
    condition, version} -> {estimates: [...]}; confirmations are
    written back with {action: 'confirm', ...}.  Custom endpoints
    are a self-hosted deployment choice (hosted deployments pin
    the endpoint) -- the client itself only speaks http/https."""

    def __init__(self, url, timeout=20):
        # #137 (B12): estimates steer copper sizing, so the path
        # must not be silently interceptable -- https everywhere,
        # plain http only for loopback endpoints (the bundled /
        # self-hosted-on-this-machine case).
        u = urlparse(url)
        loopback = u.hostname in ('localhost', '127.0.0.1', '::1')
        if u.scheme == 'http' and not loopback:
            raise ValueError('KB endpoint must use https '
                             '(plain http is allowed only for loopback endpoints)')
        if u.scheme not in ('http', 'https'):
            raise ValueError('KB endpoint must be http(s)')
        self.url = url
        self.timeout = timeout

    def _post(self, payload):
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'User-Agent': _UA})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def query(self, mpn, condition=None):
        r = self._post({'action': 'query', 'version': INTERFACE_VERSION,
                        'mpn': mpn, 'condition': condition})
        return r.get('estimates') or []

    def write_back_confirmation(self, mpn, datasheet_rev, kind):
        self._post({'action': 'confirm', 'version': INTERFACE_VERSION,
                    'mpn': mpn, 'datasheet_rev': datasheet_rev, 'kind': kind})


def _valid_current(v):
    """A KB current is usable only as a finite, non-negative
    float (#137 (B12): NaN/inf used to flow straight into sizing)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


def resolve_loads(entries, cache, kb=None):
    """Fill KB-referenced loads from CONFIRMED estimates only.

    entries: validated markings power entries (copies are made).
    Returns (entries, resolutions): resolutions records, per ref,
    what happened -- 'kb_confirmed' (with citation), 'user' (left
    untouched), 'kb_unconfirmed' / 'kb_missing' (load removed;
    the tree will report the rail incomplete)."""
    out = []
    resolutions = []
    for entry in entries:
        e = dict(entry)
        loads = {}
        for ref, spec in (entry.get('loads') or {}).items():
            s = dict(spec)
            kbref = s.get('kb')
            if s.get('peak_a') is not None or not kbref:
                loads[ref] = s
                resolutions.append({'ref': ref, 'net': entry['net'],
                                    'outcome': 'user'})
                continue
            mpn, rev = kbref.get('mpn'), kbref.get('datasheet_rev')
            est = cache.confirmed(mpn, rev, 'peak') if cache else None
            if est is None and kb is not None:
                try:
                    found = kb.query(mpn)
                except Exception:
                    found = []
                if cache:
                    cache.store_estimates(found)
                est = cache.confirmed(mpn, rev, 'peak') if cache else None
            if est is None:
                pending = cache.pending(mpn, rev) if cache else []
                resolutions.append({'ref': ref, 'net': entry['net'],
                                    'outcome': 'kb_unconfirmed' if pending else 'kb_missing',
                                    'mpn': mpn, 'datasheet_rev': rev})
                continue  # load removed: rail reports incomplete
            peak = _valid_current(est.get('value_a'))
            if peak is None:
                # #137 (B12): a confirmed-but-corrupt value (NaN,
                # inf, negative, non-numeric) is never applied.
                resolutions.append({'ref': ref, 'net': entry['net'],
                                    'outcome': 'kb_invalid',
                                    'mpn': mpn, 'datasheet_rev': rev})
                continue  # load removed: rail reports incomplete
            s['peak_a'] = peak
            s['source'] = 'kb'
            s['citation'] = est.get('citation')
            sus = cache.confirmed(mpn, rev, 'sustained') if cache else None
            sus_v = _valid_current(sus.get('value_a')) if sus is not None else None
            if sus_v is not None:
                s['sustained_a'] = sus_v
            else:
                s['sustained_a'] = s['peak_a']  # conservative default, stated
            loads[ref] = s
            resolutions.append({'ref': ref, 'net': entry['net'],
                                'outcome': 'kb_confirmed', 'mpn': mpn,
                                'datasheet_rev': rev,
                                'citation': est.get('citation')})
        e['loads'] = loads
        out.append(e)
    return out, resolutions


def confirm_estimate(cache, kb, mpn, datasheet_rev, kind, confirmed_by='user'):
    """The review-queue action: record the confirmation locally
    (with its citation) and write it back to the endpoint."""
    ok = cache.confirm(mpn, datasheet_rev, kind, confirmed_by) if cache else False
    if ok and kb is not None and hasattr(kb, 'write_back_confirmation'):
        try:
            kb.write_back_confirmation(mpn, datasheet_rev, kind)
        except Exception:
            pass  # the local record stands; write-back retries next time
    return ok
