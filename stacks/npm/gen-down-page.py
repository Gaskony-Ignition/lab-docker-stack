#!/usr/bin/env python3
"""Build the page nginx serves when a .test name has nothing behind it.

    python3 stacks/npm/gen-down-page.py > down.html

WHY A PAGE AND NOT A 502

Every .test name in this stack belongs to an optional demo, so on a core-only
machine all of them are dead -- which is correct, and which the browser reports
as "502 Bad Gateway", a message that names no cause and suggests no action. The
first person it happened to went looking for a broken proxy.

The proxy is not broken. The demo is not running, and the thing that knows
which demo a hostname belongs to is demos.json. So this bakes that mapping into
the error page: the hostname you typed, the demo it needs, and the one command
that starts it.

GENERATED, because the mapping is not stable -- a stack added to a demo, or a
demo added or retired, changes which demo a name belongs to.
A hand-written page would be wrong the first time either happened, and wrong in
the place where somebody is already confused.

The lookup happens in the browser from `location.hostname`, so ONE page serves
every proxy host and nginx needs no per-host configuration beyond `error_page`.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
STACKS = os.path.join(REPO, "stacks")


def stack_hosts():
    """{TEST_HOST: stack} from the manifests -- the same source the proxy hosts
    themselves are generated from, so the two cannot disagree."""
    out = {}
    for name in sorted(os.listdir(STACKS)):
        meta = os.path.join(STACKS, name, "stack.meta")
        if not os.path.isfile(meta):
            continue
        with open(meta) as fh:
            m = re.search(r"^TEST_HOST=(.+)$", fh.read(), re.M)
        if m:
            out[m.group(1).strip()] = name
    return out


def build():
    with open(os.path.join(REPO, "demos.json")) as fh:
        d = json.load(fh)
    core = set(d["core"]["stacks"])

    entries = {}
    for host, stack in stack_hosts().items():
        demos = [{"id": x["id"], "title": x["title"]}
                 for x in d["demos"] if stack in x["stacks"]]
        entries[host] = {"stack": stack, "core": stack in core, "demos": demos}
    return entries


PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not running</title>
<style>
  :root { color-scheme: dark }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #12131a; color: #e6e6f0;
    font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  main { max-width: 40rem; padding: 2rem }
  h1 { font-size: 1.5rem; margin: 0 0 .25rem; font-weight: 600 }
  .host { color: #a78bfa }
  p { margin: .8rem 0; color: #b9b9c9 }
  code {
    display: inline-block; background: #1d1f2b; border: 1px solid #2f3243;
    border-radius: 5px; padding: .35rem .6rem; color: #e6e6f0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
  }
  a { color: #a78bfa }
  .muted { color: #7c7f93; font-size: 13px }
</style>
<main>
  <h1><span class="host" id="host">This address</span> is not running</h1>
  <div id="why"><p>Nothing is listening behind this name.</p></div>
  <p class="muted">
    The proxy is fine &mdash; it is the stack behind this name that is stopped.
    <a href="https://console.test/data/perspective/client/GatewayAdmin">Open the
    demo console</a> to start what you need, or run <code>make demos</code>.
  </p>
</main>
<script>
const MAP = __MAP__;
const host = location.hostname;
document.getElementById("host").textContent = host;
const e = MAP[host];
const why = document.getElementById("why");
function html(s) { why.innerHTML = s; }

if (!e) {
  html("<p>No stack in this repo answers to that name. " +
       "Check the spelling, or your hosts file.</p>");
} else if (e.core) {
  // The core is meant to be up. This one really is something being wrong.
  html("<p><b>" + e.stack + "</b> is part of the CORE, which should always be " +
       "running. That makes this a fault rather than a stopped demo.</p>" +
       "<p><code>make up STACK=" + e.stack + "</code></p>");
} else if (!e.demos.length) {
  html("<p><b>" + e.stack + "</b> is stopped, and no demo in demos.json lists " +
       "it &mdash; so nothing starts it for you.</p>" +
       "<p><code>make up STACK=" + e.stack + "</code></p>");
} else {
  const one = e.demos[0];
  const others = e.demos.slice(1);
  let s = "<p><b>" + e.stack + "</b> is stopped. It belongs to the <b>" +
          one.title + "</b> demonstration";
  if (others.length) {
    s += ", and to " + others.map(d => d.title).join(", ");
  }
  s += ".</p><p><code>make demo-start DEMO=" + one.id + "</code></p>";
  if (others.length) {
    s += "<p class=\\"muted\\">or: " +
         others.map(d => "<code>make demo-start DEMO=" + d.id + "</code>").join(" ") +
         "</p>";
  }
  html(s);
}
</script>
"""


if __name__ == "__main__":
    sys.stdout.write(PAGE.replace("__MAP__", json.dumps(build(), indent=2)))
