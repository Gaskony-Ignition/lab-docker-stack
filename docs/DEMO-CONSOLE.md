# Starting only the demonstration you are about to give

The stack is nine stacks. No single demonstration needs more than half of
them, and running all of it costs about **6 GB of RAM** on this machine and
enough CPU to make the gateways themselves slow.

So the **core** stays up and everything else is started when it is about to be
shown:

```text
core        npm + ignition + wd-control          ~1.7 GB
demo        whatever that one demonstration needs, started on the spot
```

`make core` starts the core. The **Demos** tab in GatewayAdmin is the front
door after that, and `make demos` is the same thing from a terminal.

---

## The one idea in it

**The page does not start or stop stacks. It records which demos you want, and
the control plane reconciles the machine to match.**

```text
wanted = core + every stack the demos you asked for need
          -> anything in `wanted` that is down, comes up (in ORDER)
          -> anything of ours that is up and not in `wanted`, goes down
```

That is not a stylistic preference, it is the only version that is correct.
Stacks are shared: both edges carry the EAM push **and** the store-and-forward
road. So "stop store & forward" must not stop the gateways the EAM
demonstration is still using. Reference-counting that inside a button handler is
arithmetic that is right until the day it is not, in front of an audience.

Verified: with `store-forward` running, stopping `eam` — which needs the same
two edges — reported *"nothing to do, already in that shape"* and left both
edges up.

It is also why the page cannot stop the core. Not because the buttons are
hidden: **the core is always in `wanted`**, so no request the page can make
would take it down. A page able to stop the gateway that serves it will do
exactly that, once, in front of somebody. From a terminal `make down` still
stops everything, core included — the command line keeps the big hammer.

---

## Using it

```text
make core                        the core, and nothing else
make demos                       what exists, and what is running
make demo-start DEMO=eam         that demo and everything it needs
make demo-stop  DEMO=eam         unless another running demo needs its stacks
make demos-stop                  back to the core alone
```

The page has the same five actions and the same state document behind it. That
is deliberate: `scripts/wd-demos.sh` calls the same endpoints the Perspective
page calls, and both end at `scripts/stack.sh` — the script `make up` runs. One
implementation of "bring a stack up", so the page and the terminal cannot tell
a customer different stories about the same machine.

### Adding a demo

Add it to `demos.json` — an id, a title, a blurb, the stacks it needs, and the
console **tab** it is watched on. The card appears in place of a dotted slot;
nothing else changes, up to the sixth demo. The seventh is a layout decision —
see the frame, below.

`tab` is what keeps the cards and the tabs honest about each other. **Every demo
tab opens whether or not its demo is running.** Disabling a tab until its demo
is up would avoid a page of dashes, but it makes the console un-explorable: you could not see what a
demo shows before deciding to spend ten minutes and a gigabyte starting it,
which is the first thing anyone meeting the stack wants to do. So the tab is
always reachable, and the page carries the reason instead: `demo_control.
ready_note(<id>)` returns "*Title* is not running. Start it on the Demos
tab.", the tab's rail shows it as its banner, and every action button on
the tab is bound to `ready` and off until the demo is ready. Data areas show
dashes, not last-known values — MQTT Engine keeps the last reading it received
long after its edge has gone, and a stopped demo displaying it reads as half
working. The key is the same vocabulary `demo_guide.TAB_TOPICS` uses and
deliberately **not** a tab index: an index would tie this file to the order of
Main's children, which `ia.container.tab` keys by position and which nothing
would re-check if it changed.

**Put the new demo in the list where its tab sits along the top**, because the
cards are drawn in `demos.json`'s order and the tabs in `demo_control.TAB_KEYS`'
order — two orderings, two files, and nothing joining them. Reorder either alone
and the page still renders perfectly: every card still names the right tab and
every label is still correct. It is wrong only to a reader, who pairs the first
card with the first tab by **position** before reading either label. Cards
running redundancy, store-forward, EAM under tabs running EAM, store-forward,
redundancy is an exact reversal, and it reads as two different navigations on
one page. `make validate` cross-checks the two lists.

**Three demos, three tabs, and each card is titled after its tab** — "EAM Demo",
"Store & Forward Demo", "Redundancy Demo". There is deliberately no fourth kind
of card: a support-tooling card mixed in with the demos would mean four cards
against three tabs, with no way to tell from the page why. Support tooling is
not a demonstration, and if it needs a card it gets one of its own.

### One row of four; only three of them are cards

The tiles are **one row of four** — the demos, then inert dotted **slots**
carrying a "+" to fill out the row. Three cards on their own said, to anyone who
had not been told otherwise, that three demonstrations is the product.

> **Superseded, same day.** This shipped first as a *six*-tile frame, three
> across and two down, which reserved a whole second row to say "more coming"
> and cost a third of the page. Measured at 1440×900: 262px of header prose plus
> a 530px grid, for three cards and three empty boxes. One row says the same
> thing for a quarter of the space, and the count is derived (`(-n) % ACROSS`)
> so it never reserves an empty row at any number of demos.

That is not the fourth-card mistake repeating itself, and the difference is the
whole design. The tools card was **a card**: same fill, same border, same
shadow, a title and a button — so it read as a demonstration, and the only way
to learn it was not one was to click it. A slot is transparent, dotted and
carries no button at all. It is not something to click that does nothing; it is
visibly *not a thing yet*. The test to hold any future addition to: could a
customer mistake it for a demonstration? A card must fail that test. A slot
must pass it.

