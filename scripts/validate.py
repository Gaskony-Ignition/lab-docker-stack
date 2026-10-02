#!/usr/bin/env python3
"""
Validate everything in this repo that CI can check without a running gateway.

  scripts/validate.py            all checks
  scripts/validate.py projects   Ignition project resources only
  scripts/validate.py stacks     Compose stacks only

This is the thing that runs in GitHub Actions on every push. It exists because
the work machine is where these files actually run, and a typo that only shows
up there costs a round trip. Everything checked here is checkable from the file
tree alone -- no Docker, no gateway, no secrets.
"""

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS = os.path.join(ROOT, "ignition", "projects")
STACKS = os.path.join(ROOT, "stacks")

# THERE IS NO VENDORED ROOT ANY MORE, and that is the point of the cutover.
#
# `Styles_Template2` used to arrive as a submodule of a PRIVATE repo, so CI
# could not clone it without a credential somebody had to create, and four
# checks were skipped on every push. The look-and-feel is now vendored INTO this
# repo -- the gateway themes under ignition/themes and their style classes in
# the ordinary local project `ignition/projects/Themes` -- so every check runs
# everywhere, and CI is the same gate as a laptop rather than a weaker one.

errors: list[str] = []
warnings: list[str] = []
skips: list[str] = []
checks = 0


def err(msg):
    errors.append(msg)


def skip(msg):
    """A check that could not run. Not a pass, and said out loud."""
    skips.append(msg)


def warn(msg):
    warnings.append(msg)


def rel(p):
    return os.path.relpath(p, ROOT)


# --------------------------------------------------------------------------
# Stack manifests (stack.meta)
# --------------------------------------------------------------------------

# The closed keyword set IS the schema. An unknown key is an error, which is
# what turns a typo (TEST_HOTS=) into a CI failure instead of a name that
# silently never reaches the proxy, the certs or the hosts file.
META_KEYS = {
    "ORDER", "KIND", "PORT_VAR", "PORT_DEFAULT", "STATUS_SCHEME", "STATUS_NOTE",
    "TEST_HOST", "TEST_FORWARD_PORT", "SEED_VOLUME", "SEED_SRC",
    "ROLE", "STANZA", "MQTT_MODULE", "EAM_AGENT", "SITE_PROJECT", "SF_TRANSPORT",
    "GAN_PROVIDER", "SIGNIN_STANZA", "PUBLIC_HOST", "TEST_FALLBACK",
}
META_ENUMS = {
    "KIND": {"service", "gateway"},
    # edge-isolated is a gateway that gets every per-gateway treatment ROLE=edge
    # does (modules, edge-visual, public address, trial, sign-in, proxy host,
    # hosts entry, themes) but is deliberately excluded from
    # `gateways_with_role edge` -- so bootstrap's Gateway Network step, its EAM
    # step, eam-push.sh and sf-gan-history.sh's security-zone grant all skip it
    # by construction, with no per-gateway exception written anywhere. It is
    # the Sparkplug alarms demo's edges: the demo's whole claim is that nothing
    # crosses except the MQTT broker, so a Gateway Network link from one of
    # these would itself be the defect (verify-demos.sh checks for its
    # absence). Also exempt from the ROLE=edge required-field set below, since
    # it carries no EAM_AGENT/SITE_PROJECT/SF_TRANSPORT -- it is never an EAM
    # target and never on a store-and-forward road.
    "ROLE": {"hub", "backup", "edge", "edge-isolated"},
    "MQTT_MODULE": {"engine", "transmission"},
    "SF_TRANSPORT": {"gan", "mqtt"},
    "STATUS_SCHEME": {"none", "http"},
}
# bare value: what a hosts file, a compose service name or a port can contain.
# STATUS_NOTE alone may be quoted free text (no embedded quotes, no escapes).
_META_BARE = re.compile(r"^[A-Za-z0-9._:/-]+$")
_META_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def load_meta(stack):
    """Parse one stack.meta with the same tolerance the shell/PS parsers have:
    a line regex and nothing else. Returns (dict, problems)."""
    path = os.path.join(STACKS, stack, "stack.meta")
    meta, problems = {}, []
    raw = open(path, "rb").read()
    if b"\r" in raw:
        # The shell parser strips \r defensively, but a CR in a committed
        # manifest means the .gitattributes pin failed somewhere -- fail here,
        # where it is visible, not in a hosts file.
        problems.append("contains CR bytes -- must be LF-only")
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _META_LINE.match(line)
        if not m:
            problems.append(f"line {n}: not KEY=VALUE: {line!r}")
            continue
        key, val = m.group(1), m.group(2)
        if key not in META_KEYS:
            problems.append(f"line {n}: unknown key {key}")
            continue
        if key in meta:
            problems.append(f"line {n}: duplicate key {key}")
            continue
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            if key != "STATUS_NOTE":
                problems.append(f"line {n}: only STATUS_NOTE may be quoted")
            val = val[1:-1]
            if '"' in val:
                problems.append(f"line {n}: embedded quote in STATUS_NOTE")
        elif not _META_BARE.match(val):
            problems.append(f"line {n}: bad value for {key}: {val!r} "
                            "(bare values are [A-Za-z0-9._:/-]+, non-empty)")
        if key in META_ENUMS and val not in META_ENUMS[key]:
            problems.append(f"line {n}: {key} must be one of "
                            f"{sorted(META_ENUMS[key])}, got {val!r}")
        meta[key] = val
    return meta, problems


def load_all_meta():
    """stack name -> parsed manifest, for every stack that has one."""
    out = {}
    if not os.path.isdir(STACKS):
        return out
    for name in sorted(os.listdir(STACKS)):
        if os.path.isfile(os.path.join(STACKS, name, "stack.meta")):
            out[name], _ = load_meta(name)
    return out


def meta_stack_order(metas):
    return [s for _, s in
            sorted((int(m.get("ORDER", "0")), s) for s, m in metas.items())]


