"""#140 (B14) regressions: the engineered modules import as a
package without the GUI plugin registration, and pipeline has
no flat-import fallback -- its handles are defined (real modules
in a healthy install) either way."""
import os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ROOT = os.path.join(os.path.dirname(__file__), '..')

_BLOCKER = '''
import sys
from importlib.abc import MetaPathFinder
class B(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('wx', 'pcbnew'):
            raise ImportError('blocked: ' + name)
        return None
sys.meta_path.insert(0, B())
sys.path.insert(0, %r)
from ai_router import markings, tlcalc, hvdomains, quietnets
from ai_router import pipeline
assert pipeline._markings is not None
assert pipeline._hvdomains is not None
print('headless import ok')
''' % os.path.abspath(ROOT)


def test_modules_import_without_gui():
    r = subprocess.run([sys.executable, '-c', _BLOCKER],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert 'headless import ok' in r.stdout


def test_pipeline_handles_defined():
    from ai_router import pipeline
    for h in ('_markings', '_tlcalc', '_rfcheck', '_powertree',
              '_powerplan', '_wcca', '_kbclient', '_library_rules',
              '_placement_audit', '_lengthmatch', '_hvdomains',
              '_quietnets'):
        assert getattr(pipeline, h) is not None, h
    assert pipeline._ENG_IMPORT_ERROR is None
