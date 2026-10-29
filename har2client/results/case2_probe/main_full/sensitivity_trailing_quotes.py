"""Post-hoc check (SENSITIVITY.md). Run from the extracted runtime_source.zip directory, with the run in
runs/case2_probe_main_rest/ and the 30 task dirs in tasks/tierb50_rest/: uv run python <this file>.
Writes /tmp/c2fix/regrade.json."""
import ast, glob, json, os, subprocess
rows = []
for p in sorted(glob.glob("runs/case2_probe_main_rest/*/*/client.py")):
    s = open(p).read()
    try:
        ast.parse(s); continue
    except SyntaxError:
        pass
    t = s.rstrip()
    if not t.endswith('"""'):
        continue
    fixed = t[:-3].rstrip() + "\n"
    try:
        ast.parse(fixed)
    except SyntaxError:
        continue
    task, cell = p.split("/")[2], p.split("/")[3]
    out = f"/tmp/c2fix/{task}__{cell}"
    os.makedirs(out, exist_ok=True)
    open(f"{out}/client.py", "w").write(fixed)
    subprocess.run(["uv", "run", "har2client", "grade", f"tasks/tierb50_rest/{task}", f"{out}/client.py"], capture_output=True)
    g = json.load(open(f"{out}/grade.json"))
    rows.append({"task": task, "cell": cell, "pass_rate_fixed": g["pass_rate"]})
    print(task, cell, g["pass_rate"], flush=True)
json.dump(rows, open("/tmp/c2fix/regrade.json", "w"), indent=1)