def check_stack_meta():
    """Every stack must carry a valid manifest, and the facts a manifest
    asserts about its own stack (ports, container name, the edge's -n name)
    must match the compose file they describe."""
    global checks
    metas = {}

    for name in sorted(os.listdir(STACKS)):
        d = os.path.join(STACKS, name)
        if not os.path.isdir(d) or not os.path.isfile(os.path.join(d, "compose.yaml")):
            continue
        checks += 1
        if not os.path.isfile(os.path.join(d, "stack.meta")):
            err(f"stacks/{name}: no stack.meta -- every stack must describe "
                "itself (grammar in docs/PORTABLE.md)")
            continue
        meta, problems = load_meta(name)
        for p in problems:
            err(f"stacks/{name}/stack.meta: {p}")
        metas[name] = meta

        # Required-by-kind. PORT_VAR/PORT_DEFAULT are required on EVERY stack:
        # each one is a row in `make status`.
        required = {"ORDER", "KIND", "PORT_VAR", "PORT_DEFAULT"}
        if meta.get("KIND") == "gateway":
            required |= {"ROLE", "STANZA"}
            if meta.get("ROLE") == "edge":
                required |= {"EAM_AGENT", "SITE_PROJECT", "SF_TRANSPORT"}
                # The Gateway Network road has TWO halves and only one of them
                # is history. Live values come from a remote tag provider the
                # hub mounts, and GAN_PROVIDER is its name there. Required, so
                # a future GAN edge cannot be added with a working trend and a
                # permanently OFFLINE card -- which is what happened here.
                if meta.get("SF_TRANSPORT") == "gan":
                    required |= {"GAN_PROVIDER"}
        for k in sorted(required - set(meta)):
            err(f"stacks/{name}/stack.meta: missing required key {k}")

        # Pairs: half a feature is worse than none.
        if ("TEST_HOST" in meta) != ("TEST_FORWARD_PORT" in meta):
            err(f"stacks/{name}/stack.meta: TEST_HOST and TEST_FORWARD_PORT "
                "come together or not at all")
        # SIGNIN_STANZA gives a non-gateway host the console's sign-in door
        # (the redundant pair's front door is the case it exists for). It has
        # to name a stanza that .gateways.env will actually carry, which is a
        # gateway's STANZA -- otherwise the button 502s at the moment somebody
        # clicks it in front of an audience, and the manifest looks fine.
        # Every gateway must say what address a BROWSER should reach it at.
        # Without it scripts/ign-public-address.sh skips the gateway, it goes on
        # auto-detecting a docker-internal address, and its Launch Perspective
        # button leads somewhere no browser can reach -- which is exactly how
        # both EDGES ended up unset while the redundant pair was configured.
        if meta.get("KIND") == "gateway" and "PUBLIC_HOST" not in meta:
            err(f"stacks/{name}/stack.meta: a gateway needs PUBLIC_HOST -- the "
                "address a browser uses, or it auto-detects an unreachable one")

        if "SIGNIN_STANZA" in meta:
            if "TEST_HOST" not in meta:
                err(f"stacks/{name}/stack.meta: SIGNIN_STANZA without a "
                    "TEST_HOST -- there is no host to sign in to")
            known = {m.get("STANZA") for m in metas.values() if m.get("STANZA")}
            if meta["SIGNIN_STANZA"] not in known:
                err(f"stacks/{name}/stack.meta: SIGNIN_STANZA "
                    f"'{meta['SIGNIN_STANZA']}' is not any gateway's STANZA "
                    f"({', '.join(sorted(known))})")

        # TEST_FALLBACK names the stack the proxy serves when this one is not
        # running. A name that is not a proxied stack would reload into a 502.
        if "TEST_FALLBACK" in meta:
            fb = metas.get(meta["TEST_FALLBACK"])
            if "TEST_HOST" not in meta or not fb or "TEST_FORWARD_PORT" not in fb:
                err(f"stacks/{name}/stack.meta: TEST_FALLBACK "
                    f"'{meta['TEST_FALLBACK']}' needs a TEST_HOST here and a "
                    "stack with TEST_FORWARD_PORT to fall back to")

        if ("SEED_VOLUME" in meta) != ("SEED_SRC" in meta):
            err(f"stacks/{name}/stack.meta: SEED_VOLUME and SEED_SRC "
                "come together or not at all")
        if "SEED_SRC" in meta and not os.path.isdir(os.path.join(d, meta["SEED_SRC"])):
            # warn, not err: a seed source may be generated (`make certs`),
            # so a fresh clone can legitimately lack it. seed_all dies
            # loudly at runtime either way.
            warn(f"stacks/{name}/stack.meta: SEED_SRC '{meta['SEED_SRC']}' "
                 "does not exist (run `make certs`?)")

        compose = open(os.path.join(d, "compose.yaml")).read()

        # container_name == folder name is what lets container name, stack
        # name and manifest name be the same string everywhere.
        m = re.search(r"^\s*container_name:\s*(\S+)", compose, re.M)
        if not m or m.group(1) != name:
            err(f"stacks/{name}/compose.yaml: container_name must be '{name}' "
                f"(got {m.group(1) if m else 'nothing'})")

        # The manifest's port claims must match the compose interpolation --
        # this is the check that catches real drift, not just presence.
        if "PORT_VAR" in meta and "PORT_DEFAULT" in meta:
            m = re.search(r"\$\{%s:-(\d+)\}" % re.escape(meta["PORT_VAR"]), compose)
            if not m:
                err(f"stacks/{name}/compose.yaml: does not interpolate "
                    f"${{{meta['PORT_VAR']}:-...}} named by stack.meta")
            elif m.group(1) != meta["PORT_DEFAULT"]:
                err(f"stacks/{name}: PORT_DEFAULT={meta['PORT_DEFAULT']} but "
                    f"compose defaults ${{{meta['PORT_VAR']}}} to {m.group(1)}")
            example = os.path.join(d, ".env.example")
            if os.path.isfile(example):
                if not re.search(r"^#?%s=" % re.escape(meta["PORT_VAR"]),
                                 open(example).read(), re.M):
                    err(f"stacks/{name}/.env.example: does not mention "
                        f"{meta['PORT_VAR']} (commented default expected)")

        # An edge's EAM_AGENT is the gateway's own system name -- the -n in its
        # compose command. If they differ, EAM registration targets a gateway
        # that does not exist, which fails only at bootstrap time.
        if meta.get("ROLE") == "edge" and "EAM_AGENT" in meta:
            m = re.search(r"^\s*-n\s+(\S+)", compose, re.M)
            if not m or m.group(1) != meta["EAM_AGENT"]:
                err(f"stacks/{name}: EAM_AGENT={meta.get('EAM_AGENT')} but "
                    f"compose -n says {m.group(1) if m else 'nothing'}")
        if meta.get("SITE_PROJECT") and not os.path.isdir(
                os.path.join(PROJECTS, meta["SITE_PROJECT"])):
            err(f"stacks/{name}: SITE_PROJECT={meta['SITE_PROJECT']} does not "
                "exist under ignition/projects/")

    # Cross-stack uniqueness. PORT_DEFAULT collisions are real collisions: two
    # stacks defaulting to one host port cannot both start.
    checks += 1
    for key in ("ORDER", "STANZA", "TEST_HOST", "PORT_DEFAULT",
                "EAM_AGENT", "SITE_PROJECT"):
        seen = {}
        for s, m in metas.items():
            if key in m:
                if m[key] in seen:
                    err(f"stack.meta: duplicate {key}={m[key]} in "
                        f"{seen[m[key]]} and {s}")
                seen[m[key]] = s

    hubs = [s for s, m in metas.items() if m.get("ROLE") == "hub"]
    if len(hubs) != 1:
        err(f"stack.meta: exactly one ROLE=hub expected, found "
            f"{len(hubs)} ({', '.join(hubs) or 'none'})")

    return metas



def _extract_literal(path, name):
    """Pull `NAME = <literal>` out of a JYTHON file. ast.parse on the whole
    file is a trap: these are Jython 2 by declaration (`except Exception, e:`)
    and Python 3 refuses the file outright. The assignment itself is a pure
    literal, so scan from the assignment line accumulating until bracket depth
    returns to zero, then literal_eval the snippet alone."""
    import ast
    lines = open(path).read().splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^%s\s*=\s*(.*)$" % re.escape(name), line)
        if not m:
            continue
        snippet, depth = m.group(1), 0
        depth += snippet.count("[") + snippet.count("(") + snippet.count("{")
        depth -= snippet.count("]") + snippet.count(")") + snippet.count("}")
        j = i
        while depth > 0 and j + 1 < len(lines):
            j += 1
            snippet += "\n" + lines[j]
            depth += lines[j].count("[") + lines[j].count("(") + lines[j].count("{")
            depth -= lines[j].count("]") + lines[j].count(")") + lines[j].count("}")
        try:
            return ast.literal_eval(snippet)
        except (ValueError, SyntaxError) as e:
            err(f"{rel(path)}: cannot read {name} as a literal -- {e}")
            return None
    return None


EDGE_PROJECTS = os.path.join(ROOT, "ignition", "edge-projects")


def _call_span(text, open_paren):
    """From the '(' at `open_paren`, return the index just past its matching
    ')'. Bracket-depth only, same tolerance `_extract_literal` above uses for
    Jython source that Python 3 refuses to parse -- good enough for a call
    whose arguments are not themselves carrying unbalanced parens in a string
    literal, which none here do."""
    depth = 0
    i = open_paren
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


_HTTPCLIENT_CALL = re.compile(r"system\.net\.httpClient\(")
_NO_TIMEOUT_OPTOUT = re.compile(r"#\s*no-timeout:\s*\S")


