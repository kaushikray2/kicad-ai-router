"""v1.12/#167: result-board naming -- <project>-<YYYYMMDD>-<HHMMSS>,
generated suffixes stripped so names never compound, and the
never-overwrite collision rule. Pure python.
Run: python3 tests/test_result_naming.py"""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import naming

WHEN = datetime(2026, 11, 25, 19, 28, 48)


def _mk(d, *names):
    for n in names:
        open(os.path.join(d, n), 'w').close()


def test_format_from_project_pro():
    d = tempfile.mkdtemp()
    _mk(d, 'routed-6040g.kicad_pcb', 'routed-6040g.kicad_pro')
    got = naming.result_filename(os.path.join(d, 'routed-6040g.kicad_pcb'), WHEN)
    assert got == 'routed-6040g-20261125-192848.kicad_pcb', got


def test_backend_suffix_never_compounds():
    # The owner's failure: a previous backend result re-routed.
    d = tempfile.mkdtemp()
    _mk(d, 'routed-6040g-backend.kicad_pcb', 'routed-6040g.kicad_pro')
    got = naming.result_filename(os.path.join(d, 'routed-6040g-backend.kicad_pcb'), WHEN)
    assert got == 'routed-6040g-20261125-192848.kicad_pcb', got
    assert naming.strip_generated_suffixes('routed-6040g-backend-backend') == 'routed-6040g'


def test_timestamp_suffix_stripped_on_rerun():
    d = tempfile.mkdtemp()
    _mk(d, 'routed-6040g-20261125-192848.kicad_pcb', 'routed-6040g.kicad_pro')
    got = naming.project_stem(os.path.join(d, 'routed-6040g-20261125-192848.kicad_pcb'))
    assert got == 'routed-6040g', got
    assert naming.strip_generated_suffixes('x-20261125-192848-2') == 'x'


def test_fallbacks():
    d = tempfile.mkdtemp()
    _mk(d, 'lonely.kicad_pcb')  # no .kicad_pro anywhere
    assert naming.project_stem(os.path.join(d, 'lonely.kicad_pcb')) == 'lonely'
    d2 = tempfile.mkdtemp()
    _mk(d2, 'board.kicad_pcb', 'board.kicad_pro')  # pro shares the stem
    assert naming.project_stem(os.path.join(d2, 'board.kicad_pcb')) == 'board'


def test_never_overwrite_collision_numbering():
    d = tempfile.mkdtemp()
    _mk(d, 'routed-6040g.kicad_pcb', 'routed-6040g.kicad_pro',
        'routed-6040g-20261125-192848.kicad_pcb')
    p = naming.unique_result_path(os.path.join(d, 'routed-6040g.kicad_pcb'), WHEN)
    assert os.path.basename(p) == 'routed-6040g-20261125-192848-2.kicad_pcb', p
    _mk(d, 'routed-6040g-20261125-192848-2.kicad_pcb')
    p = naming.unique_result_path(os.path.join(d, 'routed-6040g.kicad_pcb'), WHEN)
    assert os.path.basename(p) == 'routed-6040g-20261125-192848-3.kicad_pcb', p
    assert not os.path.exists(p)


if __name__ == '__main__':
    for _n, _f in sorted(list(globals().items())):
        if _n.startswith('test_'):
            _f()
    print('PASS test_result_naming')
