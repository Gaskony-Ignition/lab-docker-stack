# Perspective layout and components — the traps that cost a render each

Everything here was measured on this stack, and every one of them fails the same
way: **the view deploys cleanly, the gateway logs nothing, the browser logs
nothing, and the pixels are wrong.** None of it is about themes -- it applies
to any Perspective view on any gateway.

Two habits are worth more than the whole list. **Measure, never look**: compare
a child's width against its container's, `scrollHeight` against `clientHeight`,
the `<img>` box against its wrapper's. And **a measurement that disagrees with
what you asked for IS the finding** -- a popup asked for 460 and rendered 492,
and 492 was written down as proof it fitted, with two separate bugs sitting
inside that one reading.

## Scripting

**Perspective script actions must be gateway scope (`"scope": "G"`).** A
component event with `"scope": "C"` **silently does nothing** — the click
registers, the button takes focus, nothing is written, and there is no error in
the browser console or the gateway log. Sixteen style swatches did exactly
nothing until the scope changed; the working `StyleSwitcher` popup had used `G`
for all 64 of its handlers all along. If a handler appears not to fire, check the
scope before anything else.

## Containers

**A container can define its own child-position schema, and `ia.container.tab` does.**
Its `childPositionSchema` is `{tabIndex}` with `additionalProperties: false` — a child
belongs to a tab by its **position**, not by its order among the children. Giving the
children a flex position (`{basis, grow}`) is invalid there, so all three silently fell
back to `tabIndex: 0` and tabs 2 and 3 rendered an empty content frame with **no error
anywhere** — the tab highlighted, `data-tab-index` changed, and the component tree
collapsed from 71 components to 2. Check `childPositionSchema` in
`ia.components.json` before positioning children of any container that is not a flex
or coordinate container. Two neighbouring traps on the same component: `tabSize` is an
object `{width, height}`, not a string, and `tabStyle` **requires** all three of
`active`, `inactive` and `disabled`. And **an indexed sub-prop binding into
`props.tabs[N].disabled` is not a valid target here** — three of them produced
exactly three entries in the session's NOTICES bar and a red border round the
whole page, with nothing in the gateway log. Indexed paths *do* work on charts
(`props.series[0].line.appearance.stroke.color`), which is what makes this look
like it should; the difference is that `tabs` is a plain array prop rather than
a schema'd object array. Disable the visible nav instead — on this console the
tab strip is hidden and the header buttons are what anyone clicks.

**`alignItems`, `justify` and `wrap` are PROPS on `ia.container.flex`, not style keys** —
they have their own defaults and the CSS equivalents in `style` are silently ignored.
Measured on the Architecture header: `style.alignItems: center` computed to `stretch`,
so 34px icon buttons sat at the top of a 45px row and the glyphs landed 12px above the
menu text. Setting the prop lined every child up on the same centre. If something is
mysteriously not centring, check whether you wrote it as a style key.

**A style class already carries padding; don't add more inside it.**
`containers/card` pads by 16px, so a child container with its own `padding: 10px` put
the body 26px in while the header stayed at 16px. Use gaps between children and let
the class own the inset.

**Never centre with `justify: 'center'` inside a scrollable container.** Centring
works by pushing content out of *both* ends of the box, and nothing can scroll above
its own origin — so the moment the content is taller than the space, the top is
clipped and is **unreachable at any scroll position**. Measured on the EAM tab at
1207×520: the tree's box began at y=161 while the hub card was drawn at y=150.
Centre with **collapsible spacers** instead — an empty flex child with
`{basis: '0px', grow: 1}` above and below, and the content `shrink: 0`. `grow` only
shares out *positive* free space, so the spacers take half the slack each when there
is room and collapse to nothing when there is not, leaving the content top-aligned
and the container scrolling normally.

## Sizing, and the ways a box lies about its height