def check_http_timeouts():
    """Every `system.net.httpClient(...)` call must pass a `timeout=`.

    A Perspective script binding runs on a client polling thread, and Java's
    HTTP client has no connect/read timeout of its own -- a peer that is down
    or still starting hangs the call, and every OTHER binding sharing that
    thread, for as long as the OS takes to give up. This is GUARDS-AUDIT.md
    finding 6: the Store & Forward page once polled its broker's API every 3
    seconds with no timeout, so a starting broker froze the page.
    `gateway_admin._is_up`, `demo_control._call` and `sparkplug_demo`'s
    observer calls already set one, each with a comment saying why -- this
    makes that the rule instead of a convention some call sites happened to
    follow.

    Opt-out: a `# no-timeout: <reason>` comment on the call's own line or the
    line immediately above it. The reason is required -- a bare marker still
    fails, so a suppression cannot be silent.
    """
    global checks
    for root in (PROJECTS, EDGE_PROJECTS):
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
                lines = text.splitlines()
                for m in _HTTPCLIENT_CALL.finditer(text):
                    checks += 1
                    end = _call_span(text, m.end() - 1)
                    if end is None:
                        err(f"{rel(p)}: system.net.httpClient( has no matching "
                            "')' -- cannot check it for timeout=")
                        continue
                    if "timeout=" in text[m.start():end]:
                        continue
                    lineno = text.count("\n", 0, m.start()) + 1
                    idx = lineno - 1
                    near = lines[max(idx - 1, 0):idx + 1]
                    if any(_NO_TIMEOUT_OPTOUT.search(ln) for ln in near):
                        continue
                    err(f"{rel(p)}:{lineno}: system.net.httpClient(...) call with "
                        "no timeout= -- a slow or down peer blocks this call, and "
                        "everything sharing its binding thread, with no ceiling. "
                        "Add timeout=<ms>, or opt out with a "
                        "'# no-timeout: <reason>' comment on this line or the one "
                        "above")


THEMES_DIR = os.path.join(ROOT, "ignition", "themes")
THEME_PROJECT = "Themes"


def load_themes():
    idx = os.path.join(THEMES_DIR, "themes.json")
    if not os.path.isfile(idx):
        return None
    with open(idx, encoding="utf-8") as fh:
        return json.load(fh)


def check_themes(projects):
    """The three things that must agree about what a theme is, and one contract.

    A theme is a gateway CONFIG resource carrying the token layer; the `Themes`
    project carries 69 style classes per theme; `demo_styles.THEMES` is the list
    the picker draws and the gateway validates against. All three are written
    down separately -- a gateway script cannot read this repo, and a config
    resource cannot live in a project -- so all three can disagree, and the way
    they fail is silent in every direction: an id in the picker with no theme
    behind it renders unstyled, a theme with no classes renders a page with no
    cards, and a class the views bind to that no theme defines renders nothing
    at all with no error anywhere.
    """
    global checks
    themes = load_themes()
    checks += 1
    if themes is None:
        err("ignition/themes/themes.json is missing -- run: "
            "python3 scripts/gen-themes.py --src <styles-template-v2>")
        return
    # Built-ins are Perspective's own -- there is no file here to check and no
    # class set to carry. They are in the list so the picker can offer the
    # stock look for comparison; every check below is about what WE ship.
    # TWO lists, and the difference matters. `ids` is what THIS REPO ships and
    # must therefore have files and classes for. `all_ids` includes the
    # built-ins, and is what the picker offers and is_valid() must accept -- a
    # theme the picker can select but is_valid() rejects is a row that silently
    # does nothing.
    ids = [t["id"] for t in themes if not t.get("builtin")]
    all_ids = [t["id"] for t in themes]
    builtin = [t["id"] for t in themes if t.get("builtin")]

    checks += 1
    RESERVED = {"light", "dark", "light-cool", "light-warm",
                "dark-cool", "dark-warm"}
    clash = sorted(set(ids) & RESERVED)
    if clash:
        err(f"vendored theme(s) {clash} use a name Perspective ships -- "
            f"ign-themes.sh refuses these, so they would never install")
    stray = sorted(set(builtin) - RESERVED)
    if stray:
        err(f"theme(s) {stray} are marked builtin but Perspective does not "
            f"ship them -- session.props.theme would find nothing")

    # --- each theme is a complete, installable config resource --------------
    for t in themes:
        if t.get("builtin"):
            continue
        checks += 1
        d = os.path.join(THEMES_DIR, t["id"])
        missing = [f for f in ("config.json", "index.css", "variables.css",
                               "resource.json")
                   if not os.path.isfile(os.path.join(d, f))]
        if missing:
            err(f"theme '{t['id']}': missing {', '.join(missing)}")
            continue
        # The same trap as a project resource, in a different tree: the gateway
        # computes the signature over the resource's own bytes, and a stale one
        # makes the scan skip it with no error. A theme must ship without one.
        with open(os.path.join(d, "resource.json"), encoding="utf-8") as fh:
            if "lastModificationSignature" in fh.read():
                err(f"theme '{t['id']}': resource.json carries a "
                    f"lastModificationSignature -- the scan will skip it silently")

    # --- every class the views bind to exists for EVERY theme ---------------
    #
    # This is the check that replaces the parent project. Inheriting from
    # Styles_Template2 meant 81 packs of 69 classes were either all there or the
    # submodule was missing entirely; vendoring means a theme can be short one
    # class, and the only place that shows is the one screen nobody opened.
    sc = os.path.join(PROJECTS, THEME_PROJECT,
                      "com.inductiveautomation.perspective", "style-classes")
    checks += 1
    if not os.path.isdir(sc):
        err(f"ignition/projects/{THEME_PROJECT} carries no style classes")
        return

    have = {}
    for tid in ids:
        paths = set()
        base = os.path.join(sc, tid)
        for dirpath, _dirs, files in os.walk(base):
            if "style.json" in files:
                paths.add(os.path.relpath(dirpath, base).replace(os.sep, "/"))
        have[tid] = paths

    checks += 1
    first = ids[0]
    for tid in ids[1:]:
        checks += 1
        if have[tid] != have[first]:
            missing = sorted(have[first] - have[tid])
            extra = sorted(have[tid] - have[first])
            err(f"theme '{tid}' does not carry the same classes as '{first}': "
                f"missing {missing[:5]}, extra {extra[:5]}")

    # Every LITERAL class path the views ask for. The dynamic ones
    # ('/alarms/' + a prop) cannot be checked this way, which is why the
    # same-set check above matters: it is what covers the group as a whole.
    wanted = set()
    pat = re.compile(r"\{session\.custom\.style\} \+ '/([a-z0-9/-]+)'")
    for name in projects:
        vroot = os.path.join(PROJECTS, name,
                             "com.inductiveautomation.perspective", "views")
        for dirpath, _dirs, files in os.walk(vroot):
            if "view.json" not in files:
                continue
            with open(os.path.join(dirpath, "view.json"), encoding="utf-8") as fh:
                wanted.update(pat.findall(fh.read()))
    wanted = {w for w in wanted if not w.endswith("/")}

    checks += 1
    absent = sorted(w for w in wanted if w not in have[first])
    if absent:
        err(f"{len(absent)} style class(es) bound in views but absent from every "
            f"theme: {absent[:6]} -- they render with no class and no error")

    # --- demo_styles.THEMES is the same list, in the same order -------------
    for name in sorted(projects):
        lib = os.path.join(PROJECTS, name, "ignition", "script-python",
                           "demo_styles", "code.py")
        if not os.path.isfile(lib):
            continue
        checks += 1
        with open(lib, encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r"# --- BEGIN GENERATED THEMES ---\nTHEMES = \[\n(.*?)\]",
                      src, re.S)
        if not m:
            err(f"{name}/demo_styles: no generated THEMES block")
            continue
        listed = re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', m.group(1))
        if [i for i, _ in listed] != all_ids:
            err(f"{name}/demo_styles.THEMES does not match "
                f"ignition/themes/themes.json -- regenerate with gen-themes.py")
        chosen = re.search(r'^CHOSEN_PACK = "([^"]+)"', src, re.M)
        checks += 1
        if not chosen:
            err(f"{name}/demo_styles: no CHOSEN_PACK")
        elif chosen.group(1) not in all_ids:
            err(f"{name}/demo_styles: CHOSEN_PACK is {chosen.group(1)!r}, which is "
                f"not a theme in the list -- the session would fall back")


