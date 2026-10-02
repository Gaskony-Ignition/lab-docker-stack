# Store & Forward

Cut an edge off from the Store & Forward page, watch it keep collecting, let
the link come back, and the trend fills its own gap in — the buffered values
arrive carrying their original timestamps.

Measured, each across an outage driven from the page:

| cut | samples recovered | largest gap |
| --- | --- | --- |
| Site 1 alone (Gateway Network), 60 s | 80 | 3.0 s |
| Site 2 alone (MQTTS), 60 s, on EMQX | 80 | 1.5 s |
| both at once, 60 s, on EMQX | 143 / 152 | 3.0 s / 2.8 s |
| Site 2 alone, 40 s, on Distributor (network cut, 18/09/2026) | 40 inside the cut | 2.0 s |

The edges sample once a second, so a gap of about a second is the floor and
sixty would mean the data was lost. The hub's view freezes for the whole minute
and then jumps — 64 rows arrived in one 12-second sample on the Gateway Network
edge, 69 on the MQTT one — which is the backfill landing in the past.

```bash
make sf-setup     # historian + history guard + broker + both edge roads (idempotent)
```

## What it demonstrates

Two edges reach the hub by **different roads**, and each buffers with its own
machinery. Break either and it keeps collecting; restore it and the backlog
arrives carrying its original timestamps, so the trend is continuous afterwards.

| | Site 1 | Site 2 |
| --- | --- | --- |
| transport | Gateway Network → `ignition:8060` | Sparkplug B to the hub pair's MQTT Distributor, `ssl://mqtt-master:8883` then `mqtt-backup` on `wd-mqtt` ([MQTT-DISTRIBUTOR.md](MQTT-DISTRIBUTOR.md)) |
| live values | remote tag provider `[Edge1]` | MQTT Engine mirror |
| buffering | the edge's own historian, synced to the hub's | MQTT Transmission's history store |
| the break | the edge cuts itself, on a deadline it owns | wd-control takes the edge's container off `wd-mqtt`, on a deadline it owns |
| restore | only the edge can, when its deadline lapses | Restore now (wd-control reconnects it), or wait for the deadline |

That asymmetry is the point: the same demonstration, told twice, by the two
mechanisms a real deployment actually has.

**One edge, one transport — and that has to be enforced, not assumed.** Both
edges carry the Transmission module, and it ships *enabled*, so Site 1 published
Sparkplug as well as using the Gateway Network for six weeks: the hub's MQTT
Engine mirrored a second edge node nobody mentions and every sample was filed
twice under two driver rows. Nothing failed, which is why nobody noticed —
cutting the Gateway Network link still emptied the Site 1 card and still gapped
the trend, because both read the GAN road, while the MQTT copy kept flowing
underneath and quietly made "cut Site 1 and it stops reaching the hub" untrue.
`sf-arm.sh` now arms the transmitter only where `SF_TRANSPORT=mqtt` and
**disables** it wherever the road is `gan`, every run.

The page (`GatewayAdmin` → Store & Forward) is the console's shared left rail
(DEMO-CONSOLE.md, *One rail on every demo tab*) — each site's state, where the
history is stored, how long a cut lasts, Cut Site1 / Cut Site2 / Cut both /
Restore now — beside one sentence, **the diagram**, and the trend.

**The diagram** is the tree of gateway cards: the hub and its backup, the two
roads as connector chips (Gateway Network, MQTT over TLS), and an edge card per
site with its live values. It takes the top half of the page and the trend the
bottom half. The diagram is 442px at full size, so on a short screen it is
scaled down as a whole with CSS `zoom`, worked out from the viewport height —
about 55% at 1366×640, full size at 1080 high — rather than scrolled or cut.
**Hide trend** (beside the sentence) gives the diagram the whole area, up to
1.6× and capped by the width so the cards never crowd; **Show trend** splits
it again. The page opens with the trend showing.

It was swapped for a 96px strip on 21/09/2026, to give the trend the height at
1366×640, and brought back on 30/09/2026: the diagram is what the presentation
is built around.

**The trend** is the last 10 minutes of each edge's `FlowRate` as recorded at the
hub. Its data lands on the
view once (`custom.trend`), and the same data decides whether the chart or
*Nothing yet* is in the layout: before, a stopped demo or a fresh machine drew
an empty frame with no axes and no words — a hole in the one card the tab is
for, which read as broken.

