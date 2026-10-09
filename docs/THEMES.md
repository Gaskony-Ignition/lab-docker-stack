# Themes — the styling, and the seam that makes the demo work

Read this before touching anything styling-related. It supersedes
`STYLES-TEMPLATE.md`, which described a submodule and a template project that no
longer exist.

## The one-paragraph version

Styling is **vendored**, generated from upstream's built output by
`scripts/gen-themes.py`, and it comes in two halves that reach a gateway by two
different roads. The *paint* is a gateway **theme** — platform config, installed
by `scripts/ign-themes.sh`, which cannot travel over EAM. The *choice* is
`demo_styles.CHOSEN_PACK` — a project **resource**, which is the only form EAM's
Send Project can carry to an edge. That split is not a workaround; it is how a
real site would run it, and demonstrating it is half the point.

## What is where

```text
ignition/themes/<id>/           GATEWAY CONFIG RESOURCE -- the token layer
  config.json                   ~90 KB flattened per theme. Repaints every
  variables.css                 stock IA component through the ~136 CSS custom
  globals.css                   properties they all consume. Installed per
  index.css                     gateway. NEVER travels over EAM.
  resource.json
ignition/themes/themes.json     the index: id, label, dark, family, pair,
                                source_pack -- upstream's metadata, carried
                                through verbatim

ignition/projects/Themes/       INHERITABLE PROJECT -- the semantic layer
  .../style-classes/<id>/...    69 classes per theme, 690 in all: status pills,
                                KPI deltas, alarm-priority chips, nav states,
                                card chrome. A theme categorically cannot
                                reach these. Travels over EAM.
  .../views/Appearance/Switcher the picker
```

`Themes` is the parent of `GatewayAdmin`, `Site1` and `Site2`. It carries **no
stylesheet and no script libraries** — deliberately, see below.

## The classes are named after the THEME, not the pack

This is the decision that made the cutover small. `Site1`'s views carry 417
bindings of the form:

```text
{session.custom.style} + '/containers/card'
```

So as long as `session.custom.style` holds a name that is **both** a theme id
and a style-class prefix, every one of those resolves exactly as before and not
one view needed editing. `session.props.theme` is bound to the same value, so
the token layer and the class layer cannot disagree about what is on screen.

## Regenerating

A maintenance action for when upstream changes something — **not** something a
clone ever runs. A clone has everything already.

```bash
python3 scripts/gen-themes.py \
  --themes  <checkout of toolbox-theme-manager> \
  --classes <checkout of ignition-styles-template-v2>
make validate
make deploy PROJECT=Themes     # the style classes
make themes                    # the gateway themes
```

### Where the themes are built

Since 09/10/2026 the ten themes are built in `Gaskony-Ignition/toolbox-theme-manager`
(`tools/themes/`); `ignition-themes` is archived. `--themes` takes either
checkout.

### Two repos, and only the themes moved (26/08/2026)

The themes were promoted out of `ignition-styles-template-v2` into their own
repo, `Gaskony-Ignition/ignition-themes` — they were `experiments/themes/` and
are now the whole of it, with `out/`, `build_theme.py` and `packs/` at the
root. **The style classes did not move**: they are still Styles_Template2's, in
styles-v2. So this repo needs both checkouts in one pass — the token layer from
one and the semantic layer from the other — which is why there are two flags
where there was one.

A colour still originates in exactly one place: `ignition-themes` vendors its
ten packs *from* styles-v2 and re-pulls them with its own
`tools/sync-packs.sh`.

`gen-themes.py` accepts either layout for `--themes` (`out/themes.json` or the
old `experiments/themes/out/themes.json`), so an older checkout still works;
point it at the wrong repo entirely and it names both repos rather than just
the path it wanted.

**Build from a release tag**, not from somebody's working tree, and get the tag
without writing to a reference workspace:

```bash
git -C <ignition-themes> archive v1.1.0        | tar -x -C /tmp/themes-1.1.0
git -C <styles-v2>       archive themes-1.1.0  | tar -x -C /tmp/styles-v2
python3 scripts/gen-themes.py --themes /tmp/themes-1.1.0 --classes /tmp/styles-v2
```