def check_duplicated_runtime():
    """Resources this repo deliberately keeps a COPY of in each sibling project
    must stay byte-identical.

    Inheritance here is single-parent and the only shared ancestor is upstream's
    Styles_Template2, which must never be edited -- so anything GatewayAdmin and
    both Sites all need has to be duplicated. `demo_guide` and the Guide views
    are the newest instance of that.

    Duplication is fine; SILENT drift is not. A guide edited in one project and
    not the others reads as correct wherever you happen to look, and wrong on
    the page the customer opens. Comparing bytes is enough: these files are
    copies, not variants -- anything that genuinely differs per site belongs in
    session props, not here.
    """
    global checks
    shared = [
        os.path.join("ignition", "script-python", "demo_guide", "code.py"),
        os.path.join("com.inductiveautomation.perspective", "views", "Guide",
                     "Panel", "view.json"),
        os.path.join("com.inductiveautomation.perspective", "views", "Guide",
                     "Step", "view.json"),
    ]
    for rel_path in shared:
        found = {}
        for proj in sorted(os.listdir(PROJECTS)) if os.path.isdir(PROJECTS) else []:
            full = os.path.join(PROJECTS, proj, rel_path)
            if os.path.isfile(full):
                found[proj] = open(full, "rb").read()
        if len(found) < 2:
            continue
        checks += 1
        first = sorted(found)[0]
        odd = [p for p in sorted(found) if found[p] != found[first]]
        if odd:
            err(f"{rel_path} differs between projects: {first} vs "
                f"{', '.join(odd)} -- these are copies and must stay identical")


MIN_IGN_SERIES = "8.3"


def _series_lt(a, b):
    """True when Ignition series `a` is older than `b` ("8.1" < "8.3")."""
    try:
        return tuple(int(x) for x in a.split(".")) < tuple(int(x) for x in b.split("."))
    except ValueError:
        return False


def check_module_pins():
    """The module version is written in TWO places -- the release URL and
    filename in scripts/modules.manifest, and the tag in scripts/upstream.pins --
    and they must agree.

    They are separate on purpose: the manifest is what `get-modules.sh` fetches
    and hash-checks, the pins file is the record of what this stack was last
    VERIFIED against, and `watch-upstream.sh` compares the latter to GitHub. A
    manifest bumped without the pin would make the watcher report an update that
    had already been taken; a pin bumped without the manifest would silently keep
    fetching the old binary while claiming the new version. Neither fails
    loudly on its own.
    """
    global checks
    manifest = os.path.join(ROOT, "scripts", "modules.manifest")
    pins = os.path.join(ROOT, "scripts", "upstream.pins")
    if not (os.path.isfile(manifest) and os.path.isfile(pins)):
        return

    # The Ignition version is part of the contract: a module built for another
    # minor does not load, and the gateway only logs a mismatch and carries on
    # without it -- which looks exactly like the module not working.
    # EVERY gateway, not the first one found. This used to `break` on the first
    # match, so bumping one compose file and leaving the other three sailed
    # through -- and four gateways on two versions is precisely the state that
    # stops a redundant pair syncing and makes an EAM push to a mismatched edge
    # a gamble. The check that exists to protect the version contract could not
    # see the most likely way to break it.
    ign_by_stack = {}
    for name in sorted(os.listdir(STACKS)):
        cf = os.path.join(STACKS, name, "compose.yaml")
        if not os.path.isfile(cf):
            continue
        body = open(cf).read()
        if "inductiveautomation/ignition" not in body:
            continue
        checks += 1
        # Matches the literal tag and the ${IGNITION_VERSION:-8.3.8} form: the
        # DEFAULT is the repo's declared version, and a per-machine override
        # lives in the gitignored .env, exactly as the host ports do.
        m = re.search(r"image:\s*inductiveautomation/ignition:"
                      r"(?:\$\{IGNITION_VERSION:-)?([0-9][0-9.]*)\}?", body)
        if not m:
            # NOT a silent skip. The old code left `ign` empty here and every
            # check below simply did not run -- so one compose file it could
            # not parse disabled the floor check AND the module cross-check at
            # once, while reporting success.
            err(f"stacks/{name}/compose.yaml runs an Ignition image whose "
                "version cannot be read -- keep it as "
                "`inductiveautomation/ignition:${IGNITION_VERSION:-<version>}` "
                "so validate and watch-upstream.sh can both read it")
            continue
        ign_by_stack[name] = m.group(1)

    if len(set(ign_by_stack.values())) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(ign_by_stack.items()))
        err("the gateways declare different Ignition versions -- they must "
            f"move together ({detail}). `make ignition-version SET=<v>` "
            "writes all four at once")

    ign = sorted(ign_by_stack.values())[0] if ign_by_stack else ""
    series = ".".join(ign.split(".")[:2]) if ign else ""

    if ign and _series_lt(series, MIN_IGN_SERIES):
        err(f"stacks/*/compose.yaml runs Ignition {ign}, but this repo is "
            f"{MIN_IGN_SERIES}+ only -- nothing here is verified below that")

    if ign:
        checks += 1
        head = open(manifest).read()
        m = re.search(r"PINNED TO THE IGNITION VERSION[^(]*\(([0-9.]+)\)", head)
        # BY SERIES, not by full version. The rule this enforces -- and the
        # reason the manifest names a version at all -- is that a module's ABI
        # is per Ignition MINOR. Comparing the full version failed 8.3.8 ->
        # 8.3.10 for a module set that is perfectly good, which is the single
        # most common bump there is; the header still names the exact version
        # the modules were verified against.
        if m and ".".join(m.group(1).split(".")[:2]) != series:
            err(f"scripts/modules.manifest says it is pinned to Ignition "
                f"{m.group(1)} but the compose files run {ign} -- module "
                "versions are per-Ignition-minor and must move together")

    pinned = {}
    for line in open(pins):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            pinned[parts[0]] = parts[1]

    for line in open(manifest):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # The tag can contain slashes -- Embr publishes `releases/8.3/2026.6.17`
        # -- so take everything between /download/ and the FINAL slash, not the
        # first path segment. Getting this wrong reported the tag as "releases".
        m = re.search(r"github\.com/([^/|]+/[^/|]+)/releases/download/(.+)/[^/|]+",
                      line)
        if not m:
            continue
        checks += 1
        repo, tag = m.group(1), m.group(2)
        pin = pinned.get(repo)
        if pin is None:
            err(f"scripts/upstream.pins: {repo} is fetched by modules.manifest "
                "but not pinned -- watch-upstream.sh would never report it")
        elif pin != tag:
            err(f"scripts/modules.manifest fetches {repo} {tag} but "
                f"upstream.pins records {pin} -- run scripts/watch-upstream.sh --pin")
        # Some upstreams publish parallel channels per Ignition minor (Embr
        # tags releases/8.3/<date> beside releases/8.1/<date>). Where the tag
        # names one, it has to be OURS -- picking the wrong channel installs a
        # module that silently never loads.
        if series:
            chan = re.match(r"^releases/(\d+\.\d+)/", tag)
            if chan:
                got = chan.group(1)
                # A FLOOR as well as a match. This stack is 8.3+ and will not go
                # back -- 8.1 is a different Perspective, resource format and
                # module ABI -- and upstreams that publish parallel channels keep
                # cutting 8.1 releases indefinitely, so the newest tag in a repo
                # is regularly the one we must never take.
                if _series_lt(got, MIN_IGN_SERIES):
                    err(f"scripts/modules.manifest fetches {repo} from the {got} "
                        f"channel; this repo is Ignition {MIN_IGN_SERIES}+ only "
                        "and an older module is skipped by the gateway in silence")
                elif got != series:
                    err(f"scripts/modules.manifest fetches {repo} from the "
                        f"{got} channel but this stack runs Ignition "
                        f"{ign} -- that module will not load")

        # Where the tag IS a plain version, the filename must carry it: the
        # release asset often has no version in its name, so that column is
        # what restores it. Skipped for tags that are not versions (Embr's
        # `releases/8.3/<date>` names a channel and a build date instead).
        fname = line.split("|")[0].strip()
        want = tag.lstrip("v")
        if re.match(r"^\d+(\.\d+)+$", want) and want not in fname:
            err(f"scripts/modules.manifest: {fname} does not carry version "
                f"{want} from its own download URL")