**`height: 100%` on a flex child defeats the row's `alignItems: stretch`, and then
`top: 50%` inside it means nothing.** Stretch only applies while the cross size is
`auto`; an explicit height opts out of it, and `100%` of a flex row whose own height
is content-driven resolves to `auto` — which for a box whose children are all
absolute is **zero**. The redundant-pair chip was positioned `top: 50%` inside such a
box and drew *above both cards it was meant to sit between*, with no error and
nothing in the log. It survived a dozen nudges to the chip, the rules and the leg
because none of them was wrong — the container was. **Anchor an overlay to a box
whose height is not in question:** make the row itself `position: relative` and give
the overlay `top: 0; bottom: 0`, which is unconditionally full height, then leave a
plain empty flex child in the row to reserve the gap. Measure `chip centre` against
`card centre` and assert `0`; the failure looks like a styling opinion, not a bug.

**Three ways a Perspective box clips its own content with `overflow: hidden` — no
scrollbar, no error, just missing pixels.** All three are found the same way: compare
`scrollHeight` to `clientHeight` on every visible element and report anything bigger.

- **A label's line box can exceed its own height.** `ia.display.label` renders as
  `display: flex`, so the text is a flex item sized by the font's real ascent+descent
  — 25px for a 20px font — while a style class setting `line-height: 1.15` makes the
  element 23px. Descenders are shaved off. Set `lineHeight` ≥ ~1.35 on the label.
- **An `<img>` is inline, so its wrapper is always ~4px shorter than the picture.**
  `props.style` lands on the wrapper, so growing the wrapper grows the gap with it
  (72→76 became 76→80). Kill the line box: `lineHeight: 0` and `fontSize: 0`.
- **An embedded view's `defaultSize.height` is a promise the content has to keep.**
  Declare 62 for content that measures 69 and the flex children shrink to fit rather
  than the box growing — so raising the height moves the clip onto whichever child
  has `shrink: 1` instead of removing it. Measure the content, then declare that.

**`height: 100%` on a PAGE-LEVEL view root is what stops the page scrolling, and
it does not look like it.** It reads as "fill the pane"; it means *never be
taller than the pane*. The pane above already has `overflow: auto`, so with the
root clamped there is nothing to scroll **to** — and the pixels the content
wanted have to come out of whichever child can shrink. On a laptop the page
therefore always "fits", by crushing the one thing you opened the tab to look
at, and the scrollbar never offers anything worth taking. Measured on the
console's Store & Forward tab, whose trend is the entire point of the tab:

```text
viewport 900 tall   chart 313px          viewport 656 tall   chart 117px
```

Fix it in **two places or neither** — this is why it survives so many attempts,
because every one-sided change moves a number and fixes nothing:

- the root becomes `minHeight: 100%` with **no `overflow`**, so it fills the pane
  when there is room and grows past it when there is not;
- the flexible band gets a real floor (`minHeight`, and `shrink: 0`), so it stops
  absorbing the deficit and the overflow actually happens.

Measured 25/08/2026 at 1366×640, one change at a time: `height:100%` +
`overflow:hidden` → chart 117px, pane travel 95px; root unclamped → chart 117px,
travel 212px; floor added as well → **chart 250px, travel 332px**.

The chart's own `height: 100%` was the same trap one level down — 100% of a
parent whose height is content-driven resolves to `auto`, which after the title
and the hint have taken theirs is *whatever is left*. **Embedded views keep
`height: 100%`** (`DemoCard`, `RigChip`, `CardMetric`, `GatewayCard`): there it
is correct, because their box is decided by the parent that placed them and they
are not what the pane scrolls.

An **empty label still draws a line box**, so a card with a blank footer keeps the
footer's row of height. Bind `meta.visible` to `{view.params.x} != ''`.

**A repeater's tile height must be measured at the NARROWEST column, not the
widest** — the tile's own `minWidth`. A blurb and a chip row rewrap as a column
narrows, so a height that fits at 1440 clips by 60px at 1100. And
content-sizing instead (`useDefaultViewHeight: false`) collapses the lot: the
embedded view sizes to content, so an inner `height: 100%` resolves to `auto`
and every tile falls to ~10px — the `height: 100%`-defeats-stretch trap, one
level down. Also add the border: `containers/card` draws 1px, so a declared
height equal to the content clips by exactly 2px.