`gen-themes.py` writes five things from those sources, which is what keeps
them from drifting apart:

1. `ignition/themes/` — the ten themes, copied from upstream's `out/`
2. `ignition/projects/Themes/.../style-classes/` — 690 classes, renamed pack → theme
3. `Themes/Appearance/Switcher` — the picker, one row per theme
4. the `# --- BEGIN GENERATED THEMES ---` block in all three `demo_styles/code.py`
5. the chart colour maps — 99 map transforms across three views

**Never hand-edit `ignition/themes/` or `ignition/projects/Themes/`.** Both
output directories are deleted and rewritten on every run, so an edit is gone
without warning and without an error.

## Installing, and the two ways it lies to you

```bash
make themes                    # every running gateway
make themes GATEWAY=ignition   # one
```

**A theme present when the gateway STARTS registers with no scan.** A theme
added or changed on a *running* gateway needs Platform → Overview **"Scan File
System"** — the config scan, a different button from the project scan, with **no
REST route and no in-process API**. There is no `system.*` call to wrap in a
timer the way `Ops/AutoScan` wraps `requestScan()`, so the only lever from a
script is a restart. `bootstrap` sidesteps the whole problem by installing
themes *before* the one restart it already performs for `Ops`.

Two failures this script had, both of the same family — **a check that proves
the wrong thing**:

- **Probing one theme.** It checked the first theme alphabetically and reported
  "installed and serving". Useless for the case that matters most — adding a
  *new* theme to a gateway that already has the others: the old one answers 200,
  the script says fine, and the new one is a 404 nobody looked for.
- **Probing for 200 at all.** Updating a theme that already exists leaves the
  gateway serving what it compiled at registration. Measured 25/08/2026 taking
  `themes-1.1.0`: `newsprint-dark`'s accent was `#b1554a` on disk and `#e8e2d6`
  in every session, with the script reporting "all 10 serving". **200 proves
  presence, not currency.**

It now samples every 9th declaration of each theme's `variables.css` and looks
for it in the served sheet. Two ways *that* was wrong before it was right:
fingerprinting `--callToAction` alone (which would have passed both leather
themes in the same release, whose change is confined to `--div-9..16`), and
normalising whitespace on the local side only (which made all ten report stale,
seven of them unchanged). **A comparison is only as good as its least
normalised end.**

## Driving the demonstration

```text
1. Site1 or Site2, /admin  ->  Appearance, pick a theme, close the popup
      previews in THIS SESSION only -- writes session.custom.styleOverride
   then  ->  "Commit to every project"
      rewrites CHOSEN_PACK in every project on the gateway carrying
      demo_styles, and rescans; both hub sites re-theme within ~5s,
      including sessions already open
2. same page  ->  "Push BOTH edges"
```

**Picking a theme and then pushing sends the OLD one, and every part of that is
working correctly.** The switcher writes a session prop; a session does not
travel; a push carries `CHOSEN_PACK`. The missing click is the commit — which is
why the header grows an **`Apply <theme> everywhere`** button the moment a
preview is uncommitted, doing exactly what the commit does, right where the
cursor already is.

Read the effective theme **before** clearing the override. Clearing first makes
`session.custom.style` fall back to the committed value, so you would commit the
very thing you were replacing.

**EAM's Send Project is differential and compares resource *metadata*, not
content**, so `commit_pack()` also bumps
`attributes.lastModification.timestamp`. Without that, the hub re-themes
perfectly (its own scan hashes bytes) while every push reports **Success** and
sends nothing. A `Success` from EAM means "nothing to do" as readily as "sent" —
verify on the target:

```bash
docker exec ignition-edge1 sh -c "grep '^CHOSEN_PACK' \
  /usr/local/bin/ignition/data/projects/Edge/ignition/script-python/demo_styles/code.py"
```

## Why the runtime is duplicated

Inheritance is single-parent, and `Themes` carries style classes only. So
`GatewayAdmin`, `Site1` and `Site2` are siblings with no shared *local*
ancestor, and each carries its own `demo_styles`, `demo_guide` and the rest. The
edges get any of it because EAM flattens a Site's ancestors into the pushed
project.