def check_proxy_aliases(metas):
    """Every TEST_HOST is also a network alias on the estate proxy.

    The hosts file points a BROWSER at the proxy; the aliases are what make the
    same name mean the same thing to a CONTAINER. For a dashboard the second
    half is a nicety. For anything that follows a redirect server-side it is the
    whole thing -- the control plane signs in to a gateway through the front
    door, because an Ignition session is bound to the origin it was minted on,
    and that only resolves from inside Docker because of these aliases.
    """
    global checks
    want = {m["TEST_HOST"] for m in metas.values() if "TEST_HOST" in m}

    def aliases(path):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            return None
        return set(re.findall(r"aliases:\s*\[([^\]]*)\]", text)
                   ) | {a.strip() for blk in re.findall(r"aliases:\n((?:\s+- \S+\n)+)", text)
                        for a in re.findall(r"- (\S+)", blk)} | {
                       a.strip() for grp in re.findall(r"aliases:\s*\[([^\]]*)\]", text)
                       for a in grp.split(",")}

    npm = aliases(os.path.join(STACKS, "npm", "compose.yaml"))
    if npm is None:
        err("stacks/npm/compose.yaml: unreadable, cannot check proxy aliases")
        return
    for missing in sorted(want - npm):
        err(f"stacks/npm/compose.yaml: {missing} is a TEST_HOST but is not a "
            "network alias -- containers cannot resolve it")
        checks += 1
    for _ in want:
        checks += 1


def check_topology_names(metas):
    """The Jython side of the topology -- gateway_admin.AGENTS, TRIAL_URLS and
    GAN_SERVERS, and sf_demo.TRANSPORTS in every project carrying it -- names
    the same agents, sites and transports the manifests declare. These lists
    stay hand-written (a gateway script cannot read the repo), so this is the
    check that keeps them honest: an edge added to the manifests but not to
    the AGENTS list simply never appears on the EAM page, with no error."""
    global checks
    edges = {s: m for s, m in metas.items() if m.get("ROLE") == "edge"}
    want_agents = {m["EAM_AGENT"] for m in edges.values() if "EAM_AGENT" in m}
    want_pairs = {(m["EAM_AGENT"], m["SITE_PROJECT"], m["SF_TRANSPORT"])
                  for m in edges.values()
                  if all(k in m for k in ("EAM_AGENT", "SITE_PROJECT", "SF_TRANSPORT"))}
    agent_to_stack = {m["EAM_AGENT"]: s for s, m in edges.items() if "EAM_AGENT" in m}

    for proj in sorted(os.listdir(PROJECTS)) if os.path.isdir(PROJECTS) else []:
        ga = os.path.join(PROJECTS, proj, "ignition", "script-python",
                          "gateway_admin", "code.py")
        if os.path.isfile(ga):
            checks += 1
            agents = _extract_literal(ga, "AGENTS") or []
            got = {(a.get("agent"), a.get("site")) for a in agents}
            want = {(a, s) for a, s, _ in want_pairs}
            if got != want:
                err(f"{rel(ga)}: AGENTS says {sorted(got)} but the manifests "
                    f"say {sorted(want)} -- an edge missing here never appears "
                    "on the EAM page")
            gan = _extract_literal(ga, "GAN_SERVERS") or {}
            for agent, container in gan.items():
                if agent_to_stack.get(agent) != container:
                    err(f"{rel(ga)}: GAN_SERVERS maps {agent} -> {container} "
                        f"but the manifests say {agent_to_stack.get(agent)}")
            trial = _extract_literal(ga, "TRIAL_URLS") or []
            for name, url in trial:
                if url is None:
                    continue
                m = re.match(r"http://([a-z0-9-]+):", url)
                if not m or m.group(1) not in metas:
                    err(f"{rel(ga)}: TRIAL_URLS names container "
                        f"{m.group(1) if m else url!r}, which is not a stack")

        sf = os.path.join(PROJECTS, proj, "ignition", "script-python",
                          "sf_demo", "code.py")
        if os.path.isfile(sf):
            checks += 1
            transports = _extract_literal(sf, "TRANSPORTS") or []
            got = {(t.get("agent"), t.get("site"), t.get("transport"))
                   for t in transports}
            if got != want_pairs:
                err(f"{rel(sf)}: TRANSPORTS says {sorted(got)} but the "
                    f"manifests say {sorted(want_pairs)} -- the roads shown "
                    "on the Store & Forward page would not match the edges")

            # A GAN edge's live values are read through the provider the hub
            # mounts it as, so the "[Edge1]..." in `root` and GAN_PROVIDER in
            # the manifest are the SAME name written twice. They disagreed
            # silently for days: the provider was hand-made gateway state that
            # no script recreated, so after a rebuild the trend drew fine (it
            # comes from the historian, a different mechanism) while the card
            # read OFFLINE with three dashes forever.
            checks += 1
            want_roots = {m["EAM_AGENT"]: m["GAN_PROVIDER"]
                          for m in edges.values()
                          if m.get("SF_TRANSPORT") == "gan" and "GAN_PROVIDER" in m}
            for t in transports:
                if t.get("transport") != "gan":
                    continue
                want_p = want_roots.get(t.get("agent"))
                got_p = re.match(r"\[([^\]]+)\]", t.get("root") or "")
                got_p = got_p.group(1) if got_p else None
                if want_p and got_p != want_p:
                    err(f"{rel(sf)}: TRANSPORTS root for {t.get('agent')} reads "
                        f"provider [{got_p}] but the manifest says "
                        f"GAN_PROVIDER={want_p} -- the Site card would read "
                        "OFFLINE while the trend looked fine")



# --------------------------------------------------------------------------
# Ignition projects
# --------------------------------------------------------------------------

def _load_from(root, report=True):
    """Every directory under `root` that has a project.json."""
    out = {}
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        pj = os.path.join(root, name, "project.json")
        if os.path.isfile(pj):
            try:
                out[name] = json.load(open(pj))
                out[name]["_root"] = root
            except json.JSONDecodeError as e:
                if report:
                    err(f"{rel(pj)}: invalid JSON -- {e}")
    return out


def load_projects():
    """Every directory under ignition/projects that has a project.json."""
    return _load_from(PROJECTS)


def check_json_parses():
    """Every .json under a project must parse. A broken view.json takes down a
    scan on the gateway with a far less obvious error than this one."""
    global checks
    for dirpath, _, files in os.walk(PROJECTS):
        for f in files:
            if not f.endswith(".json"):
                continue
            p = os.path.join(dirpath, f)
            checks += 1
            try:
                json.load(open(p))
            except json.JSONDecodeError as e:
                err(f"{rel(p)}: invalid JSON -- line {e.lineno} col {e.colno}: {e.msg}")