**A slot has no button, and that is a contract rather than a style choice.**
`scripts/e2e-demos.js` finds a demo card by finding a `<button>` reading exactly
`Start` or `Stop` and walking up to the ancestor holding one of them. Give a
slot such a button and it becomes a demo to the harness — and since the slots
are identical to one another, the run would fail on the *distinct card text*
assertion, which names the card boundary and would send the next reader after
entirely the wrong thing. So the split is structural: a slot is a different
view (`DemoSlot`), reached through `DemoTile`, not a `DemoCard` with its button
hidden. **Hiding it would not even work** — `meta.visible: false` computes
`visibility: hidden` with `display: flex`, so the button would still be in the
DOM, still reading "Start", and still occupying its box.

**The column count lives in two places**: `demo_control.ACROSS`, which decides
how many slots pad the last row, and the `demos` repeater's
`elementPosition.basis` of `24%`, which decides how many tiles fit on a line.
Change one and not the other and nothing errors — you get a fifth tile wrapping
alone, or four slots padding a row that holds three. `make validate` derives the
column count from the basis and fails when they disagree.

**But the basis is not what makes it responsive — `minWidth` is.** A percentage
basis always yields the same column count and simply cramps; `minWidth` on the
tile beats `flex-basis` unconditionally, so `24%` with `minWidth: 300px` on
`DemoTile` gives four across on a monitor and drops to three, then two, then one
as the window narrows. Measured: 4 / 4 / 4 / 3 / 2 at 1920 / 1440 / 1270 / 1100 /
800, with no horizontal page scroll at any of them.

**The tile height is derived from the NARROWEST column, not the widest.** A
fixed height cannot be right at every width, because the blurb and the chip row
rewrap as the column narrows — 231px fits at 1440 and clips by 60px at 1100. The
narrowest a tile can ever be is its own `minWidth`, so measure the content there
and declare that: at a 301px tile all three cards measure 229, plus the 2px
border `containers/card` draws, which the declared height must include or the
card clips by exactly the border.

**Content-sizing the tiles instead does not work**, and it fails in the
documented way: with `useDefaultViewHeight: false` the embedded view sizes to
content, so `DemoCard`'s root `height: 100%` resolves against a content-driven
box, becomes `auto`, and every tile collapses to ~10px. That is the
`height: 100%`-defeats-stretch trap in docs/PERSPECTIVE-LAYOUT.md, one level
further down.

**Shorter chip labels are a layout fix, not a wording preference.** A label
ending in a noun -- "Edge 1 gateway", "Backup gateway" — costs
enough width that, at four across, Store & Forward's four chips wrap to a
second row. A row of
tiles is as tall as its tallest member, so that one wrap made *every* card 40px
taller than its own content needed: 269 against 229. They are all buttons that
open a thing; which kind of thing was never the useful half.

**`basis` is a `css-length`, and `calc()` does not satisfy it.** The schema in
`perspective-common-<v>.jar` is
`^(auto|0)$|^[+-]?[0-9]+.?([0-9]+)?(px|em|ex|%|in|cm|mm|pt|pc|rem)$`, so
`calc(33.333% - 7px)` — which is what this was first written as, and which
renders perfectly — is an invalid value the gateway happens not to re-check on
scan. A plain `32%` is equivalent to the pixel (measured: 456 against 455) and
inside the schema. `Redundancy/view.json` still carries a `calc(50% - 6px)`
basis of the same kind; it works, and it is the reason this looked proven.

`make validate` also fails if a demo names a
stack that does not exist, if it names a **core** stack, or if a stack on disk
is in no demo at all — that last one being the way a new stack would otherwise
become invisible: startable from nowhere, and silent about it.

### The fourth demo: Sparkplug (11/09/2026)

`sparkplug` fills the row's fourth tile exactly — `demo_control.ACROSS` is 4 and
this is the fourth demo, so no dotted slot is left and nothing about the
frame's layout changed. Its stacks are `ignition-edge3` and `ignition-edge4`
— a second, **isolated** pair of edges (no Gateway Network, no EAM agent; see
`docs/SPARKPLUG.md`, owned by the edge side of this build) that talk to the hub
only as Sparkplug B over the broker, MQTT Distributor on the hub pair itself
(`docs/MQTT-DISTRIBUTOR.md`).

The console-side pieces, all in `GatewayAdmin`:

- **`sparkplug_demo`** (script library) is this demo's `sf_demo` — one module
  polling each edge's own read-only/actionable HTTP "observer", reading the
  hub's `[MQTT Engine]` mirror and real alarm status, and holding the decoded
  wire log. Every edge call is isolated and cached exactly the way
  `sf_demo._edge_state()` already does it, for the same reason: one edge being
  slow or down must never blank the other edge's numbers.
