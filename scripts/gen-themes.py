#!/usr/bin/env python3
"""Vendor the Gaskony themes into this repo -- gateway CSS and style classes.

WHY THIS EXISTS, AND WHY ITS OUTPUT IS COMMITTED.

This repo used to inherit its styling from `Styles_Template2`, a project built
from a PRIVATE upstream repo carried as the `styles-template` submodule and
unpacked from a published zip. That worked, and it cost a fresh clone the one
dependency it could not satisfy on its own: the outer clone succeeds, only the
submodule fails, and you are left with a checkout that looks complete and
deploys no projects. Nothing else in this repo needs anything off the network.

So the styling is now VENDORED, in two halves that come from the same packs:

  ignition/themes/<id>/          a gateway CONFIG resource -- the token layer.
                                 ~90 KB flattened, restyles every stock IA
                                 component through the ~136 CSS custom
                                 properties they all consume.
  ignition/projects/Themes/      an inheritable project carrying 69 style
                                 classes per theme -- the semantic layer a
                                 theme categorically cannot reach (status
                                 pills, KPI deltas, alarm-priority chips, nav
                                 states, card chrome).

THE CLASSES ARE EMITTED UNDER THE THEME ID, NOT THE PACK ID, and that one
decision is what made this a small change instead of a rewrite. `Site1`'s views
carry 417 bindings of the form

    {session.custom.style} + '/containers/card'

so as long as `session.custom.style` holds a name that is BOTH a theme id and a
style-class prefix, every one of them resolves exactly as it did before and not
a single view needed editing. `session.props.theme` is then bound to the same
value, so the token layer and the class layer can never disagree about which
look is on screen.

Verified before any of this was written, because each one could have killed it:

  * A theme dropped in before the gateway starts registers with NO config scan
    and NO restart-after-the-fact -- it served 200 the moment the gateway came
    up. (A theme added to a RUNNING gateway needs the Platform -> Overview
    "Scan File System", which has no REST route and no in-process API; that is
    why `ign-themes.sh` installs before the one restart bootstrap already does.)
  * `session.props.theme` is a plain writable string in the session-props
    schema inside perspective-common-3.3.8.jar -- not a system prop, so it
    takes a binding like any other.
  * EVERY source pack carries an IDENTICAL set of 69 class paths, so there is
    no gap to special-case. Re-checked on every run rather than assumed --
    a pack that grew a class would otherwise leave one theme unable to paint
    something every other theme paints.
  * NOT ONE of those 69 classes references `var()`. They are literal values, so
    they do not depend on the 4,346-line token stylesheet -- which is what let
    that stylesheet be deleted outright rather than trimmed.

REGENERATING NEEDS TWO UPSTREAM CHECKOUTS; USING THIS REPO NEEDS NEITHER.

    python3 scripts/gen-themes.py \
        --themes  <checkout of toolbox-theme-manager>        (the gateway themes)
        --classes <checkout of ignition-styles-template-v2>  (the style classes)

Two, because the themes were promoted into their own repo on 26/08/2026 and the
style classes stayed behind -- see the note above main(). Regenerating is a
maintenance action for when upstream corrects a colour, not something a clone
ever runs, which is the whole point of vendoring.
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEMES_OUT = os.path.join(REPO, "ignition", "themes")
CLASSES_OUT = os.path.join(
    REPO, "ignition", "projects", "Themes",
    "com.inductiveautomation.perspective", "style-classes")

# Every style class resource has the same resource.json. Written rather than
# copied so the output cannot inherit a `lastModificationSignature` from
# upstream's tree -- the gateway computes that over the resource's own bytes,
# and a stale one makes the scan skip the resource with no error at all.
CLASS_RESOURCE = {
    "scope": "G",
    "version": 1,
    "restricted": False,
    "overridable": True,
    "files": ["style.json"],
    "attributes": {},
}


# --------------------------------------------------------------------------
# Themes this repo builds for itself: NONE, as of themes-1.1.0
# --------------------------------------------------------------------------
#
# There used to be an EXTRA list and a build_extra() here. This repo wanted a
# dark counterpart to Finance Ledger, upstream had the newsprint-night PACK but
# had not built it into a THEME, so build_extra imported upstream's own
# build_theme.py and redirected its OUT_DIR at a temp directory -- byte-for-byte
# what upstream would have produced, without writing to the reference checkout
# and without a second implementation of ~700 lines of token mapping to drift.
#
# themes-1.1.0 ships newsprint-dark officially, with an oxblood accent this
# repo's build did not have (the pack's own accent.primary is the same warm
# off-white as its body text, so the local build rendered monochrome and
# --callToAction gave no separation between an action and its surface at all).
# So the whole path is gone: every theme now comes from upstream's out/.
#
# If a pack ever needs building here again, `git log -S build_extra` has it.
# The trick worth remembering is the one line that made it safe: set
# mod.OUT_DIR AFTER exec_module, so the module's own relative paths still
# resolve to upstream's packs and only the output moves.

# --------------------------------------------------------------------------
# The themes Perspective already ships
# --------------------------------------------------------------------------
#
# `light` and `dark` live inside the Perspective module jar; the four variants
# sit under the gateway's own config resources. All six are RESERVED -- we never
# install them, never overwrite them, and they cost this repo nothing.
#
# They are in the picker because the comparison is worth showing: the same
# screens under Ignition's own look, beside what the packs do with it. Picking
# one is genuinely different from picking ours, and the picker says so -- a
# built-in themes the STOCK COMPONENTS ONLY. The views bind style classes as
# `{session.custom.style} + '/containers/card'`, which for `light` resolves to
# `psc-light/containers/card`, a class no project defines; an unmatched class
# is not an error, it simply does not paint. So cards lose their chrome and
# alarm rows lose their severity colour, which IS the comparison rather than a
# fault -- but it has to be said out loud, or it reads as a broken theme.
#
# The swatch colours are IA's real values, resolved from a running 8.3.8 gateway
# (its --container / --label / --callToAction, following the var() indirection
# through --neutral-*). Written down rather than fetched, because a generator
# that needs a running gateway is a generator that cannot run on a fresh clone.
# Re-derive on an Ignition version bump:
#     curl -s <gw>/data/perspective/themes/<id>.css
BUILTIN = [
    # id, label, dark, swatch(bg, fg, accent)
    ("light",      "Ignition Light",      False, ("#F4F4F4", "#323232", "#0C7BB3")),
    ("dark",       "Ignition Dark",       True,  ("#323232", "#F4F4F4", "#229AD6")),
    ("light-cool", "Ignition Light Cool", False, ("#DDE1E6", "#21272A", "#0C7BB3")),
    ("light-warm", "Ignition Light Warm", False, ("#E5E0DF", "#272525", "#0C7BB3")),
    ("dark-cool",  "Ignition Dark Cool",  True,  ("#21272A", "#DDE1E6", "#229AD6")),
    ("dark-warm",  "Ignition Dark Warm",  True,  ("#272525", "#E5E0DF", "#229AD6")),
]


def builtin_entries():
    return [{"id": i, "label": l, "dark": d, "source_pack": None,
             "builtin": True, "swatch": list(sw)}
            for i, l, d, sw in BUILTIN]


# --------------------------------------------------------------------------
# The order the picker draws them in, which IS the layout
# --------------------------------------------------------------------------
#
# The switcher wraps at two across, so there is no grid to place a swatch into
# -- the list order decides which two themes share a row. Drawn in build order
# the pairs interleaved: Leather Dark did land beside Leather Light, but
# Finance Ledger sat beside Nord Dark and Nord Light beside Industrial Dark, so
# comparing a theme with its own counterpart meant crossing a row boundary for
# most of them. Nothing was wrong; it simply read as ten unrelated themes
# rather than five pairs.
#
# UPSTREAM DECLARES THE FAMILY, so this does not have to guess it. themes.json
# carries `family` and `pair` per entry (added upstream a1b64d2, after this
# repo asked for it) -- and a declared family beats a derived one for the two
# cases derivation cannot reach:
#
#   glass-violet / glass-green share no light/dark token at all -- they are two
#       hues of one family, and upstream says so (family "glass", pair null).
#       Derived from the ids they would be two families of one and drift apart
#       as the real pairs claimed their rows.
#   newsprint-dark is Finance Ledger's dark counterpart by intent, not by
#       spelling -- different packs upstream, no shared stem. Upstream declares
#       both as family "finance" (and pairs them both ways) since
#       themes-1.1.0, so nothing here has to know it.
#
# The derivation below survives only for the built-ins, which are Perspective's
# and carry no metadata: `light-cool`/`dark-cool` reduce to the stem `cool`.
#
# A theme upstream adds later and cannot be paired keeps its build position and
# fills whatever half-row is going -- no die, no warning, because an unpaired
# theme is a perfectly good theme.


def pair_order(themes):
    """Sort so counterparts share a row -- light on the left, dark on the right.

    Grouping is by first appearance, not alphabetical: upstream's own order is
    a considered one, and re-sorting it would be this repo overriding an
    editorial decision for no reason. Only the members of a group move.
    """
    def family(t):
        # Built-in and custom ids live in one list here and are only split
        # apart when the switcher is drawn; keying them separately stops a
        # future custom `cool` pack from being grouped with Ignition Dark Cool.
        scope = "builtin:" if t.get("builtin") else "custom:"
        if t.get("family"):
            return scope + t["family"]
        stem = "-".join(p for p in t["id"].split("-") if p not in ("light", "dark"))
        return scope + stem

    groups, seq = {}, []
    for t in themes:
        k = family(t)
        if k not in groups:
            groups[k] = []
            seq.append(k)
        groups[k].append(t)

    out = []
    for k in seq:
        # `sorted` is stable, so a group whose members are all the same
        # brightness keeps its build order rather than being shuffled.
        out.extend(sorted(groups[k], key=lambda t: bool(t["dark"])))
    return out


# --------------------------------------------------------------------------
# The charts
# --------------------------------------------------------------------------
#
# A STOCK PERSPECTIVE CHART DOES NOT READ STYLE CLASSES FOR ITS SERIES COLOURS,
# and it cannot read a CSS variable either -- amCharts takes literal values off
# props. So every colour-bearing leaf carries a `map` transform keyed on
# {session.custom.style}, and those maps are the one place a theme's colours
# have to be written out rather than referenced.
#
# WHICH IS EXACTLY WHY THEY GO STALE, AND HAD. The maps were still keyed on v1
# pack ids (`glass-aurora`, `isa-101-high-performance`) after the 21/08/2026 v2
# cutover moved session.custom.style to `aurora-violet` -- a value in none of
# them. Every chart on the dashboard had been drawing its FALLBACK for four days
# by the time this was written: a generic #2563eb blue where the pack's accent
# should be. Nothing errored, nothing logged, and the charts looked fine unless
# you knew what colour to expect.
#
# So they are generated now, from the themes' own variables, and regenerating is
# part of the same command that builds everything else.
ROLE_VAR = {
    "props.arc.color":                              "--callToAction",
    "props.arcBackground.color":                    "--neutral-30",
    "props.color.selected":                         "--callToAction",
    "props.color.unselected":                       "--neutral-40",
    "props.label.color":                            "--label",
    "props.legend.labels.font.color":               "--label--disabled",
    "props.series[0].line.appearance.fill.color":   "--qual-1",
    "props.series[0].line.appearance.stroke.color": "--qual-1",
    "props.series[1].line.appearance.stroke.color": "--qual-2",
    "props.xAxes[0].appearance.grid.color":         "--containerBorder",
    "props.xAxes[0].appearance.labels.color":       "--label--disabled",
    "props.yAxes[0].appearance.grid.color":         "--containerBorder",
    "props.yAxes[0].appearance.labels.color":       "--label--disabled",
}


def theme_vars(tid):
    """Every --custom-property a theme declares, as a dict."""
    css = open(os.path.join(THEMES_OUT, tid, "variables.css")).read()
    return dict((k, v.strip()) for k, v in
                re.findall(r"^\s*(--[a-zA-Z0-9-]+):\s*([^;]+?);", css, re.M))


def vars_for(t):
    """The variables a theme offers the chart maps.

    A built-in has no file here to read -- it lives in the Perspective module --
    so its three swatch colours stand in for the handful of roles the charts
    need. Not a full palette, and it does not have to be: the point of a
    built-in in this list is the stock look, and a chart drawn in IA's own
    accent is exactly that. What matters is that it is a REAL value rather than
    the map's fallback, which is the failure this whole generator exists to
    stop.
    """
    if not t.get("builtin"):
        return theme_vars(t["id"])
    bg, fg, accent = t["swatch"]
    return {"--callToAction": accent, "--label": fg, "--label--disabled": fg,
            "--container": bg, "--containerBorder": fg, "--containerNested": bg,
            "--neutral-30": bg, "--neutral-40": fg,
            "--qual-1": accent, "--qual-2": fg}


def regen_maps(themes):
    """Rewrite every map transform in every view against the vendored themes.

    A map whose target is not a known colour role (there are two -- a per-pack
    `props.style.gap` and a `props.style.width` on the demo dashboard) is
    rebuilt with each theme pointing at the map's OWN fallback, so no dead pack
    id survives anywhere and the rendered result is unchanged.
    """
    values = dict((t["id"], vars_for(t)) for t in themes)
    ids = [t["id"] for t in themes]
    touched, rebuilt, unmapped = 0, 0, set()

    for path in sorted(glob.glob(os.path.join(
            REPO, "ignition", "projects", "*",
            "com.inductiveautomation.perspective", "views", "**", "view.json"),
            recursive=True)):
        doc = json.load(open(path))
        n = [0]

        def walk(node, role=None):
            if isinstance(node, dict):
                if node.get("type") == "map" and role:
                    cfg = node.get("config", node)
                    var = ROLE_VAR.get(role)
                    if var is None:
                        unmapped.add(role)
                        out = dict((i, cfg.get("fallback")) for i in ids)
                    else:
                        out = {}
                        for i in ids:
                            v = values[i].get(var)
                            if v is None:
                                die("theme '%s' has no %s" % (i, var))
                            out[i] = v
                    cfg["mappings"] = [{"input": i, "output": out[i]} for i in ids]
                    n[0] += 1
                for k, v in node.items():
                    walk(v, k if k.startswith("props.") else role)
            elif isinstance(node, list):
                for v in node:
                    walk(v, role)

        walk(doc)
        if n[0]:
            with open(path, "w") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.write("\n")
            touched += 1
            rebuilt += n[0]

    if unmapped:
        print("  (non-colour maps held at their fallback: %s)"
              % ", ".join(sorted(unmapped)))
    print("  %d map transforms rebuilt across %d views" % (rebuilt, touched))


# --------------------------------------------------------------------------
# The picker
# --------------------------------------------------------------------------
#
# GENERATED, for the reason the last picker was DELETED. This repo used to draw
# its own grid of every pack, and on 06/08/2026 one surface offered 19 while two
# others offered a stale 16 -- a pack that is installed, inherited and rendering
# perfectly but reachable from nowhere a user can click is indistinguishable
# from a pack that was never added. The answer then was to stop enumerating and
# open the template's own switcher; with the template gone that is not
# available, so the enumeration comes back -- but written by the same command
# that writes the themes, from the same list, and cross-checked by validate.
#
# Each row previews the theme it selects, using that theme's OWN surface, ink
# and accent read out of its variables.css. A list of names in the current
# theme's colours tells you nothing about what you are choosing.
SWITCHER = ("Appearance", "Switcher")


def _btn(name, text, on_click, style, classes=None):
    c = {
        "events": {"component": {"onActionPerformed": {
            "config": {"script": on_click}, "scope": "G", "type": "script"}}},
        "meta": {"name": name},
        "position": {"shrink": 0},
        "props": {"text": text, "style": style},
        "type": "ia.input.button",
    }
    if classes:
        c["propConfig"] = {"props.style.classes": {
            "binding": {"config": {"expression": classes}, "type": "expr"}}}
    return c


def gen_switcher(themes):
    """Write Themes/Appearance/Switcher -- one row per theme, each previewing itself.

    EVERY ROW IS AN ia.input.button, and that is not a style choice.
    `ia.container.flex` declares NO COMPONENT events -- checked in
    ia.components.json, where its `events` is null against the button's
    `onActionPerformed`, and confirmed independently by the Web Designer
    session, which found `events` null for EVERY container type in
    perspective-common-3.3.8.jar. So a COMPONENT-event handler written onto a
    container is accepted by the file format, deploys cleanly, renders a row
    that highlights under the cursor, and does nothing whatsoever when clicked,
    with nothing in the browser console and nothing in the gateway log. Exactly
    the shape of the scope-"C" trap in CLAUDE.md, one level along: the click is
    not ignored, it was never subscribed to.

    The trap is narrower than it first looks, and the distinction is the whole
    of it: a container's `events.dom.onClick` DOES fire (live-verified on
    8.3.8 by the styles-template session). It is only the component events that
    are absent. Buttons stay, because they are the honest component for a thing
    that is clicked -- they take focus, answer the keyboard and appear in the
    accessibility tree, which a clickable div does not -- but if a future row
    needs to be a container, `dom.onClick` is the way to make it work.

    So the swatch is the button's own left border in the theme's accent, and its
    surface and ink are the theme's -- a real preview of the thing you are
    choosing, in a component that can actually be clicked.

    HOW THE POPUP IS SIZED, AND THE ARGUMENT THAT DOES NOT EXIST
    ------------------------------------------------------------
    `props.defaultSize` on THIS view, 560x460, and nothing else. It renders
    560x492: the shell adds a 32px title bar on top of the content height, so
    the number to fit against a viewport is defaultSize + 32.

    **openPopup HAS NO `width` OR `height` ARGUMENT.** Read the constant pool
    of PerspectiveScriptingFunctions in perspective-gateway-3.3.8.jar and it
    knows `position`, `modal`, `resizable`, `draggable`, `overlayDismiss`,
    `viewportBound`, `showCloseIcon`, `title`, `type`, `params` and `id` --
    and neither `width` nor `height`. PopupPayload carries the same set.
    **Unknown kwargs are swallowed without a word**, so
    `height='min(460px, 88vh)'` at eight call sites read exactly like a
    working fix. It was not one: the popup measured 560x492 with those
    arguments and 560x492 with them deleted. Corrected by the styles-template
    session 25/08/2026, who read the signature; confirmed here by reflection
    and by deleting them and measuring.

    The tell was in the first measurement and went unread: the popup asked for
    460 and rendered 492. A number that disagrees with what you asked for is
    the answer, not a rounding error.

    A per-call override does exist and is `position={'width': N, 'height': N}`
    -- INTEGERS, PIXELS, no CSS strings, so there is no viewport-relative form
    of any kind, which is why the min()/vh spelling was invented in the first
    place. `demo_guide.popup_size()` already used the right form, three files
    away, because a guide's height varies by topic. This size is constant, so
    the view owns it and no call site repeats a number.

    THE FLOOR IS ~510px OF VIEWPORT HEIGHT, and `viewportBound=True` does not
    move it -- that flag does not resize the popup. Measured 25/08/2026 at
    1440x420: drawn from y=-36, title bar off the top of the screen, bottom
    control at y=440 and unreachable. At 1440x530 -- the laptop this was
    reported from -- it is drawn at y=19 and the bottom control ends at 495.
    The ROWS scroll inside the popup, so content is never the constraint; the
    popup's own box is.
    """
    rows, builtins = [], []
    for t in themes:
        if t.get("builtin"):
            bg, fg, accent = t["swatch"]
            builtins.append(_btn(
                t["id"],
                "%s  \u00b7  %s" % (t["label"], "dark" if t["dark"] else "light"),
                "\tself.session.custom.styleOverride = '%s'\n" % t["id"],
                {"backgroundColor": bg, "border": "1px solid " + accent,
                 "borderLeft": "6px solid " + accent, "borderRadius": "4px",
                 "color": fg, "cursor": "pointer", "fontSize": "13px",
                 "fontWeight": "600", "height": "40px", "minHeight": "40px",
                 "padding": "0 14px"}))
            continue
        v = theme_vars(t["id"])
        # A write, not a commit. session.custom.style is BOUND (it prefers
        # styleOverride, else the committed theme, on a 5s poll) and a bound
        # property silently swallows a write -- so this sets the override and
        # the binding picks it up. Committing to every project, which is the
        # only form that travels over EAM, is the separate `Apply everywhere`
        # button the header grows the moment a preview is uncommitted.
        rows.append(_btn(
            t["id"],
            "%s  \u00b7  %s" % (t["label"], "dark" if t["dark"] else "light"),
            "\tself.session.custom.styleOverride = '%s'\n" % t["id"],
            {
                "backgroundColor": v["--container"],
                "border": "1px solid " + v["--containerBorder"],
                "borderLeft": "6px solid " + v["--callToAction"],
                "borderRadius": v.get("--borderRadius", "6px"),
                "color": v["--label"],
                "cursor": "pointer",
                "fontSize": "13px",
                "fontWeight": "600",
                "height": "40px",
                "minHeight": "40px",
                "padding": "0 14px",
            }))

    def section(name, title, kids):
        """A labelled band of swatches, two across.

        `wrap` is a PROP on ia.container.flex, not a style key -- written as
        style it is silently ignored and every swatch ends up on one row. Same
        for alignItems and justify; CLAUDE.md records the measurement.

        Two across comes from a 48% basis, NOT calc(). elementPosition.basis is
        a `css-length` in the schema (perspective-common's css-length.schema.json)
        and calc()/min()/vw are all invalid there -- they render anyway, because
        the gateway does not re-validate a view written externally, and only the
        Designer ever complains. A plain percentage is valid and degrades to one
        column on a narrow window instead of cramping.
        """
        return [
            {"meta": {"name": name + "Label"}, "position": {"shrink": 0},
             "propConfig": {"props.style.classes": {"binding": {"config": {
                 "expression": "{session.custom.style} + '/text/muted'"},
                 "type": "expr"}}},
             "props": {"style": {"fontSize": "10px", "fontWeight": "700",
                                 "letterSpacing": "0.08em", "lineHeight": 1.4,
                                 "textTransform": "uppercase"},
                       "text": title},
             "type": "ia.display.label"},
            {"children": kids, "meta": {"name": name},
             "position": {"shrink": 0},
             "props": {"direction": "row", "wrap": "wrap",
                       "style": {"gap": "6px", "width": "100%"}},
             "type": "ia.container.flex"},
        ]

    # grow 0, NOT 1. With grow the last swatch of an odd-numbered section
    # stretches to fill its row on its own -- drawn double-width, which reads
    # as a category rather than as the last of a list. A fixed 48% keeps every
    # swatch the same size and leaves the odd one in the left column where it
    # belongs. Ten themes is even today; the count is upstream's to change.
    for c in rows + builtins:
        c["position"] = {"basis": "48%", "grow": 0, "shrink": 1}

    body = []
    body += section("custom", "Custom \u00b7 %d themes" % len(rows), rows)
    body += section("stock", "Ignition \u00b7 stock components only", builtins)

    root = {
        "children": [
            {"meta": {"name": "hint"}, "position": {"shrink": 0},
             "propConfig": {"props.style.classes": {"binding": {"config": {
                 "expression": "{session.custom.style} + '/text/muted'"},
                 "type": "expr"}}},
             "props": {"style": {"fontSize": "11px", "lineHeight": 1.4},
                       "text": "Previews in THIS session. Use 'Apply "
                               "everywhere' to send it to the edges."},
             "type": "ia.display.label"},
            {"children": body, "meta": {"name": "rows"},
             "position": {"basis": "0px", "grow": 1, "shrink": 1},
             "props": {"direction": "column",
                       "style": {"gap": "8px", "minHeight": "0",
                                 "overflow": "auto", "width": "100%"}},
             "type": "ia.container.flex"},
            _btn("clear", "Back to committed",
                 "\tself.session.custom.styleOverride = ''\n",
                 {"fontSize": "12px", "height": "30px", "minHeight": "30px"},
                 classes="{session.custom.style} + '/buttons/ghost'"),
        ],
        "meta": {"name": "root"},
        "props": {"direction": "column",
                  # height:100% HERE, and minHeight:100% would be wrong -- the
                  # exact opposite of the rule for a PAGE root, so the
                  # distinction is worth stating.
                  #
                  # A page root lives in a pane that scrolls, so clamping it to
                  # the pane leaves nothing to scroll to. A POPUP root is a
                  # bounded box that owns its own scroller: the band below has
                  # flex 1 1 0 + overflow:auto, and that only bounds it if the
                  # root's height is fixed. With minHeight:100% the root grows
                  # with its content instead, the band never scrolls, and the
                  # bottom of the popup -- "Back to committed" -- is pushed off
                  # the screen on any laptop. minHeight:0 as well, or the flex
                  # child refuses to shrink below its content.
                  "style": {"gap": "10px", "height": "100%", "minHeight": "0",
                            "padding": "16px", "width": "100%"}},
        "type": "ia.container.flex",
    }

    d = os.path.join(REPO, "ignition", "projects", "Themes",
                     "com.inductiveautomation.perspective", "views", *SWITCHER)
    write_json(os.path.join(d, "view.json"), {
        "custom": {}, "params": {},
        "props": {"defaultSize": {"height": 460, "width": 560}},
        "root": root,
    })
    write_json(os.path.join(d, "resource.json"), {
        "scope": "G", "version": 1, "restricted": False, "overridable": True,
        "files": ["view.json"], "attributes": {},
    })
    print("  picker: %s -- %d custom + %d stock, two across"
          % ("/".join(SWITCHER), len(rows), len(builtins)))


def gen_lib_themes(themes):
    """Rewrite the generated THEMES block in every demo_styles copy.

    The list is written down in Jython because a gateway script cannot see this
    repo, and it is written in THREE places because inheritance is single-parent
    and the three consumers are siblings. Three copies of a hand-maintained list
    is three chances to drift, which is why validate compares them against
    themes.json -- and why this writes them rather than a person.

    Built-ins are in the list too: is_valid() has to accept `light`, or picking
    Ignition Light would be rejected by the very function that guards the theme.
    """
    rows = "".join('    ("%s", "%s"),\n' % (t["id"], t["label"]) for t in themes)
    begin = "# --- BEGIN GENERATED THEMES ---\nTHEMES = [\n"
    end = "]\n# --- END GENERATED THEMES ---"
    n = 0
    for name in sorted(os.listdir(os.path.join(REPO, "ignition", "projects"))):
        lib = os.path.join(REPO, "ignition", "projects", name, "ignition",
                           "script-python", "demo_styles", "code.py")
        if not os.path.isfile(lib):
            continue
        src = open(lib).read()
        i = src.find(begin)
        j = src.find(end, i)
        if i < 0 or j < 0:
            die("%s/demo_styles: no generated THEMES block to rewrite" % name)
        src = src[:i + len(begin)] + rows + src[j:]
        open(lib, "w").write(src)
        n += 1
    print("  demo_styles.THEMES rewritten in %d project(s)" % n)


def die(msg):
    sys.stderr.write("gen-themes: %s\n" % msg)
    raise SystemExit(2)


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# TWO UPSTREAM REPOS, since 26/08/2026
# --------------------------------------------------------------------------
#
# The themes were promoted out of ignition-styles-template-v2 into their own
# repo, `ignition-themes`. They were `experiments/themes/` there; they are the
# whole of the new repo, with `out/`, `build_theme.py` and `packs/` at its root.
#
# But THE STYLE CLASSES DID NOT MOVE. They are still Styles_Template2's, in
# styles-v2, and this repo needs both halves in one pass -- the token layer from
# ignition-themes and the semantic layer from styles-v2. Hence two flags where
# there was one.
#
# The colour source of truth is unchanged: ignition-themes vendors its ten packs
# FROM styles-v2 and re-pulls them with its own tools/sync-packs.sh, so a colour
# still originates in exactly one place.
#
# The tell that you have pointed --themes at the old repo is a clean-looking
# `die` about a missing out/themes.json, which is why the message names both
# layouts rather than just the path it wanted.
THEMES_LAYOUTS = [
    ("tools", "themes", "out"),          # toolbox-theme-manager (current)
    ("out",),                            # ignition-themes (archived 09/10/2026)
    ("experiments", "themes", "out"),    # styles-v2 before the split
]


def find_themes_out(root):
    """The built themes, in whichever of the two layouts this checkout uses."""
    for parts in THEMES_LAYOUTS:
        cand = os.path.join(root, *parts)
        if os.path.isfile(os.path.join(cand, "themes.json")):
            return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--themes", required=True, metavar="DIR",
                    help="a checkout of toolbox-theme-manager, or of the archived "
                         "ignition-themes (tag v1.1.0 or later)")
    ap.add_argument("--classes", required=True, metavar="DIR",
                    help="a checkout of ignition-styles-template-v2 "
                         "(the style classes did not move)")
    args = ap.parse_args()

    themes_root = os.path.abspath(args.themes)
    classes_root = os.path.abspath(args.classes)

    themes_src = find_themes_out(themes_root)
    if themes_src is None:
        die("no built themes under %s\n"
            "     Looked for tools/themes/out/, out/ and experiments/themes/out/themes.json.\n"
            "     Since 09/10/2026 the themes are built in:\n"
            "         https://github.com/Gaskony-Ignition/toolbox-theme-manager\n"
            "     --themes wants that one; --classes still wants "
            "ignition-styles-template-v2." % themes_root)

    themes = json.load(open(os.path.join(themes_src, "themes.json")))

    # Ours first, Ignition's after -- the order the picker draws them in --
    # and then paired, because at two across the order IS the layout.
    themes = pair_order(themes + builtin_entries())

    # Read the classes straight out of the CHECKOUT, not from a zip unpacked
    # into this repo. Upstream carries the built tree as well as the published
    # zip, and taking the tree is what lets this repo drop the submodule and
    # `.styles-build` entirely -- there is nothing left here that has to be
    # produced before anything else can run.
    packs_src = os.path.join(classes_root, "Styles_Template2",
                             "com.inductiveautomation.perspective", "style-classes")
    if not os.path.isdir(packs_src):
        die("no %s\n"
            "     --classes wants a checkout of ignition-styles-template-v2. The\n"
            "     style classes stayed there when the themes moved out." % packs_src)

    # Wipe both outputs first. A rename upstream must never leave a stale
    # directory under an old id sitting beside the new one -- it would still be
    # inherited, still render, and be reachable from nothing.
    for d in (THEMES_OUT, CLASSES_OUT):
        if os.path.isdir(d):
            shutil.rmtree(d)

    paths_seen = None
    for t in themes:
        if t.get("builtin"):
            # Shipped by Perspective: nothing to copy, nothing to generate, and
            # ign-themes.sh refuses these names outright.
            continue
        tid, pack = t["id"], t["source_pack"]

        # --- the token layer: the gateway theme ---------------------------
        tsrc = os.path.join(themes_src, tid)
        if not os.path.isdir(tsrc):
            die("theme '%s' is in themes.json but has no directory in out/ -- "
                "is --themes a complete checkout?" % tid)
        shutil.copytree(tsrc, os.path.join(THEMES_OUT, tid))

        # --- the semantic layer: 69 style classes, RENAMED to the theme id -
        psrc = os.path.join(packs_src, pack)
        if not os.path.isdir(psrc):
            die("pack '%s' (source of theme '%s') is not in the built template"
                % (pack, tid))

        paths = []
        for root, _dirs, files in os.walk(psrc):
            if "style.json" not in files:
                continue
            rel = os.path.relpath(root, psrc)
            paths.append(rel)
            dst = os.path.join(CLASSES_OUT, tid, rel)
            os.makedirs(dst, exist_ok=True)
            shutil.copyfile(os.path.join(root, "style.json"),
                            os.path.join(dst, "style.json"))
            write_json(os.path.join(dst, "resource.json"), CLASS_RESOURCE)

        paths = sorted(paths)
        # Every pack must carry the same contract. A pack missing a class is a
        # theme whose cards or alarm chips silently fall back to unstyled, and
        # the only place it shows is the one screen nobody opened.
        if paths_seen is None:
            paths_seen = paths
        elif paths != paths_seen:
            missing = sorted(set(paths_seen) - set(paths))
            extra = sorted(set(paths) - set(paths_seen))
            die("pack '%s' does not match the class contract: missing %s, extra %s"
                % (pack, missing, extra))

        print("  %-17s <- %-24s %d classes" % (tid, pack, len(paths)))

    # The index the picker and `make validate` both read, so neither has a list
    # of theme names written down in it that can fall behind this one.
    write_json(os.path.join(THEMES_OUT, "themes.json"), themes)

    ours = [t for t in themes if not t.get("builtin")]
    print("\n  %d vendored themes, %d classes each, %d total"
          % (len(ours), len(paths_seen), len(ours) * len(paths_seen)))
    print("  + %d shipped by Perspective (stock components, no classes)"
          % (len(themes) - len(ours)))
    gen_switcher(themes)
    gen_lib_themes(themes)
    regen_maps(themes)


if __name__ == "__main__":
    main()