def check_resource_manifests():
    """Each resource.json lists the sibling files that make up the resource. If
    that list disagrees with what is on disk the gateway either ignores a file
    you added or logs an error for one you deleted."""
    global checks
    for dirpath, _, files in os.walk(PROJECTS):
        if "resource.json" not in files:
            continue
        p = os.path.join(dirpath, "resource.json")
        checks += 1
        try:
            d = json.load(open(p))
        except json.JSONDecodeError:
            continue  # already reported

        declared = set(d.get("files", []))
        actual = {f for f in files if f != "resource.json"}

        for missing in sorted(declared - actual):
            err(f"{rel(p)}: declares '{missing}' but that file is not on disk")
        for extra in sorted(actual - declared):
            # thumbnail.png is written by the Designer and is genuinely not
            # part of the resource manifest.
            if extra == "thumbnail.png":
                continue
            warn(f"{rel(p)}: '{extra}' is on disk but not listed in \"files\"")


def check_inheritance(projects, known=None):
    """Parent must exist, must be marked inheritable, and the graph must be
    acyclic. Ignition will refuse to load a project whose parent is missing, and
    a cycle hangs the project loader rather than erroring cleanly."""
    global checks
    known = known if known is not None else projects
    for name, cfg in projects.items():
        checks += 1
        parent = (cfg.get("parent") or "").strip()
        if not parent:
            continue
        if parent not in known:
            err(f"project '{name}': parent '{parent}' is not a project in "
                f"ignition/projects/")
            continue
        if not known[parent].get("inheritable", False):
            err(f"project '{name}': parent '{parent}' has \"inheritable\": false "
                f"-- a project cannot inherit from it")

    # cycle detection
    for name in projects:
        seen, cur = [], name
        while cur:
            if cur in seen:
                err(f"inheritance cycle: {' -> '.join(seen + [cur])}")
                break
            seen.append(cur)
            cur = (known.get(cur, {}).get("parent") or "").strip() or None


def check_project_json_shape(projects):
    """project.json needs these four keys; a missing 'enabled' silently yields a
    disabled project, which looks exactly like a failed deploy."""
    global checks
    for name, cfg in projects.items():
        checks += 1
        for key, typ in (("title", str), ("enabled", bool),
                         ("inheritable", bool), ("parent", str)):
            if key not in cfg:
                err(f"project '{name}': project.json is missing \"{key}\"")
            elif not isinstance(cfg[key], typ):
                err(f"project '{name}': project.json \"{key}\" should be "
                    f"{typ.__name__}, got {type(cfg[key]).__name__}")


# Every view a project can reach: its own, plus everything it inherits.
#
# Returns (views, complete). `complete` is False when an ancestor could not be
# resolved -- which WITHOUT THE SUBMODULE is every project here, since the
# parent is the template. The set is then a subset of what really exists, so a
# target missing from it proves nothing and the caller must skip rather than
# fail. check_popup_targets failed instead for two commits (24/08/2026),
# reporting the template's own Styles/Switcher as a view that does not exist --
# the moment this repo started inheriting a view rather than shipping a copy of
# it, CI went red on every push while validate stayed green on any machine with
# the submodule checked out.
#
# One copy, used by both callers. There were two, and patching the wrong one is
# how the above took a second attempt to fix.
def views_available(name, known):
    def walk(name, seen):
        if name in seen:
            return set(), True
        if name not in known:
            return set(), False
        seen.add(name)
        base = os.path.join(known[name].get("_root", PROJECTS), name,
                            "com.inductiveautomation.perspective", "views")
        out = set()
        if os.path.isdir(base):
            for dirpath, _, files in os.walk(base):
                if "view.json" in files:
                    out.add(os.path.relpath(dirpath, base))
        parent = (known[name].get("parent") or "").strip()
        if not parent:
            return out, True
        up, complete = walk(parent, seen)
        return out | up, complete
    return walk(name, set())


def check_page_config(projects, known=None):
    """Every page route must point at a view that exists -- in the project
    itself or in something it inherits from. A dangling route renders a blank
    page in the browser with nothing in the gateway log."""
    global checks
    known = known if known is not None else projects

    for name in projects:
        cfg_path = os.path.join(PROJECTS, name, "com.inductiveautomation.perspective",
                                "page-config", "config.json")
        if not os.path.isfile(cfg_path):
            continue
        checks += 1
        try:
            pages = json.load(open(cfg_path)).get("pages", {})
        except json.JSONDecodeError:
            continue
        available, complete = views_available(name, known)
        for route, page in pages.items():
            vp = page.get("viewPath")
            if not vp or vp in available:
                continue
            if not complete:
                skip(f"project '{name}': page route '{route}' -> view '{vp}' "
                     f"not checked -- an ancestor cannot be resolved")
                continue
            err(f"project '{name}': page route '{route}' -> view '{vp}' "
                f"which does not exist in this project or its parents")


def check_popup_targets(projects, known=None):
    """Every view named in an openPopup() call must resolve.

    Page ROUTES were already checked; popup targets were not, because they are
    string literals buried inside script blocks rather than declared anywhere.
    That gap is exactly how retiring a parent project left GatewayAdmin's
    Appearance button opening a view that no longer existed: nothing failed to
    deploy, nothing logged, and the popup rendered as a small error box -- which
    is indistinguishable from the inheritance-wedge symptom and sends you
    chasing the wrong fault.
    """
    global checks
    known = known if known is not None else projects

    # openPopup(id, 'Some/View', ...) -- the view path is the second argument.
    # Inside a view.json the script is one JSON string, so its newlines and tabs
    # are the two-character sequences \n and \t, not whitespace. A regex using
    # \s* matches nothing here and the check silently passes everything --
    # which is worse than not having it.
    gap = r"(?:\\[nrt]|\s)*"
    quote = r"['\"]"
    call = re.compile("openPopup\\(" + gap + quote + "[^'\"]*" + quote +
                      gap + "," + gap + quote + "([^'\"]+)" + quote)
    for name in projects:
        base = os.path.join(PROJECTS, name, "com.inductiveautomation.perspective", "views")
        if not os.path.isdir(base):
            continue
        available, complete = views_available(name, known)
        for dirpath, _, files in os.walk(base):
            if "view.json" not in files:
                continue
            path = os.path.join(dirpath, "view.json")
            try:
                raw = open(path).read()
            except OSError:
                continue
            for target in set(call.findall(raw)):
                if target in available:
                    checks += 1
                    continue
                if not complete:
                    skip("%s: openPopup target '%s' not checked -- project "
                         "'%s' has an ancestor that cannot be resolved"
                         % (rel(path), target, name))
                    continue
                checks += 1
                err("%s: openPopup opens view '%s', which does not exist "
                    "in this project or its parents" % (rel(path), target))


def check_no_signatures():
    """lastModificationSignature must not be committed. It is computed by a
    specific gateway against specific bytes; committing one means the next
    machine to deploy carries a signature that does not match its own files,
    and the scan silently skips the resource."""
    global checks
    hits = []
    for dirpath, _, files in os.walk(PROJECTS):
        if "resource.json" not in files:
            continue
        p = os.path.join(dirpath, "resource.json")
        checks += 1
        try:
            raw = open(p).read()
        except OSError:
            continue
        if "lastModificationSignature" in raw:
            hits.append(rel(p))
    if hits:
        err(f"{len(hits)} resource.json file(s) still carry a lastModificationSignature "
            f"(first: {hits[0]}) -- run: make normalise")


# --------------------------------------------------------------------------
# Compose stacks
# --------------------------------------------------------------------------


