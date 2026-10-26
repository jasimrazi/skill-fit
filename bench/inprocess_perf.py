import time, os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skill", "scripts")))
from scan import scan

def main():
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(100):
            dpath = os.path.join(tmp, "module_{:03d}".format(i))
            os.makedirs(dpath)
            for j in range(100):
                with open(os.path.join(dpath, "file_{:03d}.py".format(j)), "w") as f:
                    f.write("x = 1\n")
        with open(os.path.join(tmp, "package.json"), "w") as f:
            f.write('{"name":"perf-test","scripts":{"build":"tsc"}}')

        print("10,000 files created.")
        # Warmup
        scan(tmp)
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            scan(tmp)
            times.append((time.perf_counter() - t0) * 1000)

        avg = sum(times) / len(times)
        print("In-process scan(tmp) times (ms): {}".format(["{:.2f}".format(t) for t in times]))
        print("Average in-process: {:.2f} ms -- limit 200ms -- PASS: {}".format(avg, avg < 200))

if __name__ == "__main__":
    main()
