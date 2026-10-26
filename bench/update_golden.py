import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from scan import scan

fixtures_dir = ROOT / "tests" / "fixtures"
golden_dir = ROOT / "tests" / "golden"

fixtures = ["react-tailwind", "fastapi-docker", "flutter", "monorepo", "malformed-manifest", "empty"]
for f in fixtures:
    res = scan(str(fixtures_dir / f))
    out_file = golden_dir / f"{f}.json"
    with open(out_file, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2)
    h = res.get("hash", "")[:8]
    print(f"Wrote {out_file.name} (hash: {h})")
