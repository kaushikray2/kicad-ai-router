"""Issue #121: layer_cost accepted integer factors only, but the
range where a price nudges rather than switches is roughly
1.1-3 -- mostly fractional (at 10 a priced layer is a near-ban;
at 1 it is free). TraceMaker 0.12.1 accepts fractional factors
(per-mille internally). Both parsers (pipeline + worker) now
accept up to two decimal places; malformed specs still warn +
disable, never raise; an integer spec's formatted form stays
byte-identical to the pre-fractional format.
Run: python3 tests/test_layer_cost_fractional.py"""
import importlib.machinery, importlib.util, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
from pipeline import parse_layer_cost, format_layer_cost

assert parse_layer_cost('In1.Cu=1.5') == ([('In1.Cu', 1.5)], None)
assert parse_layer_cost('In1.Cu=1.25,In2.Cu=2') == ([('In1.Cu', 1.25), ('In2.Cu', 2)], None)
assert parse_layer_cost('In1.Cu=1.1') == ([('In1.Cu', 1.1)], None)
for bad in ('In1.Cu=1.555', 'In1.Cu=abc', 'In1.Cu=1.5.2', 'In1.Cu=-2', 'In1.Cu='):
    pairs, err = parse_layer_cost(bad)
    assert pairs is None and err, (bad, pairs, err)
for out_of_range in ('In1.Cu=0', 'In1.Cu=0.5', 'In1.Cu=1000.5', 'In1.Cu=1001'):
    pairs, err = parse_layer_cost(out_of_range)
    assert pairs is None and 'outside' in err, (out_of_range, pairs, err)
# integer behaviour unchanged, byte-identical formatting
assert parse_layer_cost('In1.Cu=10,In2.Cu=10') == ([('In1.Cu', 10), ('In2.Cu', 10)], None)
assert format_layer_cost([('In1.Cu', 10), ('In2.Cu', 10)]) == 'In1.Cu=10,In2.Cu=10'
assert format_layer_cost([('In1.Cu', 1.5)]) == 'In1.Cu=1.5'
assert format_layer_cost([('In1.Cu', 1.25), ('In2.Cu', 2.0)]) == 'In1.Cu=1.25,In2.Cu=2'

# the worker parser (what Grid._lfac consumes) accepts the same syntax
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')
ld = importlib.machinery.SourceFileLoader('w', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)
by_id, unresolved = w._parse_layer_cost_raw('In1.Cu=1.5')
vals = list(by_id.values()) + [f for _n, f in unresolved]
assert vals == [1.5], (by_id, unresolved)
by_id, unresolved = w._parse_layer_cost_raw('In1.Cu=1.555')
assert not by_id and not unresolved, (by_id, unresolved)
by_id, unresolved = w._parse_layer_cost_raw('In1.Cu=0')
assert not by_id and not unresolved, (by_id, unresolved)
print('PASS test_layer_cost_fractional')