`make validate` compares the duplicated copies **byte-for-byte**, because a
library or a guide edited in one project reads as correct wherever you happen to
look and wrong on the page the customer opens. `commit_pack()` **discovers** its
copies rather than listing them, so a new site is picked up with no edit.

`demo_styles` holds `CHOSEN_PACK`, the generated `THEMES` list and the helpers
around them. The name is historical — it once had to avoid shadowing the
template's inherited `styles` library — and there is no inherited library to
collide with any more, so it is simply the name the three copies share.

## The stylesheet rule still holds

**A child's `stylesheet` REPLACES its parent's — it does not merge.** A project
has exactly one stylesheet resource. Under the old template this was expensive:
a child's own sheet silently discarded ~780 shared declarations, and
**screenshots looked perfect**, because style classes are separate resources and
keep inheriting.

Themes make it mostly moot — the tokens live in the gateway theme now, which no
project resource can replace. `Themes` carries no stylesheet at all, and
`GatewayAdmin`'s is 240 hand-maintained lines written against `var(--container)`,
`var(--label)`, `var(--containerBorder)` and friends, down from 4,589 generated
ones. If a child ever needs a stylesheet again, it owns the whole of it.

## The picker

Ten custom themes in one section, the six Perspective ships in another, two
across. Everything about it is generated.

**The stock section is labelled STOCK COMPONENTS ONLY**, and that label is
load-bearing. `light`, `dark` and the four cool/warm variants are gateway-owned
(`ign-themes.sh` refuses those names outright), and the views bind
`psc-<theme>/containers/card` — which for `light` names a class no project
defines. An unmatched class is not an error; it simply does not paint. Cards
lose their chrome, alarm rows lose their severity colour. That **is** the
comparison, but unsaid it reads as a broken theme.

**Upstream declares the pairing and this repo reads it.** `themes.json` carries
`family` and `pair`; `pair_order()` groups by family with light before dark,
because the switcher wraps at two across and **the list order is the layout**.
Deriving it from the ids gets two cases wrong: `glass-violet`/`glass-green` are
two hues of one family and both dark, and `finance-ledger`/`newsprint-dark` are
counterparts by intent, from different packs, sharing no stem.

### Three traps it cost

- **`ia.container.flex` declares no COMPONENT events.** `events` is null for
  every container type in `ia.components.json`; only leaf inputs carry
  `onActionPerformed`. A component-event handler on a container deploys cleanly,
  highlights under the cursor, and never fires — nothing in the browser console,
  nothing in the gateway log. A container's `events.dom.onClick` *does* fire, so
  the trap is narrower than it looks. Every row here is an `ia.input.button`
  anyway: it takes focus, answers the keyboard, and appears in the accessibility
  tree.
- **`openPopup` has no `width` or `height` argument.** `PerspectiveScriptingFunctions`
  knows `position`, `modal`, `resizable`, `draggable`, `overlayDismiss`,
  `viewportBound`, `showCloseIcon`, `title`, `type`, `params`, `id` — and neither
  of those. **Unknown kwargs are swallowed silently**, so a CSS size passed there
  reads as a working fix and does nothing. Size with `props.defaultSize` on the
  view, or `position={'width': N, 'height': N}` at the call — integers and pixels
  only, with no viewport-relative form of any kind. **The shell adds a 32px title
  bar**, so fit against `defaultSize + 32`.
- **A popup root wants `height: 100%` + `minHeight: 0` — the opposite of a page
  root.** A page root clamped to `height: 100%` is what stops a page scrolling; a
  popup root is a bounded box owning its own scroller. `viewportBound=True` does
  **not** resize a popup: at a 420-high viewport this one draws from `y=-36` with
  its title bar off the top of the screen. The floor is ~510px of viewport height.

> **A confirming measurement that disagrees with what you asked for IS the
> finding.** The popup asked for 460 and rendered 492, and 492 was recorded as
> proof it fitted. Two of the three traps above were sitting in that one reading.

