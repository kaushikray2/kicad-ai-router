"""Benchmark harness: probe boards, run bench_one per board with a timeout,
append results to results.jsonl. Usage: python3 bench.py <board...> """
import sys, os, json, subprocess, time, re
import pcbnew

def mm(v): return v / 1e6

def probe(path):
    b = pcbnew.LoadBoard(path)
    text = open(path).read()
    lb = re.search(r'\(layers\n(.*?)\n\t\)', text, re.S)
    copper = [name for _i, name, _t in re.findall(r'\((\d+) "([^"]+)" (signal|power|mixed|jumper)(?:\s+"[^"]*")?\)', lb.group(1))]
    zone_layers = set()
    for zm in re.finditer(r'\(zone\s', text):
        seg = text[zm.start():zm.start() + 900]
        zone_layers.update(re.findall(r'\(layer "([^"]+)"\)', seg))
        lm = re.search(r'\(layers((?:\s+"[^"]+")+)\)', seg)
        if lm: zone_layers.update(re.findall(r'"([^"]+)"', lm.group(1)))
    banned = []
    inners = copper[1:-1] if len(copper) > 2 else []
    for k, lname in enumerate(inners, start=1):
        if lname in zone_layers:
            banned.append('In%d.Cu' % k)
    bb = b.GetBoundingBox()
    return {'layers': copper, 'banned': banned,
            'footprints': len(list(b.GetFootprints())),
            'nets': b.GetNetCount(),
            'area_cm2': round(mm(bb.GetWidth()) * mm(bb.GetHeight()) / 100.0, 1)}

def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--list':
        boards = [l.strip() for l in open(sys.argv[2]) if l.strip()]
    else:
        boards = sys.argv[1:]
    out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bench-results.jsonl'), 'a')
    for path in boards:
        name = os.path.basename(os.path.dirname(os.path.abspath(path))) + '/' + os.path.basename(path)
        try:
            info = probe(path)
        except Exception as e:
            info = {'probe_error': str(e)[:200]}
        rec = {'board': name, 'info': info}
        print('=== %s %s' % (name, json.dumps(info)), flush=True)
        if 'probe_error' in info:
            rec['result'] = {'status': 'probe_error'}
            out.write(json.dumps(rec) + '\n'); out.flush(); continue
        bench_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bench', os.path.basename(os.path.dirname(os.path.abspath(path))))
        os.makedirs(bench_dir, exist_ok=True)
        dst = os.path.join(bench_dir, 'board.kicad_pcb')
        import shutil; shutil.copy(path, dst)
        pro = os.path.splitext(path)[0] + '.kicad_pro'
        if os.path.exists(pro): shutil.copy(pro, os.path.join(bench_dir, 'board.kicad_pro'))
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bench_one.py'),
                                dst, ','.join(info['banned']), os.environ.get('BENCH_PASSES', '2')],
                               capture_output=True, text=True, timeout=1500)
            lines = [l for l in p.stdout.splitlines() if l.startswith('RESULT ')]
            rec['result'] = json.loads(lines[-1][7:]) if lines else {'status': 'no_result', 'stderr': p.stderr[-300:]}
        except subprocess.TimeoutExpired:
            rec['result'] = {'status': 'timeout', 'seconds': round(time.time() - t0, 1)}
        print('--- %s -> %s' % (name, json.dumps(rec['result'])[:240]), flush=True)
        out.write(json.dumps(rec) + '\n'); out.flush()
    out.close()

if __name__ == '__main__':
    main()
