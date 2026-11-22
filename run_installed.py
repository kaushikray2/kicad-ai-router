import sys, os, json
sys.path.insert(0, os.path.expanduser('~/.local/share/kicad/10.0/scripting/plugins'))
from ai_router.config import load
from ai_router.pipeline import Pipeline
from ai_router import orchestrator
board = sys.argv[1]
cfg = load(); cfg['mode'] = 'llm'
def log(m):
    print('[air] ' + str(m), flush=True)
pipe = Pipeline(cfg, board, log)
res = orchestrator.run_llm(cfg, pipe, log)
print('RESULT ' + json.dumps(res, default=str)[:1000], flush=True)