## Arming it — three settings, and the third is the trap

`scripts/sf-arm.sh` does all of this and refuses to report success until the
gateway's own log agrees. By hand it is:

1. The history store resource must be **`enabled`**. It ships disabled, and
   `enabled` lives in `resource.json`, so the store's `config.json` looks
   perfectly healthy either way.
2. The transmitter must **name** that store in `historyStore`. Absent by
   default; with none, MQTT Transmission builds a `NoOpHistoryStore`.
3. The transmitter must then be **re-saved**.

Step 3 is the one that will catch you, and it cost most of a day. The flush type
is computed once, when the `Transmitter` object is built, from the store's
`enabled` attribute *as it stood at that moment*:

```java
HistoryFlushType t = NONE;
if (storeResource.getAttribute("enabled")) {
    t = inOrderHistory ? IN_ORDER : ASYNC;
}
```

Enabling the store restarts the transmission *client*, so the log starts saying
`historyEnabled=true` and everything looks armed — while the `Transmitter` still
holds the `NONE` it computed while the store was disabled. And `NONE` does not
mean "flush in no particular order", it means **do not store and forward**:

```java
if ((!isConnected() || !isConnectedToPrimaryHost())
        && historyFlushType == NONE) {
    return;      // the payload is DROPPED, logged at debug only
}
```

So the entire difference between buffering and silently discarding everything is
one word in one line at connect time:

```text
Handling transition to online with ... historyEnabled=true, historyFlushType=ASYNC
                                                                          ^^^^^ not NONE
```

A 45-second outage leaves **zero** samples in the historian with `NONE`, and
recovers **45 of 45** with `ASYNC`.

## The Gateway Network road carries TWO things, and only one is history

This is the distinction that cost the most, because getting one half right makes
the other half's absence invisible.

| | mechanism | what breaks when it is missing |
|---|---|---|
| live values | a **remote tag provider** the hub mounts the edge as, `[Edge1]` | the Site card only |
| history | the edge's **sync settings** forwarding into the hub's historian | the trend only |

They share nothing but the physical link. So a hub with the sync settings and no
provider draws a **perfect trend with both edges' lines** while the Site 1 card
reads `OFFLINE`, three dashes and `LAST VALUE nothing yet`, and the hub counts
`1/2 EDGES streaming`. Every check that counts stored rows says the road is
healthy, because for history it *is*.

**That happened here, on both machines at once, and the cause is worth
remembering: the provider was hand-made in the gateway UI and no script
recreated it.** It was gateway state (rule 4) that nothing owned, so it survived
right up until the first rebuild from scratch and then silently did not come
back. `sf-gan-history.sh` now creates it, `GAN_PROVIDER` in the edge's
`stack.meta` names it, and `make validate` cross-checks that name against the
`[Edge1]` in `sf_demo.TRANSPORTS` — the two places it is written.

> The general lesson, and it applies to every demo here: **anything configured
> by hand in the gateway UI is one rebuild away from being gone.** If it is not
> in a script that bootstrap runs, it does not exist.

Getting the resource shape right is a `GET` rather than a guess:
`/data/api/v1/resources/type/ignition/tag-provider` lists the `REMOTE` extension
point with its `defaultSettings`. `serverName` is the **server** name (the plain
gateway name — the same trap as `remoteServerName` below), and
`remoteProviderName` is the provider on the *edge*, which is `edge_stream`'s
`PROVIDER = "edge"`.

### A cold start routinely leaves the sink faulted

Starting the whole stack is not the same as bootstrapping it. The edge comes up
before the hub is ready to accept remote storage, the sink caches that failure —
and it only retries when a **setting changes**, so it stays faulted indefinitely
while every container reports healthy and the live values flow normally.

Seen on both machines, twice each. `make verify-demos` catches it (two refusals
in three minutes, distinguishing a genuine fault from the single one a
deliberate cut produces) and names the repair:

```bash
make sf-gan-history      # idempotent, ~30s, toggles the setting so the sink retries
```

`stack.sh up` now says so on the way out. The rule is: **start, then check** —
never start and assume.

## The history half — three settings, none of them where you'd look

`scripts/sf-gan-history.sh` does all of this and verifies from the gateway's own
log and the hub's historian tables. By hand, and every step fails silently:

