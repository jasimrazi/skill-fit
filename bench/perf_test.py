import os, tempfile, time, subprocess, sys

with tempfile.TemporaryDirectory() as tmp:
    count = 0
    for i in range(100):
        dpath = os.path.join(tmp, "module_{:03d}".format(i))
        os.makedirs(dpath)
        for j in range(100):
            fname = os.path.join(dpath, "file_{:03d}.py".format(j))
            with open(fname, "w") as f:
                f.write("x = 1\n")
            count += 1
    with open(os.path.join(tmp, "package.json"), "w") as f:
        f.write('{"name":"perf-test","scripts":{"build":"tsc","test":"jest"}}')
    print("Created {} files".format(count))

    scan_py = os.path.join(
        os.path.dirname(__file__), "..", "skill", "scripts", "scan.py"
    )
    scan_py = os.path.normpath(scan_py)

    runs = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = subprocess.run(
            [sys.executable, scan_py, tmp],
            capture_output=True, text=True
        )
        elapsed = (time.perf_counter() - t0) * 1000
        runs.append(elapsed)

    avg = sum(runs) / len(runs)
    print("Timings (ms): {}".format(["{:.1f}".format(t) for t in runs]))
    print("Average: {:.1f} ms  limit: 200 ms  PASS: {}".format(avg, avg < 200))