- **A WebDev route**, `com.inductiveautomation.webdev/resources/sparkplug/
  wire/` (`POST /system/webdev/GatewayAdmin/sparkplug/wire`), is what
  wd-control's MQTT listener calls for every message on the demo's Sparkplug
  topics (*THE WIRE's feed* below). It is deliberately
  two files: `doPost.py` stays byte-0-minimal (a WebDev handler file may carry
  nothing before the `def`, not even a comment — a top-level import there is a
  silent empty-200) and calls straight into `sparkplug_demo.handle_wire_post`,
  which does the actual decoding. This is the **OBSERVATION road**, labelled
  as such on the page: the listener hands the console a *copy* of the traffic
  purely to show it decoded; nothing here ever becomes a tag. (On EMQX an
  EMQX rule posted it, set up by `scripts/sparkplug-witness.sh`; both retired
  with EMQX, 18/09/2026.)
- **Two new connector colours** in `stylesheet.css` — `wd-conn-h--data`
  (amber) and `wd-conn-h--command` (blue) — because this tab's flow diagram
  has two roads to tell apart on sight (Sparkplug data edge→cloud, and a
  write/ack riding a DCMD back) and reusing Store & Forward's `--gan`/`--mqtt`
  hues would say "this is that same road," which it is not: different edges,
  different group, no shared history between the two demos.

Everything Engine-side (`[MQTT Engine]Edge Nodes/...`) is kept in one constant
block in `sparkplug_demo.py` on purpose — it was a guess until the edge side's
live spike (`docs/SPARKPLUG.md`) measured a running Engine and corrected it;
the block is where that correction landed.

**Rebuilt 14/09/2026 as six guided scenarios** (Live data, Alarms, Cloud to
edge, Link outage, UDT experiment, Notifications) with an MQTT traffic drawer,
generated by `scripts/gen-sparkplug-views.py` — see `docs/SPARKPLUG.md`'s *The
page*. Below, "Tags/Alarms/UDT/Notifications tab" means the matching scenario.

**Live-corrected, 11/09/2026** (`docs/SPARKPLUG.md`'s T1–T8):

- The demo runs Sparkplug **Templates**, not the flattening default — Engine
  holds one shared `[MQTT Engine]_types_/PumpStation` for both edges. The UDT
  tab shows which edge's definition Engine currently holds (its members
  compared against each edge's raw member set) and the collision rule: first
  edge to birth after a mismatch wins, the hub logs it, the loser's NBIRTH is
  dropped. Diverge/Restore now POST `rebirth: true, scope: module` — a plain
  node-scope rebirth just resends Transmission's CACHED definition and looks
  like nothing happened.
- Alarm rows join edge and cloud on the alarm event **id**, not name or
  source path — the hub keeps the edge's id verbatim, so it is the one exact
  key once an alarm has fired at least once.
- **Node Info/Online cannot be trusted alone.** On EMQX a broker-side kick
  sent no Last Will, and Engine kept a cut node Online for the whole outage.
  Since 18/09/2026 the cut is a network one (below), and the broker gives up
  after 1.5 x keepalive: Engine says Online for the first ~15 s of a cut. The
  page cross-checks Engine against when the wire witness last actually saw a
  message from that node, and calls it STALE past 5s —
  `sparkplug_demo.WIRE_STALE_MS` — even while Engine still says ONLINE. The
  cloud card's own online count uses the same check, not Engine's alone.
- **Cut Edge 3 link (60s)** asks wd-control to take `ignition-edge3` off
  `wd-mqtt` for 60 s (`sparkplug_demo.cut_edge3()`/`restore_edge3()` through
  `demo_control.mqtt_cut`/`mqtt_restore`) — the same cut Store & Forward's MQTT
  break uses; see *Cutting an edge off MQTT* below.
- A cut's buffered values (Transmission flushes them as one DDATA carrying
  ~192 historical metrics) were landing on the hub and being stored **nowhere**
  — Engine only historises a metric into a tag that already has history
  enabled, and it recreates every AlarmDemo tag on each birth, so "enabled
  once" does not stay enabled. `ignition/timer/SparkplugHistory` is a sibling
  of the Store & Forward demo's `SFHistory` timer (not a change to it — two
  demos' tags on the one historian) that re-applies history the same way. The
  Tags tab's North/South Level trend is what makes the recovered minute
  visible after a restore.
- Edge-to-cloud latency (~1s) is Transmission's own `tagPacingPeriod`, not the
  network — the broker leg underneath it measured 2-4ms. Noted next to the
  latency figure so it does not read as a slow link.

**Notifications, live-wired 11/09/2026** (`docs/SPARKPLUG.md`'s "Notifications
over MQTT", Phase C): four roads, three of which now deliver.

- **A Sparkplug-propagated alarm never runs a hub pipeline** — Engine keeps
  the edge's pipeline properties verbatim but never evaluates them. The
  cloud's own notifications run off a DIFFERENT alarm entirely,
  `CLOUD_OWN_ALARM_NAME` ("Pump Fault (cloud)", a reference tag on Engine's
  PumpFault alarmed in the hub's own `[default]` provider) — excluded from
  the Alarms tab (which compares an edge alarm against its own propagation;
  this one is not that) and shown on the Notifications tab instead, as what
  it actually is.
- **EdgeNotice** (`[MQTT Engine]Edge Nodes/AlarmDemo/<node>/Pumps/EdgeNotice`,
  a sibling of the site/UDT folder, not inside it) is one working edge→cloud
  road — an ordinary Sparkplug metric, 0.75-1s (Transmission's publish
  cycle), sequenced and store-and-forwarded as history with original times,
  but the live tag only ever holds the latest.
- **The raw-MQTT road** (`notify/AlarmDemo/<node>` → `[MQTT Engine]Notify/
  notify/AlarmDemo/<node>/<key>`) is the OTHER working one, fixed
  11/09/2026 (EDGE agent gave the RPC client its own CA + credentials) —
  ~10ms, queued in order through a 45s cut, but in-memory (unbounded,
  unconfigurable, nothing marked late on replay) and per-key: each JSON
  field lands as its OWN tag, so a key absent from a newer message keeps
  its stale value from whenever an earlier message last wrote it (a
  burst-test message's `test`/`i`/`of` sitting beside a real alarm's
  fields, measured live). The panel groups by the receive time of the
  document's own `ts` tag and calls out anything that does not match as
  "left over from an earlier message," rather than mixing two events'
  fields into what reads as one.
- **CloudNotice** (same sibling location as EdgeNotice) is cloud→edge and
  works, riding a DCMD — shown both as Engine's own copy and the edge's own
  copy (`notices.cloudNotice`) side by side, since a customer's real
  question is "did it actually arrive," not just "did the hub send it."
- `ensure_history()` now also re-applies history to `EdgeNotice` (same
  reason as the station tags — Engine recreates it on every birth), so a
  notification raised during a cut is not silently dropped when its
  buffered flush lands.
- The wire witness's own decoder tried to protobuf-decode the raw-MQTT
  road's plain-JSON `NOTIFY` payloads (`unsupported wire type 3` on every
  one) and mislabelled the node column `AlarmDemo` (the group, from the
  wrong topic segment) instead of `Edge3`/`Edge4` — invisible while this
  road delivered nothing, both fixed once it started.

### One rail on every demo tab (21/09/2026)

The four demo tabs grew in batches and each invented its own left column. They
now share one, top to bottom: the demo's name and state badge, the not-running
banner, **STATUS** (three or four `label value` facts), the controls under
small-caps group headings, and **GATEWAYS**. Content sits to the right: one
sentence, the demo's picture, then what it shows. Redundancy's rail was the
reference.

| piece | what | where |
|---|---|---|
| `RailHead` | name + state badge -- the **card's** words (`demo_control._demo`), so rail and card cannot disagree | view |
| `NotRunning` | the banner; collapses itself when empty | view |
| `RailGroup` | STATUS / CONTROLS / GATEWAYS | view |
| `RailFact` | one `label value` row, a badge when the value is a state | view |
| `RailButton` | a guarded, full-width button with its reason under it | view |
| `RailLink` | a gateway, two to a line | view |
| `StripNode` | one box on a strip (S&F's, MQTT's) | view |
| `demo_rail` | `rail(<demo>)`: the head, banner, facts and links; `fire(session, key)`: every button's work | script |

The views are written by `scripts/gen-rail-views.py`; the MQTT tab's generator
and the three hand-built tabs all embed the same ones. **A `RailButton` carries
an action KEY, not a script** -- a view cannot be handed a handler as a
parameter -- and `demo_rail.ACTIONS` maps the key to the work, so every rail
action on the console is one table.

Rules the rail keeps:

- **Fixed 260px, never grown**, and it never scrolls: spare width and height go
  to the content. What does not fit in 640px moves (the gateway links go three
  to a line; S&F's tree became a strip; MQTT's scenario buttons go two to a
  line), it is not shrunk.
- **A stopped demo shows "—", not its last reading**, and the banner is the one
  loud thing on the tab. A fact marked `always` is the hub's own and stays.
- **Words.** One sentence per page; a fact is `label value`; a button is a verb
  and a noun; a refusal is one clause; an empty state is *Nothing yet* or *—*.
  Visible words at 1366x640, counted off the rendered page, fell on every tab
  and every MQTT scenario when this landed.

### The bar, and the rig strip

Above the tiles is **one bar**, about 52px. It replaced 262px of header: a
title, a live headline, two sentences of instruction, one RAM sentence, a core
status line and a lone "Hub gateway" chip on a row of its own.

- The **instruction** ("Start what you are about to show; stop it when you are
  done") is gone. It was on screen permanently, and the **Guide** button two
  inches away in the header is where instruction lives by design.
- The **RAM sentence became a meter** — two segments, this stack's share then
  everything else, against the machine total. It is the only thing on the page
  that answers "can I afford to start another demo", and a picture answers it
  from across a room. The prose version survives as the meter's tooltip,
  because the reason the denominator exists — and the Docker Desktop caveat,
  where the reassuring number is the one that cannot see the danger — is a
  paragraph and does not fit in a bar.
- `core 3/3`, the **hub trial** in minutes, and **load** are pills beside it.

Below the tiles is **the rig strip**: one chip per stack this repo owns, with a
health dot and — only while it is up — its memory. This is deliberately the band
that is **never empty**, which is what lets the readiness panel be a band that
appears only when it has something to say. With core-only, three chips are lit
and six are dimmed, which is exactly what 112px of prose was spending itself to
say, except that the chips also say *which*.

**A down chip must LOOK down.** Nine identically styled pills differing only by
an 8px dot read as nine healthy stacks at a glance — the pill is 100px and wins
the glance — so a stopped stack dims the whole chip. And its **memory is
hidden**: `mem` is the *last measured* figure and survives the stack stopping,
deliberately, because that is what lets a stopped demo's card print a real cost
instead of a guess. On a rig chip the same number reads as "using 1.0 GB right
now", so an idle machine listed about 6 GB of phantom usage. A cost is not a
reading.

### While a demo is starting

A start takes one to three minutes. Saying only "Starting" for all of it would
make a start going perfectly indistinguishable from one that had wedged, at the
exact moment somebody is standing in front of a customer deciding whether to
reload. Reloading mid-reconcile is how you end up clicking Start
again and getting *busy*.

The notes band now carries a bar and a line:

```text
[========------]  starting eam -- 42s of about 95s, 53s to go   1 of 2 done
```

**Elapsed alone was not enough**, which is why there is a denominator. It
answers "is anything happening" but not "should I be worried" — the same reason
the RAM figure grew a total. `wd-control` remembers how long each stack has taken
to become healthy **on this machine** (`TIMING`, persisted beside the memory
samples) and the estimate is the sum over the job's plan.

Four things about it that are deliberate:

- **The estimate is allowed to be wrong, out loud.** Once elapsed passes it the
  line stops counting down and says *"112s, longer than the usual 95s"*. A
  countdown that hits zero and sits there converts "this is taking a while" into
  "this is stuck", which is the wrong conclusion and the expensive one.
- **"about" versus "at least".** A stack never started here contributes nothing
  to the sum, so the estimate is a floor rather than a prediction, and the line
  says which — the same honesty as a card printing `>= 2.1 GB, 3 never run`.
- **A timing is rolling, not last-seen** (3:1 toward the existing figure). One
  cold start after a reboot is minutes slower than the steady state, and letting
  that reading stand would make every later estimate wrong in the direction that
  invites "it has hung". A **timeout records nothing at all** rather than
  recording `READY_TIMEOUT`, which would poison the estimate with the one run
  that went wrong.
- **The bar moves on time, capped below 100.** A bar that sits still for the
  ninety seconds one gateway takes is the complaint this answers; a bar that
  reaches the end while the job is still working is a different lie. The step
  count is what says "done", and it counts **completed** — phrased as "step 2 of
  2" it read as finished while the last stack was still being waited on.

With no history at all the line is just elapsed and there is no bar estimate. A
made-up denominator that is wrong the first time is how a progress indicator
stops being believed.

### The Guide button

**Every demonstration carries its own instructions.** A `Guide` button on the
console header (and on each Site's `/admin`) opens a popup naming where to
stand, what to click, what happens next and — the line that matters — *where to
look while it happens*, since all three demos turn on something changing a few
seconds later somewhere other than where you clicked. The content is data in
`demo_guide.TOPICS`, so fixing a sentence is editing a list rather than laying
out Perspective JSON, and it is a project resource, so EAM carries it to the
edges with everything else. `topic_for_tab()` maps the console's tabs, so the
button hides itself on Architecture rather than opening an empty guide.

`demo_guide` and the two `Guide/` views are **duplicated into all three
projects**, for the same reason `demo_styles` is: inheritance is single-parent
and `Themes` carries style classes only, no script libraries. `make validate` compares the copies
byte-for-byte, because a guide edited in one project reads as correct wherever
you happen to look and wrong on the page the customer opens.

### What is deliberately NOT on the page

**The "2 demos running at once" warning is gone** (24/08/2026). It fired on a
case that is legitimate -- two demos at once is sometimes exactly the point --
it rendered as a status badge rather than as advice, so it read like something
being reported rather than suggested, and it was occasionally wrong about which
demos counted. More to the point, the bar and the rig strip now show the RAM,
the free memory and the load, so the *facts* are on screen and the page does not
also need an opinion about them. A warning that fires on the normal case is one
nobody reads by the third time.

### Readiness on screen

The bottom band answers the question `make verify-demos` answers in a terminal:
**would the started demonstrations actually work if you clicked through them
now.** One green line when they would; the failures and their exact repair
commands when they would not.

```
make verify-demos                       the full run, in a terminal
scripts/verify-demos.sh --json          the same run, as a document
```

**It runs `verify-demos.sh`, it does not reimplement it.** The checks need
`docker exec` and the gateways' REST API from the host, and a Perspective script
has neither — so `wd-control` runs the script and the page reads the result.
That is the same rule the console already follows for starting a stack: one
implementation, used by the terminal and the page alike, so the two cannot tell
a customer different stories about the same machine.

`--json` funnels through the two helpers every check already calls, `pass` and
`fail`, plus a `SECTION` variable set at each section boundary so a result can
be attributed to a demo. Two details worth knowing if you touch it:

- **Results are accumulated as TSV and converted by `python3` at the end.**
  Quoting a shell variable into valid JSON by hand is a bug waiting for the
  first apostrophe, and every one of these messages is prose.
- **In `--json` mode the document is stdout and everything else is stderr**, so
  `--json 2>/dev/null` is exactly parseable. That is done once, by moving the
  real stdout to fd 4 — `say`, `ok` and `dim` come from `lib.sh` and are shared
  with every other script, so they must not learn about this mode.

**It is a cached snapshot with a button, not a live reading, and it says how old
it is.** A full run takes tens of seconds and shells into every gateway; putting
that on the page's 3-second poll would load the rig for no purpose but to
describe the load on the rig. The age is always on screen and *"never checked"*
is a state rather than a blank — a readiness panel that looks live and is twenty
minutes stale is the same failure as counting stored history rows and calling
the road healthy, one level up.

`POST /verify` fires and returns 202, like every other action here: the page is
already polling, so blocking the click would give a frozen button for the same
information arriving later.

**But unlike every other action, it writes NO status line, and that is the
rule rather than an omission.** The status band at the foot of the page holds
whatever an action last returned *until another action replaces it* — it has no
clock and nothing clears it. The readiness panel four rows above already says
*"checking readiness — this takes a few seconds"* and then replaces itself with
the verdict, so a status line here is the same sentence twice while the check
runs and a lie afterwards: measured on the work machine 25/08/2026,
*"checking readiness..."* was still on screen beside a finished result reading
*"1 problem of 23 checks, checked 6s ago"*.

So: **an action may only write a status line for something nothing else on the
page reports.** Start and stop still do — their progress row is gated on a job
being in flight and says nothing about a refusal — but anything with a panel of
its own stays quiet and lets the panel speak, or the page carries two accounts
of one thing and keeps only one of them up to date.

**Readiness is only meaningful for a demo that is running**, which is why it
cannot be the band that fills the page. With core-only there is nothing to
check, and the panel says so in those words — *"core ready — 7 checks. No demo
started, so none was checked"* — rather than showing a green tick that would be
a lie about scope. The rig strip above carries the always-true content; this
band is the one that expands only when it has something to say.

### Busy and ready, per demo: the `/state` contract

Every entry in `GET /state`'s `demos[]` carries four keys (plus one), added
18/09/2026 for the guards pass. Everything that was there before is unchanged.

| Key | Type | Meaning |
|---|---|---|
| `busy` | bool | A job is in flight. **True on every card**, not only the subject's: the job lock is one per machine, so any Start/Stop pressed now is refused with 409. |
| `busyWhat` | string | The job's own words, `"starting eam"`, or `""`. A sentence a disabled button can show. |
| `ready` | bool | Would this demonstration work if you clicked through it now. **Not** `live`, and not container health. |
| `readyWhy` | string | Always a sentence: `"not started"`, `"starting eam"`, `"ignition-edge1, ignition-edge2 are not running"`, `"ignition-edge3 is still starting"`, `"ignition-edge1 is stuck in COMMISSIONING -- …"`, `"checking whether it is ready"`, a failed check's own words, or `"ready -- 25 checks passed"`. |
| `readyAt` | epoch | When the verdict behind `ready` was taken; `0` when none has been. So a snapshot can carry its age. |

```json
{"id": "eam", "live": "running", "busy": false, "busyWhat": "",
 "ready": true, "readyWhy": "ready -- 25 checks passed", "readyAt": 1789695951}
```

Also new: `job.demos` (the ids the job is acting on), `job.warnings`, a
`done_with_warnings` job state, and `refused.why` / `refused.blocked_by` and a
`why` on every 409 body: *"stopping eam was ignored: starting eam is still
running, and this rig does one job at a time."* The page may show `why` as it
stands.

**How `ready` is decided, in order, and why it is cheap.** `/state` runs
nothing: every step below reads something already in memory.

1. The job is acting on this demo → not ready, `busyWhat`.
2. No stacks up → `not started`. Some down, unhealthy or starting → says which.
3. **A gateway that is healthy but not serving** → not ready. The containers'
   healthcheck is `health-check.sh -s RUNNING`, which passes all through
   COMMISSIONING (finding 5), so `wd-control` asks each running gateway's
   `StatusPing` itself, on its own 15-second clock (`ping_loop`). `wait_ready`
   makes the same check during a reconcile, and gives up on COMMISSIONING at
   once, because that state never clears on its own.
4. Otherwise it is the **readiness run's verdict**: `verify-demos.sh --json
   --quick DEMO=<ids>`, and a demo is ready when its own section and `core`
   both pass. The verdict is pinned to a fingerprint of the demo's stacks and
   whether their gateways are serving, so a shape change retires it.

**When the readiness run runs:** at the end of every reconcile (finding 7:
`verify.at` was 0 on this machine, and the panel was opt-in); at start-up for
the demos already wanted; and lazily, when a poll finds a serving demo with no
verdict for its current shape, or one older than `READY_TTL` (10 min). It is
one run per batch, never two at once and never during a job. A stale verdict
**stays up** while its replacement runs. A tab that closed itself in the middle
of a demonstration because a cache expired would be the worse lie.
Measured: 42 s for a running demo, 61 s for a stopped one. That is why
there is no clock behind it.

**`/state` is single-flight with a 2-second shared cache.** During a readiness
run, with host load at 29-47, concurrent polls piled up and `/state` hit the
30 s curl limit. The thread dump (`docker kill -s USR1 wd-control`) showed no
lock waits, only a dozen threads each building the same document. Shared, the
worst poll measured during a run was 0.36 s.

**A stopped gateway's chip lands on an explanation.** Gateway chips point at
`/_wd/login`, which nginx proxies to `wd-control`, so the proxy's down page
never gets the chance, and a bare `502 could not reach the gateway` would be all
anyone saw. `_login` checks first. If the stack is
stopped, it returns `503 The EAM Demo is not running`, naming the demo, the
Demos tab and `make demo-start DEMO=eam`. If the gateway is up but not serving,
it returns `503 … is still starting`, which reloads every 10 s. If it is stuck
in COMMISSIONING, it returns `503 … is waiting to be commissioned`.

**`redundancy`** (22/09/2026) is the Redundancy demo's changeover recorder,
`control/redproof.py`: which half each poll saw in charge, and the last ten
changeovers with their timing and seconds missing. `POST /redundancy/request
{"kind": "handover"|"stop"}` notes that one was asked for; unauthenticated, it only
annotates the next changeover. Shape and measurements: docs/REDUNDANCY.md, *The proof*.

### CPU: load average, not a percentage

RAM decides whether a demo can be **started**; CPU explains why the machine is
unresponsive **right now**. They need different treatment, and only one of them
belongs on a meter.

The failure this stack actually suffers is two Ignition JVMs classloading and
scanning at once — measured at 262% and 196% together, load average over 25, and
the then broker's (EMQX's) five-second healthcheck timing out five times in a
row so a perfectly good broker was marked unhealthy. Nothing was wrong with the
broker; the machine had nothing left to give it.

**That is already prevented structurally, and the number does not do it.**
`control/service.py`'s `wait_ready` blocks the reconcile until each stack is
*healthy* before starting the next, which is what "start the hub before the
spoke" was always supposed to mean — `ORDER` in `stack.meta` only ever ordered
the launches, and a launch is over in a second.

So the pill is **one-minute load average per core**, not a CPU percentage:

- A percentage would flash red during exactly the minute when high CPU is
  correct, and people learn to ignore a warning that fires on the normal case.
- Raw load is meaningless without its denominator — 4.0 is a busy machine
  working fine on eight cores and a machine in trouble on two. It is the same
  mistake the RAM figure made before it grew one.
- It warns above **1.5** per core rather than 1.0, because a gateway starting is
  legitimately CPU-bound for about a minute.
- `/proc/loadavg` read inside `wd-control` is not namespaced, so it is the
  host's figure on Linux and the Docker VM's under Docker Desktop — the right
  one either way, being where the containers actually run. Same source family as
  the RAM total, and the same caveat applies.

---

## THE WIRE's feed: an MQTT listener in wd-control

The MQTT tab's WIRE panel shows what the hub's witness route
(`/system/webdev/GatewayAdmin/sparkplug/wire`) has been sent. MQTT Distributor
has no rule engine, so `control/wire.py` feeds it from inside `wd-control`: it
subscribes to `spBv1.0/AlarmDemo/#`, `spBv1.0/STATE/#` and `notify/#`, and POSTs
each message as `{"topic", "payload_b64", "qos"}`.

| what | how |
|---|---|
| client | `paho-mqtt` 2.1.0, in the toolbox image (`tools/toolbox/Dockerfile`, image tag 2) |
| brokers | `WD_WIRE_BROKERS`, default `ignition:8883,ignition-backup:8883`. Tried in order, first to accept wins. The standby runs no broker, so this follows a hub failover |
| TLS, login | the repo CA (`stacks/npm/certs/rootCA.pem`); `MQTT_PASSWORD` (and `MQTT_USER`, default `ignition`) from the root `.secrets.env` (`WD_WIRE_CREDS`), Distributor's one user. Read at each connect, never logged |
| posts to | the gateway whose broker it is on, `:8088`, which is the active half. `WD_WIRE_POST_URL` overrides |
| off | `WD_WIRE=off` in `stacks/wd-control/.env` |

It runs in two threads of its own and shares only a snapshot with the HTTP
service. A slow or dead hub fills a 1000-message queue that drops the oldest
message and counts it; the page is never held up. It logs one line per change
of state (`docker logs wd-control | grep ^wire`), not one per message.
`/state` carries it as `wire`:

```json
{"connected": true, "broker": "ignition:8883", "since": 1789721647,
 "state": "connected to ignition:8883", "postTo": "http://ignition:8088/...",
 "received": 30, "forwarded": 30, "dropped": 0, "rejected": 0, "queued": 0,
 "hubReachable": true, "lastError": ""}
```

`rejected` counts messages the hub answered with something other than `ok`.
`dropped` counts queue overflow, and messages more than 30 s old by the time
the hub answered again.

Measured 18/09/2026, the listener on EMQX beside the rule over the same 120 s:
DDATA 224 and 224, NOTIFY 5 and 5, edge timestamp to POST median 7 ms, max
36 ms. On the hub's Distributor: 20 test publishes, 20 forwarded. With
`wd-control` cut off `backbone`, it noticed within about 15 s (keepalive 10 s)
and was back 4 s after the network returned.

**Across a real hub failover** (18/09/2026, a planned handover and hand-back,
`make redundancy-failover` twice): the broker moved at 21:04:31; the listener
logged `lost ignition:8883` at 21:04:32 and `connected to ignition-backup:8883`
at 21:04:33, and posted to `ignition-backup:8088`. Back again: lost
21:06:33, connected to `ignition:8883` the same second. Received and forwarded
stayed equal throughout (3591 / 3591 at the end), 0 dropped, 0 rejected, and
the Sparkplug strip on whichever half was active read *2 of 2 edges live,
1.7-1.9 msg/s*.

## Cutting an edge off MQTT: `/mqtt/cut`

The MQTT demo's *Cut Edge 3 link* and Store & Forward's MQTT break need "the WAN to
the broker went away" for one edge. Distributor has no per-client kick (a user
change restarts the whole broker, MQTT-DISTRIBUTOR.md T-D12), so the cut is the
network: every MQTT client reaches the broker on `wd-mqtt`, a network that
carries MQTT only, and `docker network disconnect wd-mqtt <edge>` takes that
road away while `backbone` stays, so the edge's observer and the console keep
answering. The gateway can't run docker; `wd-control` can
(`control/mqttcut.py`).

```text
POST /mqtt/cut      {"stack": "ignition-edge3", "seconds": 60}   X-WD-Token: <token>
POST /mqtt/restore  {"stack": "ignition-edge3"}                  X-WD-Token: <token>
```

| what | how |
|---|---|
| token | the only authenticated calls on wd-control: `WD_CONTROL_TOKEN` in `.secrets.env` (made by `make env`, read per request), installed on the hub as the `wd-control-token` secret by `scripts/ign-secrets.sh`. 403 without it, 503 if wd-control has none |
| who can be cut | `WD_MQTT_CUTTABLE`, default `ignition-edge2,ignition-edge3,ignition-edge4`; never a broker half |
| refusals | 409 with a plain-words `why`: already cut ("comes back in 42s"), not running, not on `wd-mqtt`, or a reconcile job is running (the console's one-job latch). Restore is never refused as busy |
| the deadline | written to `/state/mqtt-cuts.json` **before** the disconnect; a loop restores anything overdue, including at start-up, so a restart of wd-control or the gateway never leaves an edge off the broker |
| the reconnect | `docker network connect --alias <compose aliases> wd-mqtt <edge>`, the aliases read from the container before the cut |
| `/state` | `mqtt`: `cuts` (per stack: `until`, `secondsLeft`, `since`, `offNetwork` -- docker's own view), `onNetwork` (docker's member list, sampled every 5 s), `last` |

The page side: `demo_control.mqtt_cut` / `mqtt_restore` / `mqtt_cuts`.
`sf_demo` and `sparkplug_demo` read the cut from `mqtt_cuts()` and work out the
seconds from `until`, so the card, the countdown on the button and the refusal
all say the same number; a wd-control that isn't answering is shown as "not
known", never as "nothing is cut".

Measured 18/09/2026, click-tested in the page (keepalive 10 s):

| | cut → broker's Last Will (NDEATH) | page shows cloud Offline | Restore → NBIRTH | inside the cut, stored with original timestamps |
|---|---|---|---|---|
| Edge 3 (Sparkplug, 40 s cut) | 15.7 s after the click | +17.3 s (3 s poll) | 3.4 s | 41 heartbeats, 0 missing (111 of 111 over the 2 min window) |
| Site 2 (Store & Forward, 40 s cut) | 15.4 s | card: LINK CUT at +2.7 s | 2.7 s | 40 FlowRate values, largest gap 2.0 s |

A 10 s cut with wd-control stopped for 20 s across its deadline was restored
2 s after wd-control came back ("found 1 cut(s) from before a restart").

---

## Things that will bite

**A stack "started" is not a stack ready.** A gateway reports `Up` within a
second and serves nothing for another minute. So the card has a `Starting`
state of its own, separate from `Running`, and a demo mid-reconcile says
Starting rather than Incomplete in alarm red at the exact moment everything is
going right. Since 21/09/2026 it also stays Starting after the reconcile while
the readiness verdict says a gateway is "still starting" or "up but not serving
yet" (`demo_control._still_coming_up`): the rail's badge said Running over a
banner saying not ready, and a first-time reviewer read the pair as a
contradiction.

**The console lives at `https://console.test`.** Every other `.test` name
belongs to an optional demo, so on a core-only machine they all return 502 —
except `ignition.test`, the redundant *pair's* front door, which falls back to
the hub while the Redundancy demo's proxy is down (REDUNDANCY.md, *The pair's
front door*). Before this, the one gateway that is
always up was the only one with no name at all, reachable solely by host port.
Hitting any stopped name now gets a page naming the demo that starts it rather
than a bare 502; see `docs/ARCHITECTURE.md`.

**Each demo's RAM cost is measured, not declared.** The question a card has to
answer is "what will this cost me", and the demo being asked about is usually
stopped — which is exactly when `docker stats` has nothing to say. So
`wd-control` samples every stack while it runs and remembers the figure in
`/state/memory.json`; a stopped demo shows a real number from this machine, and
a stack that has never run here says `not measured yet` rather than guessing.

A figure typed into `demos.json` would have been the obvious alternative and
would have gone quietly wrong: this stack's own hub went from 4.6 GB to 774 MB
in one afternoon of tuning.

Two details carry the honesty:

- a sample is only believed after the stack has been up **150 seconds**. An
  Ignition gateway reports about 300 MB thirty seconds in and several times that
  once it has loaded its projects, so an early sample records a number nothing
  can be planned with;
- the stored figure is **replaced, not maxed**. A high-water mark can never come
  back down, which would have frozen that 4.6 GB reading for ever.

Stacks shared between demos are counted in each — the card answers "what does
this demo need", not "what would starting it add". The `Shared` badge and the
stacks line are what say the cost is already being paid.

**Which containers are ours is answered by a label in the compose file**, and
by nothing else. Every service carries one:

```yaml
labels:
  au.gaskony.wd.stack: "ignition-edge1"
```

The two obvious keys are both wrong. The **compose project name** was wrong in
both directions on one machine at once:

```text
ignition-module-testing   project `ignition`      a different repo entirely
ignition (ours)           project `wd-ignition`   because its .env renames it
```

— and that `.env` is gitignored and per machine, so the project name a stack
ends up with is not even stable between two checkouts. The **working-directory
label** is closer but records the path the *client* saw, so a stack started
from the host and the same stack started from the console look different. It
survives only as a fallback for containers started before the label existed.

`make validate` requires the label on every service, and fails loudly if one
carries another stack's name — the copy-paste mistake, which is worse than a
missing label because the containers get attributed to a demo that then reports
itself running when it is not.

**`make verify-demos` follows the console.** It checks the demos you have
started and nothing else, because it asks `wd-control` which those are. On a
core-only machine that is three container checks and a trial, not a dozen
failures advising `./wd up` — which would have been the console's own advice
turned inside out. Override it when the console is what you are debugging:

```bash
make verify-demos DEMO=eam           # just that one, whatever is running
make verify-demos DEMO=all           # every demo, the old sweep
```

If `wd-control` cannot be reached it checks everything, on the grounds that a
readiness tool should over-report rather than quietly pass a demo nobody looked
at — and the unreachable console is itself reported, since it is core.

## Testing it end to end

`scripts/e2e-demos.js` drives the real page in a browser — clicks, not API
calls — and checks the result with `docker ps` rather than by asking
`wd-control`, which is the thing that told the page in the first place. A
control plane that is confidently wrong passes a test that trusts it.

```bash
ln -s /path/to/a/checkout/with/node_modules node_modules   # playwright 1.62+
node scripts/e2e-demos.js
```

It starts real stacks including two Ignition gateways, takes about fifteen
minutes, and returns the machine to core-only afterwards — including when an
assertion fails, because an aborted test that leaves two gateways running costs
the afternoon it was meant to save.

What it covers: the frame renders six tiles of which exactly one per demo
carries a button; Start brings a demo to Running
and the containers really are up; a demo's RAM cost appears; a second click
while a job runs is refused **and changes nothing**; stopping one demo does not
take a shared stack out from under another; `Stop every demo` leaves the core
up; and the page reports its control plane being down instead of rendering
nothing.

**The harness checks itself first**, and that is not ceremony. The first
version found a card by walking up from its button until an ancestor's text
matched the demo title — and that ancestor is eventually the container holding
*all* the cards, so every title matched every button. "Start store-forward"
started whichever demo was listed first, and every assertion afterwards was
about a demo nobody had asked for. Most of them would have passed. The card boundary is now
structural and the run asserts that each title identifies exactly one card
before it clicks anything.

That boundary is not "the repeater's own children": the repeater's children are
*tiles*, and only some of them are cards. The harness counts the two separately
on purpose — six tiles, `demos.length` of them carrying a button — because that
pair is exactly what a slot leaking a Start button would break, and counting
only one of them would let it through.

**Editing `control/service.py` needs a restart.** The repo is bind-mounted, so
the file changes immediately and the running process does not:
`make restart STACK=wd-control`.

**The control plane holds the Docker socket**, which is root on this machine,
and it is unauthenticated on `backbone`. That is a deliberate choice for a
demonstration stack on a laptop and the alternative to putting a credential in
a project resource. It is not published to the LAN by the proxy. Do not copy
that decision to anything a customer runs.