**1. An Edge gateway has exactly one historian, and you do not create it.** Its
name comes from the `ignition/edge-system-properties` singleton (`historianName`,
default `Edge Historian`), and `EdgeHistorianCollection` builds that one instead
of reading the `historian-provider` collection at all. A provider created on an
Edge lists happily over the REST API reporting `enabled: true` and is **never
instantiated** — no start line in the log, no store on disk — so every tag
pointed at it records nothing while looking perfectly configured. That decoy cost
a day; `edge_stream.history_target()` now reads the property instead.

**2. Forwarding is not the provider's `remoteSync` block.** It is the
`ignition/edge-sync-settings` singleton — `remoteServerName`,
`remoteHistoryProviderName`, `remoteHistoryEnabled`. Singletons **read** from
`/data/api/v1/resources/singleton/<module>/<type>` and **write** to
`/data/api/v1/resources/<module>/<type>` with a one-element array; the read path
404s on `PUT` and the write path 404s on `GET`, so each half looks like the wrong
endpoint. The write takes effect immediately — the historian restarts in place
and logs its target, which is the line to confirm rather than the response code:

```text
Historian syncable 'Edge Historian' is starting. Will synchronize data to
remote historian 'Postgres' on server 'Ignition-Standard'.
```

**3. The hub refuses the data by default**, and this is the expensive one. A
gateway grants remote gateways `QueryOnly` on its historian unless told
otherwise — `AbstractServiceDescriptorFactory.DEFAULT_ACCESS` is literally
`RemoteServiceAccessLevel.QueryOnly` — so the edge records fine, syncs every ten
seconds and logs:

```text
DataStorageException: The remote service has reported that it will not accept
storage requests to 'Postgres'
```

Granting it is a **security zone** on the hub carrying an entity policy for
`TagHistoryProvider`:

```json
{ "gatewayNames": ["Ignition-Edge1"],
  "entityPolicies": [{ "entityId": "TagHistoryProvider",
                       "propName": "defaultAccess",
                       "propValue": "QueryAndStorage" }],
  "entityZonePolicy": { "priority": 1 } }
```

**Do not go looking for the Service Security page.** It exists in 8.3.8's
frontend bundle and it is a **mock** — placeholder labels (`Access Level: $test`),
hard-coded profile names, empty selects, and not one API call or submit handler
in the whole chunk. Anything set there is persisted nowhere.

Confirm it landed from Postgres rather than from the page: `sqlth_drv` gains a
row per source gateway *and* provider, so the Gateway Network edge appears
alongside the MQTT one.

```text
 id |       name        |  provider
----+-------------------+-------------
  2 | ignition-standard | mqtt engine
  4 | ignition-edge1    | edge
```

That second driver row is also why **the trend cannot use one path shape for
both edges**. The live tag path works for the MQTT edge, whose tags the hub
historises itself, and returns an empty result for the Gateway Network edge,
whose tags are not the hub's at all — a chart with one line where two were
expected, which reads exactly like an edge that is not sending. `sf_demo`
names the driver explicitly:

```text
histprov:<historian>:/drv:<source gateway>:<source provider>:/tag:<path>
```

## Breaking the link — a network cut, not a gateway setting

The demo needs "the network to the hub went away", per edge, on demand. Three
other routes were considered and rejected:

- **Disabling the edge's MQTT server connection.** That is the edge being
  switched off, not the link failing — and it rewrites a config resource on
  every button press, which is the machinery whose signature mismatch silently
  rolled back every trial reset (see CLAUDE.md). Disabling the transmitter was
  measured too: the cloud learns at once, but nothing is collected while it is
  off (MQTT-DISTRIBUTOR.md T-D12 c).
- **A control tag on MQTT Transmission.** There isn't one. The module's tree is
  `Transmission Info` / `Transmission Control`, with no connection enable.
- **Stopping the container.** It also stops the buffering we are trying to show.

**Taking the edge off the network its broker is on** is the honest version, and
what a customer sees when a WAN link drops. Every MQTT client reaches the broker
(MQTT Distributor on the hub pair) on `wd-mqtt`, a network that carries MQTT and
nothing else; `docker network disconnect wd-mqtt ignition-edge2` removes that road
and leaves `backbone`, so the rest of the console keeps working. The edge keeps
running, keeps collecting, cannot reach the broker, and buffers.

