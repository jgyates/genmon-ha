#!/usr/bin/env python3
"""Capture genhalink API answers as test fixtures (tests/fixtures/<capture>/).

Runs, for each capture, genmon's generator simulator, genmon and genhalink
from a checkout of the genmon repository (no generator, no root, no network:
everything listens on 127.0.0.1), then saves:

    info.json      GET /api/info
    status.json    GET /api/status
    entities.json  GET /api/entities

The captures are genmon's anonymized register captures in
tests/sim/captures (see captures.json there for the names). Run it again
when genhalink or genmon's entity definitions change, and check the diff.

usage, from the root of this repository:
    python3 tests/capture_fixtures.py <genmon repository> [capture ...]
e.g.
    python3 tests/capture_fixtures.py ../genmon evo_ac evo2_ac nexus_ac evo_lc pz200 powerzone h100 mebay kohler_apm603

genmon's own Python requirements (and aiohttp) must be installed for the
Python that runs this.
"""
import asyncio
import json
import os
import subprocess
import sys
import time

DEFAULT_CAPTURES = ["evo_ac", "evo2_ac", "nexus_ac", "evo_lc", "pz200", "powerzone", "h100", "mebay", "kohler_apm603"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
API_KEY = "fixture-api-key"


async def fetch(port):
    """The answers of a running genhalink on port."""
    import aiohttp

    base = "http://127.0.0.1:%d" % port
    headers = {"Authorization": "Bearer " + API_KEY}
    result = {}
    async with aiohttp.ClientSession() as session:
        # /api/entities answers 503 until genmon delivered complete data
        end = time.time() + 120
        while True:
            async with session.get(base + "/api/entities", headers=headers) as resp:
                if resp.status == 200:
                    result["entities"] = await resp.json()
                    break
                if time.time() > end:
                    raise RuntimeError("entities not ready: HTTP %d" % resp.status)
            await asyncio.sleep(2)
        for name in ("info", "status"):
            async with session.get(base + "/api/" + name, headers=headers) as resp:
                resp.raise_for_status()
                result[name] = await resp.json()
    return result


def capture(sim, MyConfig, genmon_dir, capture_def):
    """Run gensim + genmon + genhalink for one capture and save the answers."""
    gen = sim.SimulatedGenerator(capture_def).start()
    try:
        port = sim.free_port()
        conf = MyConfig(filename=os.path.join(gen.conf, "genhalink.conf"), section="genhalink")
        for key, value in {"port": port, "api_key": API_KEY, "use_https": "False",
                           "zeroconf_enabled": "False", "poll_interval": "2"}.items():
            conf.WriteValue(key, str(value))
        gen.processes.append(gen.run(
            [sys.executable, os.path.join(genmon_dir, "addon", "genhalink.py"), "-c", gen.conf],
            "genhalink_console.txt"))
        gen.wait_for_port(port, 30, "genhalink")
        result = asyncio.run(fetch(port))
    finally:
        for process in gen.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()

    folder = os.path.join(OUT, capture_def["name"])
    os.makedirs(folder, exist_ok=True)
    for key, value in result.items():
        with open(os.path.join(folder, key + ".json"), "w") as f:
            json.dump(value, f, indent=2, sort_keys=True)
            f.write("\n")
    counts = {k: len(v) for k, v in result["entities"].items() if isinstance(v, list)}
    print(capture_def["name"], "saved", counts)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    genmon_dir = os.path.abspath(sys.argv[1])
    names = sys.argv[2:] or DEFAULT_CAPTURES
    os.environ["GENMON_CAPTURES"] = os.path.join(genmon_dir, "tests", "sim", "captures")
    sys.path.insert(0, os.path.join(genmon_dir, "tests"))
    sys.path.insert(0, genmon_dir)
    import test_genmon_sim as sim  # genmon tests/test_genmon_sim.py
    from genmonlib.myconfig import MyConfig

    captures = {c["name"]: c for c in sim.load_captures()}
    failed = False
    for name in names:
        try:
            capture(sim, MyConfig, genmon_dir, captures[name])
        except Exception as err:  # keep going with the other captures
            failed = True
            print(name, "FAILED:", err)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
