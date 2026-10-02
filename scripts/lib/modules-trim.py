"""Rewrite an Ignition data/modules.json onStartup map.

stdin  -- the current modules.json
stdout -- the human report ("CHANGED n" / "NOCHANGE ..." / the --list table)
stderr -- the NEW modules.json, when there is one

Split out of ign-modules-trim.sh because the Ignition image ships no python3, so
the transform has to run on the host while the write-back runs in the container.
"""
import json
import sys

mode, keep_csv = sys.argv[1], sys.argv[2]
keep = set(k for k in keep_csv.split(",") if k)

try:
    mods = json.load(sys.stdin)
except Exception as exc:  # noqa: BLE001 -- any unreadable registry is fatal here
    print("ERROR cannot parse modules.json: %s" % exc)
    raise SystemExit(3)

want = {m: ("enabled" if (mode == "restore" or m in keep) else "disabled")
        for m in mods}

if mode == "list":
    for mid in sorted(mods):
        print("  %-9s %s" % (mods[mid].get("onStartup", "?"), mid))
    raise SystemExit(0)

changed = [m for m in mods if mods[m].get("onStartup") != want[m]]
if not changed:
    print("NOCHANGE already %s" % mode)
    raise SystemExit(0)

for mid in mods:
    mods[mid]["onStartup"] = want[mid]

sys.stderr.write(json.dumps(mods, indent=2) + "\n")
print("CHANGED %d" % len(changed))
for mid in sorted(changed):
    print("   %-9s %s" % (want[mid], mid))