The gateway cannot run docker, so the page asks `wd-control`
(`POST /mqtt/cut`, token-protected; `control/mqttcut.py`, docs/DEMO-CONSOLE.md
*Cutting an edge off MQTT*). Two properties make it safe for a live demonstration:

- **wd-control owns the deadline** and writes it to its own volume before the
  disconnect, so a 60-second break heals itself even if the browser is closed,
  the gateway restarts or wd-control does.
- The card reads the cut from wd-control's `/state`, the same place the deadline
  lives, and a wd-control that is not answering is shown as "not known", never
  as "nothing is cut".

**What the audience sees change**, from MQTT-DISTRIBUTOR.md T-D12 and the
click-test: the values stop at once; the cloud keeps the node Online for about
15 s (1.5 x the 10 s keepalive) and then the broker publishes the edge's Last
Will; the edge itself only notices at its own keepalive, and publishes into
nothing until then, which the Rolling History Buffer covers. After Restore the
edge re-births in about 3 s and the buffered values arrive with their original
timestamps.

**While it is cut, Site 2 alternates between the two brokers, and that is
Transmission, not this repo's configuration** (23/09/2026, Redundancy demo
stopped). Transmission moves to the next server in its list after every failed
connect, every 3 s: in a 40 s cut from 16:32:42 `ignition-edge2` logged `Trying
to connect to target MQTT server 'Hub A Master' at index 0`, then `'Hub B Backup'
at index 1`, and so on, each failing `UnknownHostException` (the cut removes
both names). With the backup stopped, `mqtt-backup` never answers, so the only
cost is that a restore landing on a Hub B turn waits one more retry: the cut
ended 16:33:22, Hub B was tried at 16:33:21, Hub A at 16:33:24, connected
16:33:25. Nothing else moves it: with the link up it stayed on Hub A through
the hub Engine's client restarts at 16:20:12 and 16:30:12. Removing Hub B from
the list would stop the switching and also stop the edge following a hub
failover, so the list stays.

(Until 18/09/2026 the break was a `peerhost` ban plus a client kick at EMQX, with
an EMQX API key installed by `scripts/sf-emqx-key.sh`. Both are retired.)

## Which edge is which

The Sparkplug edge node id is generated by Transmission (`Edge Node e055db`), so
it identifies a node but says nothing about which container it is. Each edge
therefore publishes **its own address** as a
metric (`edge_stream.ADDRESS_TAG`, from `InetAddress.getLocalHost()`), and the
hub matches that against the addresses its EAM agent hostnames resolve to.
Nothing is hard-coded, and an edge rebuilt with a new node id still lines up.

The two edges also **shape their signals differently**, phase and amplitude
derived from that same address. Without it both trace the identical curve and
the trend draws one line with the other hidden exactly beneath it, which makes
it impossible to see that only one of them lost its link.

## The historian

`scripts/sf-historian.sh` creates a `SqlHistorian` provider named `Postgres`
against the existing database connection. Resource type
`com.inductiveautomation.historian/historian-provider`; the extension point is
chosen with `config.profile.type`, settings in `config.settings`.

Backfill lands at the right time for two reasons, both of which have to hold:

- MQTT Engine's **`storeHistoricalEvents`** (on by default) routes a Sparkplug
  metric flagged historical straight to the historian carrying its own
  timestamp, instead of updating the live tag value.
- The SQL historian writes the **value's** timestamp, not the collection time.

**Tag history has to be re-applied, not set once.** MQTT Engine recreates its
tags from every fresh birth certificate, and a recreated tag comes back with
history off — and cutting an edge off and restoring it is exactly what produces
a new birth. `GatewayAdmin`'s `SFHistory` timer sweeps every 30s and writes only
what is actually missing.

## Verifying it for real

The page is the demonstration, but the historian is the proof:

```bash
docker exec postgres psql -U ignition -d ignition -tAc "
with s as (select t_stamp from sqlt_data_1_2026_08
           where tagid=5 and t_stamp > (extract(epoch from now())*1000 - 420000))
select 'largest gap (s): '||round(max(d)/1000.0,1)
from (select t_stamp - lag(t_stamp) over (order by t_stamp) d from s) g"
```

A minute-long outage that buffered correctly leaves a largest gap of a few
seconds, not sixty. Find the tag id in `sqlth_te`.

