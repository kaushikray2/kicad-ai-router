import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_router'))
from config import load
from pipeline import Pipeline
import orchestrator
board = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else 'auto'
cfg = load(); cfg['mode'] = mode
def log(m):
    print('[air] ' + str(m), flush=True)
pipe = Pipeline(cfg, board, log)
res = orchestrator.run_auto(cfg, pipe, log) if mode == 'auto' else orchestrator.run_llm(cfg, pipe, log)
print('RESULT ' + json.dumps(res, default=str)[:1000], flush=True)
