# Architecture Builder — module notes and undo/redo

The `GatewayAdmin` project's `/architecture` page is a canvas for sketching a
customer's Ignition architecture live, in a browser, using the WARGoetz
Architecture Builder Perspective component.

Findings we wrote up for the module's author — reproducible on a stock
install, nothing gateway-specific — are in
[ARCH-BUILDER-FEEDBACK.md](ARCH-BUILDER-FEEDBACK.md).

## Which repo, and which module

The original link was
[`ia-tgoetz/ReactFlowPerspectiveModule`](https://github.com/ia-tgoetz/ReactFlowPerspectiveModule).
**The Architecture Builder is no longer in it** — that repo's README says it was
split out, and it now ships only Database Schema, Hierarchy Chart and JSON
Editor. The builder lives in
[`ia-tgoetz/ArchitectureBuilderReactFlow`](https://github.com/ia-tgoetz/ArchitectureBuilderReactFlow),
module id `com.wargoetz.archbuilder`, component id
`com.wargoetz.reactflow.architecturebuilder`.

Both are MIT, free modules (`freeModule=true`, no licence to activate), built
against 8.3.0, and **self-signed** — the gateway reports `selfSigned: true`,
which is fine but means the certificate prompt has to be accepted on install.

Versions in use are pinned in [`../scripts/upstream.pins`](../scripts/upstream.pins).

## Installing — through the API the UI uses, not the registry

`scripts/ign-modules.sh` writes `data/modules.json` directly. **Do not use it for
this.** Per the toolkit knowledge base, hand-registering a module "does NOT
register it with `ModuleManager` — the gateway ignores it completely: no start
attempt, no fault, no log line at any level", and it also drops the gateway into
COMMISSIONING, which 302-redirects every request (Perspective sessions included)
until a human finishes `/welcome`.

`ign-gw.js module-install` drives the same REST calls the Config → Modules page
makes, which installs live with no commissioning gate:

```bash
node scripts/ign-gw.js module-install --gateway local \
     --file modules/ignition/ArchitectureBuilder.modl
```

The sequence, read out of `ModulesPage.chunk.bundle.js` rather than guessed
(these endpoints are in a lazily-loaded chunk, not the importmap):

```text
POST /data/api/v1/modules/upload?fileName=<name>   raw file bytes, NOT multipart
     -> { moduleId, containsCert, certAccepted, containsEula, licenseAccepted }
POST /data/api/v1/modules/certificate?moduleId=<id>    if containsCert
POST /data/api/v1/modules/eula?moduleId=<id>           if containsEula
POST /data/api/v1/modules/install?moduleId=<id>
```

The upload body is binary, so it cannot go through the `page.evaluate` + `fetch`
helper the rest of `ign-gw.js` uses — a `.modl` is a zip and a string round-trip
corrupts it. Playwright's request context shares the browser's cookies and can
post the Buffer directly.

**A fresh install lands `INACTIVE` and needs one gateway restart to load.**
It installs cleanly, not quarantined, with `onStartup: enabled` and nothing in
the log -- it is simply the one module not yet `ACTIVE`. Its web assets 404
until it starts, so the component cannot render before that restart.

## Undo/redo — now native (v1.1.3, 04/08/2026)

**Upstream added it, so ours is gone.** v1.1.3 ships full canvas undo/redo:
`Ctrl+Z` / `Ctrl+Shift+Z` / `Ctrl+Y`, buttons in the canvas control bar, 50 steps,
and — the part our version could never do — **one action undoes as one step**,
covering deletes, moves, resizes, style and z-order changes, edge creation,
reconnection, labels and manual routing.

Removing ours was not optional housekeeping. The release notes state that the
component **resets its history whenever `nodes` or `edges` are written from
outside** (a script write, a Designer edit, a view reload) — which is precisely
what our restore did. Left in place, the two would have fought and ours would
have won by breaking theirs.

Verified after upgrading: two drops → 2 nodes, `Ctrl+Z` → 1 → 0,
`Ctrl+Shift+Z` → 1, and the control bar grew from 4 buttons to 6.

**A module upgrade needs a gateway restart.** The install lands and reports
`shouldUpgrade: true` while the old version keeps running; only a restart loads
the new one. Ask first — see HARD RULE 1.

The Perspective-side implementation we used until then is preserved in
[`share/architecture-undo-redo/`](share/architecture-undo-redo/) as an importable
view, for anyone on an older module build.

## Undo/redo against module v1.1.2 (kept for reference)

**The component has none.** Zero occurrences of undo, redo or history across all
20 source files in the upstream repo (checked at v1.1.2). So this has to be added.

The thing that makes it tractable: **`nodes` and `edges` are ordinary Perspective
props**, and the component writes back to them (that is what the README's
"Preview Mode required for write-backs" note is about). The canvas is just data,
sitting in the view, readable and writable by a Perspective script.

### Option A — history in Perspective, no fork — BUILT AND WORKING

Implemented on `/architecture`: dragging a gateway onto the canvas, undoing it
and redoing it goes 1 node -> 0 -> 1.

`view.custom.history` is a stack of `{key, nodes, edges}`; `view.custom.cursor`
points at the current entry. A property change script on the builder's
`props.nodes` records; the toolbar's Undo/Redo write the snapshot back.

Four things it has to get right, three of which bit during the build:

- **`self.props.nodes` is not a dict.** It is a Perspective property tree backed
  by Java, and `json.dumps()` raises on it. Use `system.util.jsonEncode` /
  `jsonDecode`, which understand Ignition's own types.
- **Seed the stack with the empty canvas.** Otherwise the first entry recorded is
  the state *after* the first edit, undo has nowhere to go, and it reports
  "nothing to undo" — indistinguishable from the change script never firing.
- **Guard the restore, but not with a flag.** Writing the props fires the change
  script again; a `restoring` boolean races because the change arrives after the
  flag is cleared. Compare the incoming state's key against the snapshot at the
  cursor and return when they match — idempotent, so the restore can fire the
  script as often as it likes.
- **Debounce the drag.** A drag emits a stream of writes; each one would be its
  own undo step. Writes within 500 ms replace the top of the stack, so one
  gesture is one step.

**Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z work on the canvas** (added 04/08/2026 — the
toolbar buttons were the only way before, which is not what anyone reaches for).
It is a plain `events.dom.onKeyDown` on the view's `content` container: keydown
bubbles up from wherever focus sits inside the view, so it fires whether the
canvas or a palette item has focus. Click once on the page after loading it so
focus is inside — a freshly loaded page has focus on the document, not the view.

Perspective does have a first-class mechanism for this — session `KEY_HANDLERS`
(`SystemEventsConfig`, with `controlEventModifier`, `preventDefault` and friends,
under the `session-scripts` resource). It was not used because the on-disk layout
of that resource could not be established from the jar with any confidence, and
a DOM handler is verifiable in a minute. If a global shortcut is ever wanted
across pages rather than on this one view, that is the thing to work out.

**Tabs must set `runWhileHidden: true`, or undo/redo looks broken.** With the
default `false`, leaving the Architecture tab UNMOUNTS its view; returning
remounts it fresh, so the canvas is blank and `view.custom.history` is back to
its seeded empty state. Undo then correctly reports "nothing to undo" about a
drawing you can no longer see — which reads as undo being broken rather than as
the canvas having been discarded. Drawing, undoing, switching to another tab
and back, then redoing restores the node.

**The canvas itself now survives a reload (07/08/2026).** `arch_store` keeps it
in a memory tag (`[default]Architecture/Canvas`); the view restores it in
`onStartup` and saves it from an `onChange` on both `props.nodes` and
`props.edges`. A memory tag rather than a session prop (which dies with the
session, the original problem) or a project resource (not writable at runtime
without rewriting a file on the gateway and rescanning — this repo does that
exactly once, for the style pack, because that one has to travel over EAM).

It is loaded and saved rather than **bound**, deliberately: a bound property
cannot be written, so binding the canvas would make the first drag fail. Same
shape the style switcher uses to dodge the identical trap.

`load()` returns `(None, None)` for "nothing stored", which the caller must be
able to tell from "stored, and empty" — writing `{}` back over a canvas someone
had just drawn would be the same bug as not persisting at all.

Verified: import two nodes → full reload → still two; clear → reload → none.

Still open: the **undo history** in `view.custom` is still per-session. Undo
across a reload would mean persisting the whole stack, which is a much bigger
object than the canvas for much less benefit.

### Option A as originally scoped

Add `view.custom.history` (a list) and `view.custom.cursor` (an int). Attach a
**property change script** to the builder's `props.nodes` — the mechanism is
`propConfig["props.nodes"].onChange.script`, which is proven in this stack and
in production elsewhere. On each change, push a `{nodes, edges}` snapshot; undo
writes `history[cursor-1]` back into the props.

- **Cost:** a few hours. No toolchain, no fork, survives module upgrades, and it
  is a project resource so it travels through git and EAM like everything else.
- **Granularity:** whole-canvas snapshots, not per-gesture. A drag emits many
  intermediate writes, so debounce (say 400 ms of quiet) or one undo step will
  be one pixel of movement.
- **The trap to design for:** restoring writes the props, which fires the change
  script again and would push the restored state as a new history entry, so undo
  immediately becomes a no-op loop. Guard with a `view.custom.restoring` flag
  that the change script checks first.
- **Unknown worth testing early:** whether writing `props.nodes` wholesale makes
  the component remount and lose viewport/selection. If it does, undo will feel
  jumpy even though it is correct.

### Option B — real undo/redo inside the component (fork)

React Flow's own state lives in the component, so a history stack there gives
per-gesture undo, `Ctrl+Z`/`Ctrl+Y`, and no prop round-trip. Cleaner UX by a wide
margin.

- **Cost:** Java 17 + Node + Gradle, and a self-signed rebuild to install. The
  real cost is ongoing: upstream cut **ten releases in the five weeks** to
  22/07/2026, so a fork means rebasing regularly or falling behind.

### Option C — upstream it

MIT licence, single active author, and undo/redo is the kind of feature that
belongs in the component rather than in every project that uses it. Worth an
issue before building Option B; the answer may simply be "yes, next release".

**Recommendation:** Option A to get something usable now, and Option C in
parallel. Only fork if the author says no and per-gesture undo genuinely
matters for the demo.

## Watching for upstream changes

`scripts/watch-upstream.sh` compares the live release tag against
`scripts/upstream.pins` and prints one line per new release.

```bash
scripts/watch-upstream.sh          # check once, non-zero exit on drift
scripts/watch-upstream.sh --watch  # poll (default 30 min), one line per new release
scripts/watch-upstream.sh --pin    # record current versions; commit the diff
```

The pins file is committed deliberately: it doubles as the record of which module
versions this stack was last verified against, so `git log` on it is the upgrade
history. `ReactFlowPerspectiveModule` publishes no releases at all (it commits
its `.modl` into `build/`), so it is tracked by last-pushed timestamp instead —
"no releases" must not read as "no news".

## Theming it (03/08/2026)

The component's internals carry **no class names**, so there is nothing for a style
class to attach to. It is still fully themeable, because it paints itself with
Ignition's own `--neutral-00…100` CSS custom properties — and custom properties
inherit, so redefining them on an ancestor makes its inline styles resolve to our
colours. No `!important`, no fork, and it survives the module changing its markup.

The overrides live in a project stylesheet resource:

```text
GatewayAdmin/com.inductiveautomation.perspective/stylesheet/stylesheet.css
```

They are **generated from the packs' own style-class files**, so the packs remain the
single source of truth and a new pack needs no CSS written by hand. Each block is
scoped by the class Perspective already emits on styled containers,
`psc-<pack>/containers/page` — matched as `[class*="psc-<pack>/"]`, because a CSS class
selector cannot contain the slash.

Two things the neutral scale does not cover:

- **React Flow's zoom/fit controls** ship their own class-based CSS and stayed white on
  every dark pack. They need their own `.react-flow__controls-button` rule.
- **The screenshot button's icon is stroke-drawn** with a hard-coded `#555`, while the
  other three are fill-drawn. Setting `fill` alone left it invisible; it needs `stroke`,
  scoped to that button so the filled icons do not gain an outline.

## The tab container trap that hid all of this

The Architecture tab rendered an **empty content frame** for a long time, and so did
Store & Forward. Not a module problem: `ia.container.tab` declares
`childPositionSchema = {tabIndex}` with `additionalProperties: false`, so a child
belongs to a tab by its POSITION, not by its order. The children had a flex position,
which is invalid there, so all three fell back to `tabIndex: 0`. There was no error in
the browser console, the gateway log or the page — the tab highlighted correctly and
`data-tab-index` changed; the component tree simply collapsed from 71 components to 2.


## Clear canvas and Export JSON had never worked

Found 07/08/2026, while testing canvas persistence — which was the first thing
that ever *asserted* the canvas had changed rather than looking at it.

The menu's script reached the builder with `self.getSibling('builder')`, and
they are not siblings: the menu lives in `header`, the builder is a child of
`content`, one level up. So `getSibling` returned `None` and every item died on

```text
AttributeError: 'NoneType' object has no attribute 'props'
```

The click registered and the menu closed, so it looked exactly like a component
that had nothing to do. The error **does** reach the gateway log — which is the
only reason it is findable at all, since nothing surfaces in the browser. Now
`self.parent.parent.getChild('builder')`.

Worth the general note: a Perspective script that walks the component tree by
`getSibling`/`getChild` fails at RUNTIME and only in the log, so a tree that is
reorganised later breaks the scripts silently. Assert the effect, not the click.