## The REST shape used throughout

Both `sf-arm.sh` and `sf-historian.sh` write config through the same endpoint
the gateway's own UI calls:

```text
PUT|POST /data/api/v1/resources/<module>/<type>     body: [ {resource} ]
```

The body is an **array**, and the per-name path (`.../<type>/<name>`) does not
exist — it 404s, which reads exactly like having guessed the wrong endpoint.
Reads are `GET /data/api/v1/resources/list/<module>/<type>`.

## Breaking the Gateway Network edge — the edge cuts itself

The MQTT cut has a **third party**, wd-control: it is still there to put the
edge back, and its deadline heals a forgotten cut on its own. The Gateway
Network has no third party.
The link *is* the road, so the hub cannot both break it and keep the means of
putting it back. Two hub-side levers were considered and rejected:

- **Revoking the incoming connection's approval.** A security action rather than
  a network one, and it is precisely how you lose the road you would need to give
  it back.
- **Disabling remote history sync.** Stops the forwarding but not the live
  values, so the card would still read STREAMING while the trend went flat — a
  screen that contradicts itself in the middle of a demonstration.

So the hub **asks, over the link, while it is still up**. `system.util.sendRequest`
reaches the `breakLink` message handler on the edge (inherited from
`Toolbox_Styles`, so EAM carries it to both), and the edge disables its own
`ignition/gateway-network-outgoing` resource and re-enables it when its deadline
passes. Three details make it survivable:

- **The handler only arms the cut.** It is called over the very link it is about
  to break, so disconnecting inside the handler would drop the socket the reply
  travels back on — the hub would see a timeout and report a failure for
  something that worked. Writing a deadline and letting the edge's 1-second timer
  pull the plug puts the reply safely on the wire, at the cost of up to a second.
- **The deadline is a tag, not a module global.** Gateway scripts restart on
  every trial reset — about every ten minutes here — and a restart mid-cut would
  otherwise forget to restore, leaving the edge off the network with no way back
  in but a Designer. A memory tag survives a script restart *and* a gateway
  restart, and `edge_link.tick()` restores from it on the first tick after the
  deadline. That also covers the nastier case: a trial that lapses mid-cut stops
  the timer, and the link comes back as soon as the trial is reset.
- **`enabled` is a resource attribute, not a config field**, so the toggle
  rewrites the attribute and pushes the resource back. Three traps in one call,
  each of which reports a problem that is not the one you have:
  - `getAttribute("enabled")` is declared `Optional<JsonElement>` and hands back
    a **plain Python bool** in Jython, so `getAsBoolean()` raises
    `AttributeError` — swallowed into one log line a second while the page
    showed LINK CUT for a link that was never dropped. Read it through `str()`.
  - The resource reports the **overlay** collection it was read through
    (`local`), not the one that defines it (`core`). Pushing to the overlay is
    refused with `MODIFY illegal: 'ResourceId{…, collectionName=local}' doesn't
    exist` — which reads as a missing resource while the REST API lists it
    happily. Use `getDefiningCollectionName()`.
  - The **signature carries a ResourceId too**, stamped with that same overlay
    collection, so fixing only the resource swaps "doesn't exist" for `MODIFY
    illegal: signature mismatch` — which reads as a stale read and is not one.
    Rebuild the signature against the corrected id, keeping its bytes.

The cut arms within a second of the click (`enabled: false` on the connection
resource), and the edge logs `gateway network link restored` about a minute
after the deadline, unattended.

**The hub is blind for the duration, and the page says so.** It cannot ask a
gateway it has just disconnected how long is left, so it keeps its own countdown
in a memory tag — and Restore, for that edge, reports honestly that the cut
cannot be lifted early because the request would travel over the link it just
cut. A button that claimed otherwise would be reporting success for nothing.

## Audit and alarms — the same two roads, shown side by side