## Adding or changing a theme

A theme is added **upstream** (`packs/<id>.json` + the build there), then taken
here by regenerating against a newer tag. Adding a theme is always safe for
consumers; adding, renaming or removing a **class path** is breaking for all of
them at once, because components bind class paths by name.

**Nothing in this repo enumerates themes by hand.** The picker, the chart
colour maps and `demo_styles.THEMES` are all generated from the same list, so
nothing can fall behind upstream — which is what actually happened on
06/08/2026, when one surface offered 19 packs and two others offered a stale
16, and a pack that is installed and rendering perfectly but reachable from
nowhere a user can click is indistinguishable from one that was never added.

## The CHOICE is a project resource, and it cannot be a session prop

**The theme is a script-library constant, not a session prop, and that is not a
style choice — it is forced.** A child project's `session-props` resource
**replaces** the parent's wholesale rather than merging with it (verified
03/08/2026: dropping `style` from `Site1`'s props left `session.custom.style`
undefined and the page rendered completely unstyled, with binding errors on every
component). The Sites need their own session props for their line data, so a
shared value cannot live there. Each Site therefore *binds* `session.custom.style`
to `runScript('demo_styles.chosen_pack()')` — a script library is a project
resource, so EAM carries it, and `session.props.theme` binds to the same value
so the token layer and the class layer can never disagree.

**A bound session prop cannot be written.** `session.custom.style` is bound, so
the swatches and the Appearance popup write
`session.custom.styleOverride` instead — a separate, unbound prop — and the
binding prefers it:

```
if({session.custom.styleOverride} = '',
   runScript('demo_styles.chosen_pack()', 5000),
   {session.custom.styleOverride})
```

The `5000` is a poll rate, and it is what makes the demo feel live: without it
`runScript` evaluates once per session, so a Designer save only reached pages
opened *afterwards*. With it, a session already on screen re-themes within five
seconds — verified by changing the pack against an open session and never
reloading. "Back to committed" clears the override.

**Nothing may re-apply a theme on startup.** An `onStartup` handler that restores
a globally persisted pack stomps the per-site `session.custom.style` pin — an
inherited button did exactly that once, and Site1 rendered dark-control-room
instead of its pinned look. Every Appearance button here only *opens* the popup,
and the popup acts only on click.

Upstream's own library can persist a chosen pack to a `style_prefs` table. This
repo does not carry that library at all — only the generated style classes — and
would not use it if it did: a database row themes the gateway that holds it and
travels nowhere. Persistence here is `demo_styles.CHOSEN_PACK`, because only a
project resource travels over EAM.

## After the picker, the repo is one line behind the gateway

**After using the picker the repo is one line behind the gateway.**
`make pull-project PROJECT=Site1` (and `Site2`, and `GatewayAdmin`), or just
edit `CHOSEN_PACK` in each to match.
This is the one place the gateway deliberately writes a repo-owned file; the
alternative was a demo that needs a Designer open to run.

Editing `CHOSEN_PACK` in the Designer (or the repo + `make deploy`) still works
and is equivalent.

## Two consequences of `inheritable: true`

- **An inheritable project cannot be launched on its own.** Perspective answers
  "Project Not Runnable — inheritable projects are not runnable as stand-alone
  projects". So the dashboard is only reachable *through* `Site1`/`Site2`, never via
  `Themes` directly. Mark a *leaf* project inheritable and you have silently
  deleted it from the demo — that is what happened to `Style_Gallery`, which sat in the
  project list looking healthy while every launch answered Project Not Runnable.
- **Changing a project's `parent` usually needs a gateway restart, not a scan.** A scan
  applies the file contents but does not always re-link the inheritance graph: after
  repointing a project at a new parent, every page rendered completely unstyled —
  correct layout, no style classes resolved, nothing in the log — until the gateway
  restarted. **Collapsing a level is the exception:** repointing the Sites onto what
  was already their grandparent applied on a plain scan, because the new parent was
  already an ancestor and the resolved resource set never changed. Screenshot after the
  scan; only reach for a restart if it actually renders unstyled.