**`elementPosition.basis` is a `css-length`, and `calc()` is not one.** The
schema in `perspective-common-<v>.jar` (`schemas/css-length.schema.json`) is
`^(auto|0)$|^[+-]?[0-9]+.?([0-9]+)?(px|em|ex|%|in|cm|mm|pt|pc|rem)$` — so
`calc()`, `min()`, `max()` and `clamp()` are all invalid there, and so are `vw`
and `vh`. They **render perfectly anyway**, because the gateway does not
re-validate a view written externally on scan; the Designer is where an invalid
value surfaces. Prefer a plain percentage: for a three-across grid `32%` does
what `calc(33.333% - 7px)` does (measured 456 against 455) and degrades to
two-across on a narrow window instead of cramping. `props.style` is the one
place free-form CSS is safe — its schema leaves `additionalProperties` open.
`Redundancy/view.json` still carries a `calc(50% - 6px)` basis, which is what
made this look proven.

## Embedding views as parts

Measured 21/09/2026 building the shared left rail (DEMO-CONSOLE.md):

- **A reference is the whole path inside the braces.** `{view.custom.rail}.head`
  is not `{view.custom.rail.head}` -- it is a syntax error, and the binding
  reports it only as error quality: a small red overlay icon in the corner of
  the component, nothing in the gateway log, nothing in the browser console.
- **The expression language has no object literal**, so an embedded view's
  `props.params` can only be bound to a dict that already exists. Have the
  script shape the params (`demo_rail.rail()` returns `head`, `banner`, ...),
  or set the static ones in `props.params` and bind just the live key:
  `propConfig["props.params.can"]` works on `ia.display.view`.
- **A part that must collapse** is embedded with `useDefaultViewHeight: false`
  and has a root with no `height: 100%`, and its content leaves the layout with
  `position.display`. The embed then measures 0 when empty and grows to two
  lines when it needs them (the banner: 58px with text, gone without; a button:
  30px, 46px with its note). `meta.visible` would have kept the box.

## Hiding things

**`meta.visible: false` hides a component WITHOUT collapsing its box.** Measured
24/08/2026: the hidden component computes `visibility: hidden` with
`display: flex`, so it is blank and still occupies its full width. A row of
chips whose optional `key` button was hidden this way reserved **47px** of
nothing beside every chip that had no credential — invisible, so nobody looked
at it, and it read as "the chips are oddly spaced" rather than as a bug. It is
the same shape as the line-box trap above: the thing you cannot see is still
taking up room. Collapse the geometry as well (`width`/`minWidth`/`padding` to
`0`, `overflow: hidden`) and put the gap on the sibling's margin rather than the
row's `gap`, which is spent on a zero-width child too. Do **not** reach for
`display: none` — it fights the inline `display: flex` Perspective writes on
mount and flickers. Find these by measuring, never by looking: compare each
child's width against its container's.

**Where a component must be genuinely absent, switch VIEWS rather than hide
children.** Because a hidden component is still in the DOM, "the same card with
its button hidden" still answers `querySelectorAll('button')` with a button
reading `Start` — which is exactly how `e2e-demos.js` identifies a demo. The
Demos page's dotted placeholder slots are therefore a separate view
(`DemoSlot`), chosen by a thin `DemoTile` wrapper whose `ia.display.view` binds
`props.path` to `if({view.params.demo.placeholder}, 'DemoSlot', 'DemoCard')`.
One binding, no collapsed geometry, and the property the test depends on holds
by construction instead of by memory.

## Images and third-party components

**`ia.display.image` is sized by `props.fit`, not by CSS.** `props.style` lands on the
*wrapper*, so an `objectFit` set there computes to `fill` on the `<img>` while the
wrapper keeps its own box and `overflow: hidden` — the image is then silently cropped.
The gateway cards lost the bottom third of every icon this way: portrait artwork
(0.65 aspect) rendered 64×99 inside a 64×64 wrapper. `fit.mode` defaults to `"none"`;
set it to `"contain"` and give the wrapper the artwork's aspect ratio. Verify by
measuring — the `<img>` box and its parent box should match.