**Show evidence** (beside the page's sentence) swaps the diagram and trend for one
panel per site. Each panel has its road in words, three actions (**Make audit
record**, **Raise fault**, **Clear fault**), and two tables, **Audit** and **Alarms**.
The tables list that edge's own records beside whether the hub holds each one, with a
count line: "12 local, 12 at the hub, 0 missing". Records match on time, action and
target for audit, and on the event id for alarms. The window is the last 2 hours.

| | Site1 — Gateway Network | Site2 — no Gateway Network |
|---|---|---|
| Alarms | Edge sync, built in: `remoteJournalEnabled`, `remoteJournalName = EdgeAlarms` | MQTT (Sparkplug): the transmitter's `alarmEventEnable`, journaled locally in `EdgeJournal` first |
| Audit | Edge sync, built in: `remoteAuditEnabled`, `remoteAuditProfileName = EdgeAudit` | **pulled by the hub** over the REST API (`GET /data/api/v1/audit/log/EdgeAuditProfile`), our script `sf_audit.pull`, every 30 s |
| Lands in | hub audit profile `EdgeAudit` (table `edge_audit_events`), journal `EdgeAlarms` (`edge_alarm_events`) | the same |

**Why Site2's audit is pulled.** Sparkplug B carries tags and alarms but no audit
records, and an Edge's audit log is internal SQLite that no other gateway can open.
Ignition's only built-in way off an Edge is Edge sync, which needs the Gateway
Network. So the hub reads it over HTTP with a **read-only API token**, `hub-audit-reader`
on Edge 2:

- Its level is `ApiReader`, a top-level security level added to the gateway's **read**
  permissions only. Reads answer 200; every write answers 403.
- Its key is held only in the hub's (and backup's) `wd` secret provider as
  `edge2-audit-token`, and it is never printed.
- The header is `X-Ignition-API-Token: <token name>:<key>`; the Bearer form answers 401.
- Pulled rows keep the edge's own timestamp and carry
  `originating_system = source:Ignition-Edge2:/road:REST pull`.

**The hub refuses storage until told.** Edge 1's first sync logged "Remote server
[Ignition-Standard] does not accept storage on audit profile 'EdgeAudit'", and the
same for the journal. The fix is the same security zone that history needed,
`EdgeHistorySync`, granting **`AuditProfileProvider`** and **`AlarmJournalProvider`**
`QueryAndStorage` next to `TagHistoryProvider`. Those ids are not shown anywhere in
the UI. They were read from `gateway-api-8.3.9.jar` (`*SecurityMarker` classes).

**Store and forward, proven 01/10/2026.** Cut both, then make an audit record and
raise and clear the fault on each edge during the cut:

- 20 s in, Edge 1's note was not at the hub, and Edge 2's was (its HTTP road was not cut).
- After the restore, every record from both edges was at the hub: alarms 2 of 2 each,
  notes 1 of 1 each.

**The console's actions use HTTP for both edges.** They go through the edges'
`sf/action` and `sf/local` WebDev routes (an `X-WD-Token` from `sf-token`), so nothing
about Site2 needs the Gateway Network. Provisioning still does: the Site projects reach
the edges by EAM push.

Set up by `scripts/sf-audit-alarms.sh`, which reads first and writes only what
differs. It runs from `converge.sh` and `make sf-setup`.

## Still open

- **Edge 1's `gateway startup` audit record never reaches the hub** by Edge sync
  (01/10/2026: the 21:46:51 one is the "1 missing" in Site1's audit count). Not yet
  known whether Edge sync skips that action or it is written before sync starts.

- ~~The page infers each edge's committed theme from the push record~~ — done
  07/08/2026. `edge_stream` publishes the committed pack as a `Pack` tag, which
  both roads carry without a new mechanism: the hub mounts the Gateway Network
  edge's provider, and the MQTT edge's tags are published as Sparkplug metrics.
  The EAM page reads it back and falls back to the push record only when the tag
  cannot be read. Reading beats remembering — a push record is a promise that
  says nothing about whether the push landed, and cannot notice a theme changed
  any other way.
- ~~Nothing shows the **buffer depth** on the edge~~ — investigated 07/08/2026
  and **it cannot be done live**, which is worth writing down so nobody tries.
  Each edge now has exactly one transport by design, so the only channel that
  could carry a queue-depth reading is the very one being cut: while the edge is
  buffering, nothing it publishes can reach the hub, and by the time anything
  can, the buffer has already flushed. Neither road exposes a depth to a third
  party either — Transmission's in-memory store publishes no queue tag, and the
  Edge historian's unsynced count is not surfaced.

  The honest substitute is what the demonstration already shows: the **recovered
  sample count** after reconnection, which the hub can compute from its own
  historian, and which is the number that actually proves the claim. The trend
  filling its own gap is the visual form of the same thing.