def check_stacks():
    """Each stack needs a compose file and a committed .env.example. Every
    ${VAR} the compose file interpolates must be declared in that example, or
    the stack comes up on the work machine with an empty password."""
    global checks
    if not os.path.isdir(STACKS):
        return

    for name in sorted(os.listdir(STACKS)):
        d = os.path.join(STACKS, name)
        if not os.path.isdir(d):
            continue
        compose = os.path.join(d, "compose.yaml")
        example = os.path.join(d, ".env.example")
        checks += 1

        if not os.path.isfile(compose):
            # A STACK RETIRED ON ANOTHER MACHINE ARRIVES AS AN EMPTY DIRECTORY.
            # git removes the tracked files -- compose.yaml, stack.meta,
            # .env.example -- and then cannot remove the directory, because the
            # gitignored `.env` is still sitting in it. So retiring a stack
            # passes CI and every clone that ever ran that stack starts failing
            # validate on the pull, with a message naming a missing file.
            #
            # Found on the work machine 24/08/2026: three at once, from one
            # `git pull`. Not deleted here, because the leftover is that
            # machine's credentials for the retired stack and deleting somebody's
            # secrets is not validate's job. A WARNING, not an error: self-update
            # validates BEFORE it runs the migrations, and a migration is what
            # clears a retired stack (1.0.0-06 for emqx) -- as an error it
            # blocked the very update that removes it (29/09/2026, stacks/emqx
            # holding .env and certs/).
            left = sorted(os.listdir(d))
            tracked = {"compose.yaml", "stack.meta", ".env.example"}
            if not tracked & set(left):
                warn(f"stacks/{name}: a retired stack's leftovers "
                     f"({', '.join(left) or 'empty'}), which git cannot remove. "
                     f"`make migrate` removes known ones; otherwise delete "
                     f"stacks/{name}")
            else:
                err(f"stacks/{name}: no compose.yaml")
            continue
        if not os.path.isfile(example):
            err(f"stacks/{name}: no .env.example (it is what CI and a fresh "
                f"clone build the real .env from)")
            continue

        declared = set()
        for line in open(example):
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
            if m:
                declared.add(m.group(1))

        body = open(compose).read()
        # ${VAR}, ${VAR:-default} and ${VAR-default}
        for m in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*)(:?-[^}]*)?\}", body):
            var, default = m.group(1), m.group(2)
            if var in declared or default is not None:
                continue
            if var in ("HOME", "PWD", "USER", "STACKS_DIR"):
                continue  # supplied by the shell, not by .env
            err(f"stacks/{name}/compose.yaml uses ${{{var}}} but "
                f".env.example does not declare it")

        if "backbone" not in body:
            warn(f"stacks/{name}: does not join the 'backbone' network -- "
                 f"it will not be able to reach the other stacks")


# --------------------------------------------------------------------------

def check_app_bar_hidden(projects):
    """Every Perspective project must hide the app bar.

    The app bar is the gateway-branded strip Perspective docks at the bottom of
    a session, reachable from a floating toggle in the corner. It is fine while
    developing and wrong in front of a customer: it shows the gateway name, the
    Ignition version and a link into the gateway itself, on top of the screen
    being demonstrated.

    This is checked rather than merely documented because a child project's
    session-props resource REPLACES its parent's wholesale instead of merging,
    so setting it once on the template does NOT reach the sites. Every project
    has to carry it, which is exactly the kind of thing that gets forgotten on
    the next project someone adds.
    """
    global checks
    for name in sorted(projects):
        props = os.path.join(PROJECTS, name,
                             "com.inductiveautomation.perspective",
                             "session-props", "props.json")
        if not os.path.isfile(props):
            continue
        checks += 1
        try:
            d = json.load(open(props))
        except json.JSONDecodeError:
            continue        # check_json_parses already reported it
        pos = (d.get("props", {}).get("appBar", {}) or {}).get("togglePosition")
        if pos != "hidden":
            err(f"{name}: session props must set "
                f"props.appBar.togglePosition = \"hidden\" (found "
                f"{pos!r}) -- the app bar exposes the gateway in a demo")


def check_stack_labels():
    """Every service says which stack it belongs to, in the committed file.

    Ownership used to be inferred from the compose project name, and that was
    wrong in both directions on one machine: `ignition-module-testing` belongs
    to a different repo and carries project `ignition`, while this repo's own
    hub carries `wd-ignition` because stacks/ignition/.env renames it to dodge
    that very collision -- and that .env is gitignored and per machine, so the
    project name is not even stable across machines.

    So each service carries `au.gaskony.wd.stack: "<stack>"`, and the demo
    console reads that. A new stack without it is invisible to the console, and
    a copied-and-pasted one carrying the WRONG stack name is worse: its
    containers are attributed to another demo, which then reports itself running
    when it is not.
    """
    global checks
    label = "au.gaskony.wd.stack"
    service = re.compile(r"^  ([A-Za-z0-9][\w.-]*):\s*$")
    for stack in sorted(os.listdir(STACKS)):
        path = os.path.join(STACKS, stack, "compose.yaml")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        try:
            start = next(i for i, l in enumerate(lines) if l.rstrip() == "services:")
        except StopIteration:
            continue
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i] and lines[i][0].isalpha()), len(lines))
        starts = [i for i in range(start + 1, end) if service.match(lines[i])]
        for n, i in enumerate(starts):
            checks += 1
            name = service.match(lines[i]).group(1)
            block = lines[i:(starts[n + 1] if n + 1 < len(starts) else end)]
            wanted = '%s: "%s"' % (label, stack)
            if not any(wanted in l for l in block):
                if any(label in l for l in block):
                    err(f"stacks/{stack}/compose.yaml: service '{name}' carries "
                        f"{label} for a DIFFERENT stack -- its containers would be "
                        f"attributed to that one")
                else:
                    err(f"stacks/{stack}/compose.yaml: service '{name}' has no "
                        f"{label} label, so the demo console cannot tell it is ours")


def check_card_order_matches_tabs(demos):
    """The cards are drawn left-to-right in demos.json's order; the tabs are
    drawn in demo_control.TAB_KEYS' order. They have to be the same order.

    Two orderings of three things, in two files, with nothing joining them:
    reorder either one and the page still renders perfectly, every card still
    points at the right tab, and every label is still correct. It is only wrong
    to a reader, who pairs the first card with the first tab by POSITION before
    reading either label -- which is what happened here (cards ran redundancy,
    store-forward, EAM under tabs running EAM, store-forward, redundancy).

    TAB_KEYS is Jython 2 and the module does not parse as Python 3, so the list
    is extracted as a snippet and literal_eval'd rather than ast.parse'd.
    """
    global checks
    path = os.path.join(ROOT, "ignition", "projects", "GatewayAdmin", "ignition",
                        "script-python", "demo_control", "code.py")
    checks += 1
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"^TAB_KEYS\s*=\s*(\[[^\]]*\])", src, re.M)
    if not m:
        err("demo_control: no TAB_KEYS list -- the tab order cannot be cross-checked "
            "against demos.json, so the two can drift apart silently")
        return
    try:
        tab_keys = ast.literal_eval(m.group(1))
    except (ValueError, SyntaxError) as e:
        err(f"demo_control: TAB_KEYS does not literal_eval ({e})")
        return

    tabs = [k for k in tab_keys if k]
    cards = [d.get("tab", "") for d in demos.get("demos", [])]
    checks += 1
    if cards != tabs:
        err("demos.json: the cards are ordered " + ", ".join(cards) +
            " but the tabs are ordered " + ", ".join(tabs) +
            " -- reorder demos.json's `demos` list (or demo_control.TAB_KEYS) so a "
            "card and its tab are in the same place along the row")


