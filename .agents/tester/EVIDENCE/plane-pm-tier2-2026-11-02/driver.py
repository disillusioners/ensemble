import os, subprocess, sys, time, re

WT = sys.argv[1]
NODES = sys.argv[2]  # path to sorted node list
PROG = sys.argv[3]   # progress.tsv path
FAILED = sys.argv[4] # failed_ids.txt path
CHUNK = int(sys.argv[5]) if len(sys.argv) > 5 else 3000
TIMEOUT = 1500

os.makedirs(os.path.dirname(PROG), exist_ok=True)

with open(NODES) as f:
    nodes = [l.rstrip('\n') for l in f if l.strip()]

# load already-done shard ranges
done = set()
if os.path.exists(PROG):
    with open(PROG) as f:
        for line in f:
            parts = line.strip().split('|')
            if parts and parts[0].startswith('shard'):
                done.add(parts[0])

def run_one(shard_ids, shard_name):
    shard_file = f"/tmp/ppm-tier2/{shard_name}.txt"
    with open(shard_file, 'w') as f:
        for n in shard_ids:
            f.write(n + '\n')
    out_file = f"/tmp/ppm-tier2/{shard_name}.out"
    t0 = time.time()
    proc = subprocess.run(
        ['/tmp/ppm-tier2/run_shard.sh', WT, shard_file, out_file],
        capture_output=True, text=True, timeout=TIMEOUT+120)
    dur = time.time() - t0
    with open(out_file) as f:
        out = f.read()
    # find summary line
    m = re.search(r'(\d+) passed(?:, (\d+) failed)?(?:, (\d+) (?:error|errors))?|(\d+) failed, (\d+) passed', out)
    # Better: search for the pytest summary line
    sm = None
    for line in out.splitlines():
        if re.search(r'\d+ (passed|failed|error)', line) and ('in ' in line or 'warning' in line):
            if 'passed' in line or 'failed' in line or 'error' in line:
                sm = line
    # parse counts
    passed = failed = errors = skipped = xfailed = xpassed = 0
    if sm:
        for mm in re.finditer(r'(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)', sm):
            n = int(mm.group(1)); kind = mm.group(2)
            if kind == 'passed': passed = n
            elif kind == 'failed': failed = n
            elif kind.startswith('error'): errors = n
            elif kind == 'skipped': skipped = n
            elif kind == 'xfailed': xfailed = n
            elif kind == 'xpassed': xpassed = n
    # collect FAILED/ERROR ids
    ids = []
    for line in out.splitlines():
        if line.startswith('FAILED ') or line.startswith('ERROR '):
            ids.append(line.split(' ', 1)[1].strip())
    # detect death: no summary line
    dead = (sm is None)
    # write progress
    with open(PROG, 'a') as f:
        f.write(f"{shard_name}|{len(shard_ids)}|{passed}|{failed}|{errors}|{skipped}|{xfailed}|{xpassed}|{dur:.1f}|rc={proc.returncode}|dead={dead}\n")
    if ids:
        with open(FAILED, 'a') as f:
            for i in ids:
                f.write(i + '\n')
    print(f"{shard_name}: n={len(shard_ids)} P={passed} F={failed} E={errors} S={skipped} xf={xfailed} dur={dur:.1f} dead={dead} ids={len(ids)}", flush=True)
    return dead, ids, (passed, failed, errors, skipped, xfailed, xpassed)

def bisect(ids, name):
    # recursively bisect until singletons
    if len(ids) <= 1:
        dead, ids2, counts = run_one(ids, name)
        return
    mid = len(ids)//2
    bisect(ids[:mid], name + 'a')
    bisect(ids[mid:], name + 'b')

# chunk
idx = 0
shard_no = 0
while idx < len(nodes):
    chunk_ids = nodes[idx:idx+CHUNK]
    shard_name = f"shard{shard_no:03d}"
    if shard_name in done:
        print(f"{shard_name} already done, skip", flush=True)
        idx += CHUNK
        shard_no += 1
        continue
    dead, ids, counts = run_one(chunk_ids, shard_name)
    if dead:
        print(f"DEAD shard {shard_name}, bisecting", flush=True)
        # remove the dead progress row? keep; bisect
        bisect(chunk_ids, shard_name + '_b')
    idx += CHUNK
    shard_no += 1

print("DONE base/head leg")
