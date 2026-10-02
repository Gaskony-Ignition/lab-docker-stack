#!/usr/bin/env python3
"""
Make committed Ignition resources diff-stable.

  scripts/normalise.py [Project ...]     default: every project

Two fields in every resource.json are written by the gateway and are pure noise
under version control:

  lastModificationSignature   a hash the gateway computes over the resource's
                              bytes. Committing one is actively harmful: the
                              next machine to deploy pushes a signature that
                              does not match its own files, and the project scan
                              SKIPS that resource without logging anything. This
                              is the single most common reason a deploy "does
                              nothing".

  lastModification.timestamp  changes on every gateway write, so a round trip
                              through the Designer would otherwise show a
                              thousand-file diff with no actual change in it.

Both are stripped here and re-stamped at deploy time by ign-deploy.sh, which is
where they belong -- they describe a deploy, not a source file.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS = os.path.join(ROOT, "ignition", "projects")

EPOCH = "1970-01-01T00:00:00Z"


def normalise(project_dir):
    changed = 0
    for dirpath, _, files in os.walk(project_dir):
        if "resource.json" not in files:
            continue
        p = os.path.join(dirpath, "resource.json")
        with open(p) as fh:
            original = fh.read()
        try:
            d = json.loads(original)
        except json.JSONDecodeError as e:
            print(f"  SKIP {os.path.relpath(p, ROOT)}: invalid JSON -- {e}")
            continue

        attrs = d.setdefault("attributes", {})
        attrs.pop("lastModificationSignature", None)
        d.pop("lastModificationSignature", None)
        attrs["lastModification"] = {"actor": "external", "timestamp": EPOCH}

        rendered = json.dumps(d, indent=2) + "\n"
        if rendered != original:
            with open(p, "w") as fh:
                fh.write(rendered)
            changed += 1
    return changed


def main():
    if not os.path.isdir(PROJECTS):
        print("no ignition/projects directory")
        return 0

    names = sys.argv[1:] or sorted(
        n for n in os.listdir(PROJECTS)
        if os.path.isfile(os.path.join(PROJECTS, n, "project.json"))
    )

    total = 0
    for name in names:
        d = os.path.join(PROJECTS, name)
        if not os.path.isdir(d):
            print(f"  no such project: {name}")
            return 1
        n = normalise(d)
        total += n
        print(f"  {name}: {n} resource(s) normalised")

    print(f"\n{total} file(s) changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