def check_component_shape():
    """Every component in every view carries a `type` and a `meta.name`.

    Perspective's deserialiser reads both unconditionally. A component missing
    either takes down THE WHOLE VIEW with
    `NullPointerException ... JsonObject.get(String) is null`, and what the
    browser shows is **"View Not Found -- View with configured path not found in
    the project"** -- which reads as a missing or misnamed view, not as one
    absent key three levels down inside a view that is right there. The gateway
    log has the real answer but names only the view, not the component.

    Cost one debugging cycle on 24/08/2026: a generated flex-repeater was
    written without its `type`. Cheap to check, and the failure is otherwise
    indistinguishable from a deploy that did not land.
    """
    global checks
    for project in sorted(os.listdir(PROJECTS)):
        views = os.path.join(PROJECTS, project,
                             "com.inductiveautomation.perspective", "views")
        if not os.path.isdir(views):
            continue
        for dirpath, _dirnames, files in os.walk(views):
            if "view.json" not in files:
                continue
            path = os.path.join(dirpath, "view.json")
            try:
                with open(path, encoding="utf-8") as fh:
                    spec = json.load(fh)
            except ValueError:
                continue                 # check_json_parses owns that failure
            bad = []

            def walk(node, where):
                if not isinstance(node, dict):
                    return
                if "type" not in node:
                    bad.append(where + " has no `type`")
                elif not (node.get("meta") or {}).get("name"):
                    bad.append(where + " has no `meta.name`")
                for i, child in enumerate(node.get("children") or []):
                    name = (child.get("meta") or {}).get("name", i) \
                        if isinstance(child, dict) else i
                    walk(child, where + "/" + str(name))

            walk(spec.get("root") or {}, "root")
            checks += 1
            for problem in bad:
                err(f"{rel(path)}: {problem} -- Perspective fails the whole "
                    f"view and the browser says 'View Not Found'")


def check_demos_fit_the_frame(demos):
    """The tile row's column count lives in TWO files, and they must agree.

    `demo_control.ACROSS` decides how many dotted slots pad the last row out;
    the `demos` repeater's `elementPosition.basis` in Demos/view.json decides
    how many tiles actually fit on a line. Change one and not the other and
    nothing errors -- you get a row of five tiles where four fit, so the fifth
    wraps alone and the padding fills a row nobody can see, or four slots
    padding a row that holds three.

    Deliberately NOT a cap on how many demos there may be: padding is derived
    (`(-n) % ACROSS`), so any number of demos fills whole rows on its own. The
    old version capped it at a fixed SLOTS, which was the six-tile frame -- and
    that frame is what reserved a whole empty second row.
    """
    global checks
    path = os.path.join(ROOT, "ignition", "projects", "GatewayAdmin", "ignition",
                        "script-python", "demo_control", "code.py")
    view = os.path.join(ROOT, "ignition", "projects", "GatewayAdmin",
                        "com.inductiveautomation.perspective", "views", "Demos",
                        "view.json")
    checks += 1
    if not (os.path.exists(path) and os.path.exists(view)):
        return
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"^ACROSS\s*=\s*(\d+)", src, re.M)
    if not m:
        err("demo_control: no ACROSS constant -- the tile row's column count "
            "cannot be cross-checked against the repeater's basis")
        return
    across = int(m.group(1))
    checks += 1
    if across < 2:
        err("demo_control.ACROSS is %d -- a row of one is not a row" % across)
        return

    with open(view, encoding="utf-8") as fh:
        spec = json.load(fh)
    band = next((c for c in spec["root"]["children"]
                 if c.get("meta", {}).get("name") == "demos"), None)
    checks += 1
    if band is None:
        err("Demos/view.json: no `demos` band -- the tile row cannot be found, "
            "so its basis cannot be checked against demo_control.ACROSS")
        return
    basis = str(band.get("props", {}).get("elementPosition", {}).get("basis", ""))
    checks += 1
    pct = re.match(r"^(\d+(?:\.\d+)?)%$", basis)
    if not pct:
        err("Demos/view.json: the demos repeater's basis is %r -- it must be a "
            "plain percentage so the column count is checkable, and because "
            "calc() does not satisfy Perspective's css-length schema" % basis)
        return
    # `n` columns fit while n*basis <= 100. Assert that is exactly ACROSS.
    fits = int(100.0 / float(pct.group(1)))
    checks += 1
    if fits != across:
        err("demo_control.ACROSS is %d but the demos repeater's basis of %s "
            "fits %d per row -- the padding and the layout disagree, so a row "
            "will be short or a tile will wrap alone. Set the basis near %d%%."
            % (across, basis, fits, int(100 / across)))


def check_demos():
    """demos.json and stacks/ agree, and the core is not a demo.

    The console starts what a demo declares, so a stack missing from every demo
    is a stack nobody can start from the page -- and it will not announce
    itself, it will simply never appear. That is the failure this catches.

    The core is separate on purpose. It carries the gateway that serves the
    page, so listing it as part of a demo would make it stoppable from a button
    that is being served by it. `make down` still stops everything; the command
    line keeps the big hammer.
    """
    global checks
    path = os.path.join(ROOT, "demos.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            demos = json.load(fh)
    except ValueError as e:
        err(f"demos.json: {e}")
        return

    on_disk = {d for d in os.listdir(STACKS)
               if os.path.isfile(os.path.join(STACKS, d, "stack.meta"))}
    core = set(demos.get("core", {}).get("stacks", []))
    checks += 1
    if not core:
        err("demos.json: no core -- something has to stay up to serve the page")

    seen_ids = set()
    covered = set(core)
    for demo in demos.get("demos", []):
        checks += 1
        did = demo.get("id", "")
        if not re.match(r"^[a-z][a-z0-9-]*$", did):
            err(f"demos.json: '{did}' is not a usable demo id (lower-case, digits, hyphen)")
        if did in seen_ids:
            err(f"demos.json: two demos share the id '{did}'")
        seen_ids.add(did)
        if not demo.get("title") or not demo.get("blurb"):
            err(f"demos.json: {did} needs a title and a blurb -- the card is the "
                f"only thing telling someone what it demonstrates")
        for stack in demo.get("stacks", []):
            checks += 1
            if stack not in on_disk:
                err(f"demos.json: {did} wants stack '{stack}', which has no stacks/{stack}/stack.meta")
            if stack in core:
                err(f"demos.json: {did} lists '{stack}', which is core -- the core "
                    f"is always up and must not be something a demo can take away")
            covered.add(stack)

    check_card_order_matches_tabs(demos)
    check_demos_fit_the_frame(demos)

    for stack in sorted(core):
        checks += 1
        if stack not in on_disk:
            err(f"demos.json: core wants stack '{stack}', which has no stacks/{stack}/stack.meta")
    for stack in sorted(on_disk - covered):
        checks += 1
        err(f"stacks/{stack}: in no demo and not core -- nobody can start it from "
            f"the console. Add it to demos.json, or to the core if it must always run")


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"

    if what in ("all", "projects"):
        projects = load_projects()
        check_json_parses()
        check_component_shape()
        check_resource_manifests()
        check_project_json_shape(projects)
        known = dict(projects)
        check_inheritance(projects, known)
        check_page_config(projects, known)
        check_popup_targets(projects, known)
        check_no_signatures()
        check_app_bar_hidden(projects)
        check_duplicated_runtime()
        check_themes(projects)
        check_http_timeouts()
        if projects:
            print(f"  projects: {', '.join(sorted(projects))}")

    if what in ("all", "stacks"):
        check_stacks()
        check_module_pins()
        metas = check_stack_meta()
        check_topology_names(metas)
        check_proxy_aliases(metas)
        check_stack_labels()
        check_demos()

    for w in warnings:
        print(f"  warn  {w}")
    for e in errors:
        print(f"  FAIL  {e}")
    for s in skips:
        print(f"  SKIP  {s}")

    line = f"\n{checks} checks, {len(errors)} error(s), {len(warnings)} warning(s)"
    if skips:
        # In the summary line, not only in the body: this is the number somebody
        # reads off a green CI run, and a green run that checked less than it
        # says it did is how a gate quietly stops being a gate.
        line += f", {len(skips)} NOT RUN"
    print(line)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
