"""#137 (B12) regressions: the server cannot confirm its own
estimates, plain http is loopback-only, and NaN/inf/negative
currents never reach sizing."""
import os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import kbclient, markings

CIT = {'document': 'DS-X Rev C', 'revision': 'C', 'table': 'T1', 'page': 3}


def _est(kind, value, rev='C', **kw):
    e = {'mpn': 'IC-X', 'datasheet_rev': rev, 'kind': kind,
         'value_a': value, 'conditions': 'max', 'citation': CIT}
    e.update(kw)
    return e


def _entries():
    return [markings._check_power_entry({
        'net': 'V5', 'voltage': {'nominal_v': 5},
        'loads': {'U2': {'kb': {'mpn': 'IC-X', 'datasheet_rev': 'C'}}}}, 0)]


def test_server_minted_confirmed_is_ignored(tmp_path):
    cache = kbclient.KbCache(str(tmp_path / 'kb.json'))
    cache.store_estimates([_est('peak', 1.8, confirmed=True)])
    assert cache.confirmed('IC-X', 'C', 'peak') is None
    assert len(cache.pending('IC-X', 'C')) == 1
    entries, res = kbclient.resolve_loads(_entries(), cache, None)
    assert 'U2' not in entries[0]['loads']
    assert {r['ref']: r['outcome'] for r in res}['U2'] == 'kb_unconfirmed'
    # the local confirmation record is the only authority
    assert cache.confirm('IC-X', 'C', 'peak') is True
    got = cache.confirmed('IC-X', 'C', 'peak')
    assert got is not None and got['value_a'] == 1.8 and got['citation'] == CIT


def test_forged_cache_flag_without_local_record_is_ignored(tmp_path):
    cache = kbclient.KbCache(str(tmp_path / 'kb.json'))
    # a cache file as an older/foreign writer could leave it:
    # estimate flagged confirmed, local 'confirmed' map empty
    cache.data['entries']['IC-X@C'] = {
        'estimates': [_est('peak', 9.9, confirmed=True)], 'confirmed': {}}
    assert cache.confirmed('IC-X', 'C', 'peak') is None


def test_https_required_beyond_loopback():
    with pytest.raises(ValueError):
        kbclient.HttpKb('http://kb.example.com:9997/mcp')
    with pytest.raises(ValueError):
        kbclient.HttpKb('http://192.168.1.10:9997/mcp')
    kbclient.HttpKb('https://kb.example.com:9997/mcp')
    kbclient.HttpKb('http://127.0.0.1:9997/mcp')
    kbclient.HttpKb('http://localhost:9997/mcp')
    with pytest.raises(ValueError):
        kbclient.HttpKb('ftp://kb.example.com/mcp')


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1.0, 'high'])
def test_invalid_currents_never_applied(tmp_path, bad):
    cache = kbclient.KbCache(str(tmp_path / 'kb.json'))
    kb = kbclient.DictKb([_est('peak', bad)])
    entries, res = kbclient.resolve_loads(_entries(), cache, kb)
    assert 'U2' not in entries[0]['loads']


def test_confirmed_but_corrupt_value_rejected_at_use(tmp_path):
    cache = kbclient.KbCache(str(tmp_path / 'kb.json'))
    # tampered cache: local confirmation record exists, but the
    # stored value is not a usable current
    cache.data['entries']['IC-X@C'] = {
        'estimates': [_est('peak', float('inf'))],
        'confirmed': {'peak': {'by': 'user', 'citation': CIT}}}
    entries, res = kbclient.resolve_loads(_entries(), cache, None)
    assert 'U2' not in entries[0]['loads']
    assert {r['ref']: r['outcome'] for r in res}['U2'] == 'kb_invalid'