**A third-party component with no style classes may still be themeable.** The
Architecture Builder's internals carry no class names at all, but it paints itself with
Ignition's own `--neutral-00…100` custom properties — and custom properties inherit, so
redefining them on an ancestor makes its inline styles resolve to our colours, with no
`!important` and no fork. The per-pack overrides live in a project stylesheet resource
at `com.inductiveautomation.perspective/stylesheet/stylesheet.css`, generated from the
packs' own style-class files so the packs stay the source of truth. Scope by the class
Perspective already emits — `psc-<pack>/containers/page` — matched as
`[class*="psc-<pack>/"]`, because a CSS class selector cannot contain the slash.
Anything drawn by a bundled library still needs its own rule: React Flow's zoom controls
are class-based, and its screenshot icon is stroke-drawn with a hard-coded `#555` while
the other three are fill-drawn.

## When the whole view fails

**A component with no `type` (or no `meta.name`) fails the WHOLE view, and the
browser blames the wrong thing.** Perspective's deserialiser reads both
unconditionally; one absent key three levels down throws
`NullPointerException ... JsonObject.get(String) is null`, and what renders is
**"View Not Found — View with configured path not found in the project"** — which
reads as a missing or misnamed view rather than as a view that is right there and
one key short. The gateway log names the view but not the component. `make
validate`'s `check_component_shape` now walks every view for both keys, because
the failure is otherwise indistinguishable from a deploy that did not land.

## Charts

### Stock charts, not Embr (03/08/2026)

The dashboard originally drew its OEE donut and throughput trend with
`embr.chart.apex-charts`. On both edges those rendered as black
`embr.chart.apex-charts not found` boxes — permanently, and in the middle of the
screen you are demonstrating. They are now `ia.chart.simple-gauge` and
`ia.chart.xy`, which live inside the Perspective module itself and so render
identically on hub and edge.

`ia.chart.*` IDs are **amcharts-backed and real**, but they are not in
`ia.components.json` — they live in `perspective-amcharts.components.json` and
`perspective-timeseries.components.json` inside `perspective-common-<v>.jar`.
That jar is the authoritative source for both the ID list and the full prop
schema; pull it from `data/jar-cache/com.inductiveautomation.perspective/` and
read the schemas with python `zipfile` rather than guessing prop shapes.

Two that cost a render each:

- **`ia.chart.xy` draws an opaque white canvas unless you ask it not to.**
  `props.background.render` defaults to `"none"`, which does *not* mean
  transparent. Set `render: "color"`, `color: "transparent"`, `opacity: 0` — until
  then every dark style pack shows a white rectangle where the chart should be.
- **A stock chart does not read style classes for its series colours.** The pack's
  `charts/frame` class only styles the surrounding box. Each colour-bearing leaf
  (`props.series[0].line.appearance.stroke.color`, `props.xAxes[0].appearance.grid.color`,
  `props.arc.color`, …) carries its own map transform on `{session.custom.style}` —
  indexed sub-prop binding paths work and keep the JSON small. Swap a chart without
  porting those and the dashboard still *works*, but it stops re-theming, which is the
  one thing the demo exists to show.
- **A value axis auto-ranges on `min: ""` / `max: ""`, not on `0`.** Written as `0`/`0`
  with `useStrict: false` amCharts scales the axis to nothing and draws the series off
  the plot: the chart looks empty while the *date* axis still ranges correctly from the
  same data, which sends you looking at the query instead of the axis.
- **A date axis needs `inputFormat` spelled with `HH`.** The schema default is
  `yyyy-MM-dd kk:mm:ss`, and `kk` counts 1–24, so midnight parses as hour 24 and lands a
  day out. Set `render: "date"` and pair it with `connect: false` on the series if gaps
  in the data are the point — otherwise the line is drawn straight across a missing
  minute, quietly asserting the opposite of what happened.
