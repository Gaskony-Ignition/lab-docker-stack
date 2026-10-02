# MQTT demo

*(was: Sparkplug alarms demo; the demo is about MQTT with Ignition -- Sparkplug
B is the payload.)*

The demo has two purposes, and they want different things. The **page** shows a
customer, visually, what Ignition does over MQTT: six guided scenarios that fit
a 1366x640 laptop window (*The page* below, and *What we measured* for the
evidence behind each claim). The **gateways** are an engineer's reference
setup: open them, change the configuration, and put it back with
`make mqtt-reset`. *The reference setup* below is what to read before opening
one.

```bash
make sparkplug-setup      # edge project, token, tags, transmitters, hub Engine (idempotent)
make sparkplug-deploy     # just push the edges' Edge project and apply it
make mqtt-reset           # back to default: the cut, the fault, the UDTs, the settings
make mqtt-udt-check       # does each edge's UDT still match what the cloud holds? (read-only)
make mqtt-udt-rollout     # Fix 2, the vendor's UDT rollout procedure (not a customer visual)
```

The two things a customer asks about -- what an outage does to their alarms, and
what a UDT change on one edge does to the fleet -- are answered in
*What to tell a customer* below, each pointing at the scenario that shows it.

## The reference setup

This is what to read before opening a gateway. Every row is one setting: its
value on this rig, where to find it in the 8.3 gateway web UI, and the script
line that writes it -- so a setting you change can be compared with what the
repo intends, and put back.

**Reset:** `make mqtt-reset` -- the broker link, the pump fault, the UDTs on
both edges and in the cloud, then every setting below re-asserted by
`sparkplug-setup.sh`, then `verify-demos DEMO=sparkplug`. It reads before it
writes, so it prints "nothing to do" for whatever you did not touch.
**Vendor rollout:** `make mqtt-udt-rollout` (see *Recommendation for UDT
changes*).

UI paths are verified against 8.3.8: each gateway's own
`/data/app/navigation` for the menu names and mount URLs, and the pages
themselves for their tab names. A path this could not confirm says so in the
cell. Values were read back from `/data/api/v1/resources/...` on the running
rig the same day. Line numbers are of the file as it stands; the setting name
beside them is what to grep for if they move.

### Each edge -- MQTT Transmission (`ignition-edge3`, `ignition-edge4`)

Everything an edge publishes is decided here. There is no Gateway Network and
no EAM on these gateways: this module is the only road out.

| setting | value | in the gateway UI (8.3) | written by |
|---|---|---|---|
| server set | `Default`, `primaryHostId` **`IamHost`** | Connections → MQTT Transmission → Configuration (`/app/mqtt-transmission-resources`) → **Sets** | `ign-mqtt.sh:51` (`EDGE_SET`), `:49` (`HOST_ID`), sent at `:134` |
| servers, in list order | `Hub A Master` → `ssl://mqtt-master:8883`, `Hub B Backup` → `ssl://mqtt-backup:8883` | same page → **Servers** | `ign-mqtt.sh:135-136` |
| keepalive | **10 s** on both servers | same page → **Servers** → the server's edit form | `ign-mqtt.sh:52` (`KEEPALIVE`), sent at `:137` |
| RPC client | `rpcCaCertFile` = the broker CA, `rpcUsername` = the broker user, `rpcPassword` set | same page → **Servers** → the server's edit form; the fields are named `rpcCaCertFile` / `rpcUsername` / `rpcPassword` in the resource, *which form section shows them is not verified* | `ign-mqtt.sh:123-124` (the CA and credentials in the payload); checked by `sparkplug-setup.sh:578` (`rpc_state`) |
| server set RPC client | `rpcClientEnabled` **true** | Configuration → **Sets** → the set's edit form, *not verified* | `sparkplug-setup.sh:552` |
| history store | `Default In-Memory Store`, **enabled** | Connections → MQTT Transmission → **History Store** (`/app/mqtt-transmission-history`) | `sf-arm.sh:41` (`STORE`) |
| rolling history buffer | `rollingHistoryBufferEnabled` **true**, `rollingHistoryMaxAge` **60 s** | same page → the store's edit form | `sf-arm.sh:42` (`ROLLING_MAX_AGE`), `:115` |
| transmitter → the store | `historyStore` = `Default In-Memory Store` | Configuration → **Transmitters** | `sf-arm.sh:151` -- written **unconditionally**, because naming the store is the third of store-and-forward's three settings |
| transmitter | `groupId` `AlarmDemo`, `edgeNodeId` `Edge3` / `Edge4`, `tagProvider` `edge`, `tagPath` `AlarmDemo`, `deviceId` empty | Configuration → **Transmitters** → the transmitter's edit form | `sparkplug-setup.sh:497-498`, `:505-506` |
| alarm events | `alarmEventEnable` **true**, `alarmJournalName` `EdgeJournal` | same form | `sparkplug-setup.sh:499` |
| UDTs as Templates | `convertUdts` **false**, `publishUdtDefinitions` **true** | same form, *which section holds the two UDT flags is not verified* | `sparkplug-setup.sh:109-110` (the defaults), applied `:501-504` |

`convertUdts=false` + `publishUdtDefinitions=true` is the pair that puts a real
UDT in the cloud; `SPARKPLUG_CONVERT_UDTS` / `SPARKPLUG_PUBLISH_UDT_DEFS`
override either for an experiment (T8 measures both modes).

### The hub -- MQTT Engine (`ignition`)

| setting | value | in the gateway UI (8.3) | written by |
|---|---|---|---|
| server | `Distributor` → `ssl://localhost:8883`, in set `Distributor` | Connections → MQTT Engine → Configuration (`/app/mqtt-engine-resources`) → **Servers** | `ign-mqtt.sh:141`, set name at `:50` (`ENGINE_SET`) |
| server set | `Distributor`, `primaryHostEnabled` true, `primaryHostId` `IamHost` | same page → **Sets** | `ign-mqtt.sh:140` |
| namespace bindings | `Sparkplug B` and `AlarmDemoNotify`, both bound to set `Distributor` | same page → **Namespaces**, *which tab carries the binding is not verified* -- it is its own resource, `namespace-server-set` | asked for at `ign-mqtt.sh:142`, written by `ign-gw.js:878` |
| alarms in | `enableAlarmEventPublishing` **true** | Connections → MQTT Engine → General Settings (`/app/mqtt-engine`) | `sparkplug-setup.sh:634` |
| commands out | `blockNodeCommands` **false**, `blockDeviceCommands` **false** | same page (both ship **true**) | `sparkplug-setup.sh:634-635` |
| alarm display paths | `alarmDisplayPathType` **`ENGINE`** | same page | `sparkplug-setup.sh:119` (default), applied `:637` |
| custom namespace | `AlarmDemoNotify`: `notify/AlarmDemo/#` → `[MQTT Engine]Notify`, `jsonPayload` true, charset `UTF_8` | Configuration → **Namespaces** | `sparkplug-setup.sh:764` (name), `:771-772` (the values) |

A set with no namespace bound to it subscribes to nothing while every status
reads Good (T-D10) -- which is why the binding is a row of its own, and why
`verify-demos` fails on it.

### The hub -- MQTT Distributor (the broker itself)

The broker is a module on the hub pair, not a separate service. **Every write
here restarts the whole broker**, so each of these is written only when it
differs.

| setting | value | in the gateway UI (8.3) | written by |
|---|---|---|---|
| broker on, TLS only | `enableDistributor` true, `enableTls` true, `securePort` **8883**; `enableTcp`, `enableWebsocket`, `enableSecureWebsocket` all **false** | Connections → MQTT Distributor → General Settings (`/app/mqtt-distributor`) | `distributor-setup.sh:124-125` |
| no anonymous clients | `allowAnonymousConnections` **false** | same page | `distributor-setup.sh:126` |
| the broker's certificate | the gateway's own **web** certificate, signed by the repo CA, SANs `mqtt-master` / `mqtt-backup` | Network → Network Settings → **Web Server** (`/app/network/settings/web-server`) | `distributor-setup.sh:112` (`POST /data/config/ssl/transition/ca-signed-certificate`) |
| users | one user, the generated `MQTT_USER` (`ignition`); the shipped `admin` **deleted** | Connections → MQTT Distributor → **Users** (`/app/mqtt-distributor-users`) | `distributor-setup.sh:164` and `:182`; the delete at `:189` |

### The hub -- the cloud's own resources

| setting | value | in the gateway UI (8.3) | written by |
|---|---|---|---|
| alarm journal | `AlarmDemoJournal`, `DATASOURCE` on `Postgres`, tables `sp_alarm_events` / `sp_alarm_event_data`, pruning 14 days | Services → Alarming → **Journals** (`/app/services/alarming/journals`) | `sparkplug-setup.sh:690` (name), `:735-738` |
| the cloud's own alarm | `[default]SparkplugDemo/<node>/PumpFault`, a **reference** tag on the Engine tag, alarmed `Pump Fault (cloud)`, auto-ack, active + clear → `project:SparkplugCloud:/pipeline:CloudNotify` | Services → **Tags** (`/app/services/tags`) | `sparkplug-setup.sh:849-850`, the tag JSON at `:874-880` |
| the cloud's link alarm | `[default]SparkplugDemo/<node>/Online`, a **reference** tag on Engine's `Edge Nodes/AlarmDemo/<node>/Node Info/Online`, alarmed `Edge 3 offline` / `Edge 4 offline`: **false = Active**, priority **Critical**, auto-ack, no pipeline | Services → **Tags** (`/app/services/tags`) | `sparkplug-setup.sh:893` (the name), the tag JSON at `:910-917`, imported with the PumpFault tags in `configure_hub_cloud` |
| `CloudNotify` pipeline | in project `SparkplugCloud`, with its library `sp_cloud` (*how many blocks it has is not read from the file: `data.bin` is binary*) | Services → Alarming → **Pipelines** (`/app/services/alarming/pipelines`) lists it; the pipeline itself is edited in the **Designer** | the project is deployed by `sparkplug-setup.sh:855`; the resource is `ignition/edge-projects/SparkplugCloud/com.inductiveautomation.alarm-notification/alarm-pipelines/CloudNotify` |
| `SparkplugHistory` timer | gateway scope, **30 s** fixed delay, shared thread -- gives the Engine tags history, so the trend fills in after a rebirth | **Designer** (a project resource, not a web page); Diagnostics → Scripts → **Running** (`/app/diagnostics/scripts/running`) shows it executing | `ignition/projects/GatewayAdmin/ignition/timer/SparkplugHistory/resource.json` |
| the action token | `wd` / `sparkplug-token`, `file` provider, same value on the hub and every edge | Platform → Security → **Secret Providers** (`/app/platform/security/secret-providers`) | `sparkplug-setup.sh:257` (`hub_token`, generated once on the hub), registered at `:282` (`register_secret`) |

### Each edge -- the `Edge` project

An Edge gateway runs exactly one project and it must be called `Edge`. These
are project resources: they are edited in the Designer or in this repo, not in
the web UI.

| setting | value | where | written by |
|---|---|---|---|
| `Simulate` timer | gateway scope, **1 s** fixed delay, own thread -- drives the level, the alarms and the re-provisioning of any missing tag | Designer → the `Edge` project | `ignition/edge-projects/SparkplugEdge/ignition/timer/Simulate/resource.json` |
| `EdgeNotify` pipeline | one Script block calling `sp_notify.notify(event, PIPELINE)`; named on the alarms as `project:Edge:/pipeline:EdgeNotify` | Services → Alarming → **Pipelines** lists it; edited in the Designer | `sp_site/code.py:39` and `:47` (`PIPELINE_REF`); the resource is `.../SparkplugEdge/com.inductiveautomation.alarm-notification/alarm-pipelines/EdgeNotify` |
| `EdgeJournal` | the `LOCAL` journal an Edge gateway ships with -- an Edge has exactly one | Services → Alarming → **Journals** | nothing writes it; `sp_site/code.py:33` is only the name the transmitter and the tags use |
| `AutoScan` timer | rig plumbing: applying a file deploy | Designer → the `Edge` project | `ignition/edge-projects/SparkplugEdge/ignition/timer/` |

### Postgres -- the history guard

| setting | value | where | written by |
|---|---|---|---|
| duplicate-tolerant history | a BEFORE INSERT row trigger on every `sqlt_data_*` partition that drops a row whose `(tagid, t_stamp)` is already there, plus `stringvalue` widened to `text` | not in any gateway UI -- it is in the `Postgres` database | `scripts/pg-history-guard.sh` (`make pg-history-guard`) |

The edges' rolling buffer replays the last 60 s on every reconnect, which is
what makes a hub handover lossless -- and the replay re-sends rows the hub
already stored. Without the trigger Postgres rejects the duplicate and the SQL
historian loses the whole batch, new rows included (T-D11).

## What the demo shows

Two Edge gateways with **no Gateway Network and no EAM** each run a small pump
station and publish it to the hub as Sparkplug B through the hub pair's own
broker, MQTT Distributor (TLS 8883 on `wd-mqtt`; [MQTT-DISTRIBUTOR.md](MQTT-DISTRIBUTOR.md),
*The migration*). The T-sections below were measured on EMQX, the broker until
18/09/2026, and stay as history; where the move changed the answer the section
says so. The hub plays the cloud with MQTT Engine. Nothing crosses between them
except the broker, so everything that happens here happens by Sparkplug and
nothing else:

- tags reach the cloud, and cloud writes to `LevelSetpoint` and `Mode` go back
  as DCMD and come home as DDATA;
- alarms raised at the edge appear in the hub's own alarm status and journal;
- an alarm acknowledged in the cloud shows acknowledged at the edge, and the
  other way round;
- cut an edge off the broker and restore it, and see what the cloud reconciles;
- **the customer question**: two edges, the same UDT on both, change it on one
  of them -- what does Engine do with the definition and with both edges'
  instances? Do they sync, and should they?

### The page

GatewayAdmin's **MQTT** tab, on the console's shared left rail
(DEMO-CONSOLE.md, *One rail on every demo tab*). The rail: the demo's state,
*Edges live* and *Messages*, then **CONTROLS** -- the six scenarios as a
vertical list (**1 Live data**, **2 Alarms**, **3 Cloud to edge**, **4
Outages**, **5 UDT changes**, **6 Notifications**) with the selected
scenario's own buttons directly under it -- and the gateways (hub, Edge 3,
Edge 4). The content column: one sentence, the strip (Edge 3 / Edge 4 →
broker → cloud, the shared `StripNode` boxes), and the scenario, which opens
with ONE line saying what to do and what to watch. The live decoded MQTT
traffic is a bottom drawer (**MQTT traffic**). Nothing appears or disappears
with window size — bigger windows grow the lists and charts.

A scenario's buttons go **two to a line** under `EDGE 3 | EDGE 4` headings,
and each block keeps one answer line at its foot rather than a note under
every button: the rail has 534px at 1366x640 and must not scroll, and this is
the one rail carrying a list *and* its buttons (the budget is in
`gen-sparkplug-views.build_main`). The setpoint is typed into the rail and read
from the session (`custom.mqttSetpoint`) when Send is pressed.

**4 Outages** asks wd-control to take `ignition-edge3` off `wd-mqtt` for 60 s
(docs/DEMO-CONSOLE.md, *Cutting an edge off MQTT*); the Cut and Restore buttons
are enabled and labelled from the same reading as the Link row, so a cut
edge's button counts down. What the audience sees, measured 18/09/2026: the
wire goes quiet at once, the cloud says Online for another ~15 s until the
broker gives up (1.5 x keepalive 10 s) and publishes the Last Will, then
Offline; Restore re-births the edge in ~3 s and the buffered values land with
their original timestamps (41 of 41 heartbeats in a 40 s cut). Re-checked from
the rail 21/09/2026: Cut, *Cut - 37s left* on the button, Engine Offline by
+18 s, Restore, Connected and Online again.

Beside it, **Rebirth Edge 3** asks the edge for `Refresh Edge Node` (a death,
then a birth; refused while the link is cut), and **Alarms during the outage**
lists Edge 3's four alarms at the edge and in the cloud beside the quality of the
cloud tag each sits on -- during a cut the rows keep their last state while the
quality reads `Bad_Stale`. Both are measured under *Three field questions*.

**2 Alarms** says when what it is showing is not current. While an edge is not
publishing, that edge's block greys its **In the cloud** cells and its heading
reads `Offline — cloud rows as at 12:04:33`; scenario 4's *Alarms during the
outage* puts the same words in its own title, so the two panels never disagree.
The **At the edge** cells keep their colour, and should: that half comes from
the edge's observer, which answers over the container network and not the
broker, so it stays live through a cut. Each block also has a fifth row,
`Edge 3 offline` / `Edge 4 offline` -- the cloud's OWN alarm on Engine's
`Node Info/Online` (*The reference setup*), which has no edge half, so its edge
cell is a dash. It is the one row on the panel that is right during an outage,
and the demo shows the fix working rather than describing it.

*Greys* means dimmed as well as neutral (23/09/2026). The first cut only swapped
the cell to `badge-neutral`, which a live `Cleared` already wears, so a block of
cleared rows did not change at all when its edge went offline: the 22/09 cut
screenshot matched the running one pixel for pixel in those cells. The offline
edge's cloud cells now also carry `sp-stale` (opacity 0.45, grayscale), through
the classes binding and not a `props.style` binding, which would drop the
label's static style. Measured with Edge 3 cut at 15:59:36: every Edge 3 cloud
cell computed `opacity 0.45`, `grayscale(1)`; a `Cleared` cell's mean pixel went
35.6 to 30.8 and an `Active` one 136.8 to 33.8, while Edge 4's cells were
unchanged to the pixel and the `Edge 3 offline` row stayed coloured.

The cell states use Ignition's own words -- `Active, unacknowledged (3)`,
`Active, acknowledged (+2)`, `Cleared, unacknowledged (1)` -- and the table
pills pad 8px, not 12: `Cleared, not acknowledged (2)` lost its `)` in the
167px cell at 1366x640.

**5 UDT changes** carries a two-row **Drift check** under the three layouts:
per edge, `matches` or the first difference between what that edge publishes and
what the cloud holds (`sparkplug_demo.udt_drift()`, the same rows
`make mqtt-udt-check` prints).

**A cut no longer locks the tab** (21/09/2026). wd-control's readiness run
sees an edge off `wd-mqtt` and calls the demo not ready -- which, taken at its
word, disabled every button mid-outage, Restore included, and kept them off
after the cut until the next run, because the verdict is pinned to whether
each gateway is serving, not to network membership.
`demo_control._our_cut()` sets that one reason aside while wd-control's own
`mqtt.cuts` holds the cut, or once docker's member list (sampled after the
run) has the stack back. Store & Forward's MQTT cut gets the same.

The old **Gateways** popup went with the rail: its links are the rail's
GATEWAYS. The broker's settings page it also offered is on the hub (Services ›
MQTT Distributor).

The views are **generated**: edit `scripts/gen-sparkplug-views.py`, not the
JSON. Check fit with `make layout-check TAB=MQTT STEPS="1 Live data|2
Alarms|3 Cloud to edge|4 Outages|5 UDT changes|6 Notifications"
SIZES=1366x640,1844x690,1920x1080` — it must report 0 findings.

## How it is built

| | gateway | role | Sparkplug |
|---|---|---|---|
| cloud | `ignition` (hub) | MQTT Engine 5.0.4 | subscribes to everything |
| North | `ignition-edge3`, `Ignition-Edge3` | MQTT Transmission 5.0.4 | group `AlarmDemo`, node `Edge3` |
| South | `ignition-edge4`, `Ignition-Edge4` | MQTT Transmission 5.0.4 | group `AlarmDemo`, node `Edge4` |

Both edges are `ROLE=edge-isolated` (see `stacks/ignition-edge3/stack.meta`).
Node id and site are **derived from the gateway's system name** in one place,
`sp_site`, and `sparkplug-setup.sh` reads the node id back from the edge rather
than repeating the rule.

### The edge project

`ignition/edge-projects/SparkplugEdge/`, deployed to every isolated edge **as
`Edge`** (an Edge gateway runs exactly one project, and it must be called
`Edge`). It lives outside `ignition/projects/` on purpose: `ign-deploy.sh --all
ignition` deploys everything there to the hub. `ign-deploy.sh <P> <gw> --as Edge`
is how it lands.

| resource | what it does |
|---|---|
| `sp_site` | every name: provider `edge`, folder `AlarmDemo`, device `Pumps`, UDT `PumpStation`, journal `EdgeJournal`, the node -> site table |
| `sp_udt` | the `PumpStation` definition, its variants, idempotent provisioning |
| `sp_sim` + timer `Simulate` (1 s, own thread) | the pump station |
| `sp_tx` | the transmitter's settings, read off disk; the Rebirth lever |
| `sp_observe` | the observer document |
| `sp_actions` | local operator actions and the X-WD-Token check |
| WebDev `sparkplug/state`, `sparkplug/action` | the contract below |
| view `Station` at `/` | the edge's own screen: tiles, local controls, a native Alarm Status Table |
| `gw_trial` | byte-identical to Site1/Site2's -- these edges never receive the EAM push that carries it |
| `AutoScan` | a copy of Ops's, so later deploys apply with no restart |

### Tags

```text
[edge]_types_/PumpStation                  the UDT definition
[edge]AlarmDemo                            the transmitter's tagPath
[edge]AlarmDemo/Pumps                      first-level folder = Sparkplug DEVICE "Pumps"
[edge]AlarmDemo/Pumps/North                the instance (South on Edge4)
```

The instance sits one level below the device folder so that with
`convertUdts=false` it is a Template metric **inside** a device, which is where
the spec puts templates; both transmitter modes then publish the same tree.

| member | type | alarms (all manual ack, each with notes) | driven by |
|---|---|---|---|
| `Level` | Float8 % | Level High > 85 (High), Level Low < 15 (Medium) | simulation |
| `Inflow` | Float8 L/s | | simulation |
| `PumpRunning` | Boolean | | simulation |
| `PumpFault` | Boolean | Pump Fault = true (Critical) | the trip / reset actions |
| `DischargePressure` | Float8 bar | Pressure High > 4.4 (Medium) | simulation |
| `LevelSetpoint` | Float8 % | | **writable from the cloud** |
| `Mode` | String AUTO/MANUAL | | **writable from the cloud** |
| `Heartbeat` | Int4 | | simulation |

### The simulation

The inflow's peak exceeds the pump's 60 L/s, and in AUTO the pump starts at
setpoint + 32 and stops at setpoint - 38 (82 % and 12 % by default). So Level
crosses **Level High once a cycle** and every drain-down crosses **Level
Low**, with nobody touching anything. Tuned offline before it was written:
North cycles every ~5 minutes between ~12 % and ~93 %, South every ~3 minutes
between ~11 % and ~92 %, so the two are distinguishable side by side. A trip
or `Mode = MANUAL` stops the pump and the level rises to Level High; raising
the setpoint moves the whole band.

### UDT variants (`udt_diverge`, on one edge)

| variant | change |
|---|---|
| `add_member` | adds `Vibration` |
| `alarm_setpoint` | Level High 85 -> 75 (alarm config on the definition) |
| `remove_member` | removes `Inflow` |
| `default_value` | `LevelSetpoint` default 50 -> 65 |
| `datatype` | `Level` Float8 -> Int4 (*Three field questions*, B) |

`udt_restore` writes the shared definition back. A definition travels only in
NBIRTH, so each of these re-births with `scope: module`.

Scenario 5 sets the problem (**Change layout (diverge)** / **Put back** on Edge
4) beside the two fixes. **Fix 1: new version** has **Edge 3 to v2**, **Edge 4 to
v2** and **Retire v1** (`udt_rollout_v2`, `udt_retire_v1`; T9). **Fix 2: roll out
to every edge** is **Roll out to both edges** (`udt_rollout_all`; T10). **Reset**
(`udt_reset_baseline`) puts both edges on the original `PumpStation`, removes
`PumpStation_v2` everywhere, and repairs Engine's instances. Each button answers
with what Engine now holds, whether each cloud instance matches its edge, and the
pause per edge. They take 14-18 s.

### The observer contract

Base `http://<edge container>:8088/system/webdev/Edge/sparkplug/` -- the hub
calls it with `system.net.httpClient(version="HTTP_1_1")`.

- `GET state` -- open, read-only, no secrets. The shape in the design brief,
  plus `site`, `provider`, `folder`, `journal`, `device`, `tagRoot`,
  `instancePath`, and `errors` (what could not be read, beside what could).
  Each tag carries `path` (`North/Level`, relative to the device folder) and
  `fullPath`. `udt` carries `variant`, `hash`, `members` (with each alarm's
  setpoint), `instanceType`.
- `POST action` -- body `{"action": ...}`, answer `{"ok", "message"}`. Actions:
  `trip_fault`, `reset_fault`, `ack_local` (`id`, or all unacked), `set_mode`
  (`mode`), `set_setpoint` (`value`), `udt_diverge` (`variant`, optional
  `rebirth: true`), `udt_restore`, `udt_version` (`version` 1 or 2: bind the
  instance to `PumpStation` or `PumpStation_v2`, creating it if missing),
  `udt_retire` (`name`: delete a definition the instance no longer uses),
  `rebirth`, `provision`. Every `rebirth: true` takes `scope` (`node` or
  `module`). Requires `X-WD-Token`; 403 without it.

The hub has its own route for the two UDT fixes (T9, T10):
`/system/webdev/GatewayAdmin/sparkplug/udt`. `GET` is open and read-only:
Engine's `PumpStation*` types, each edge's Engine instance (`tagType`,
`typeId`, members and their quality, Heartbeat), each edge's own layout, and its
wire timeline since `?since=<epoch ms>`. `POST {"fn", "args"}` runs
`rollout_v2`, `retire_v1`, `rollout_all`, `reset_baseline`, `edge_action` or
`engine_delete` (only under `[MQTT Engine]`), with the same `X-WD-Token`,
checked against the hub's own `wd/sparkplug-token`.

The token is one random value, generated once by `sparkplug-setup.sh`,
installed as `sparkplug-token` in the `wd` file secret provider on the hub
and on every isolated edge. The hub's file is the source of truth. It is never
printed and never in git.

### What `sparkplug-setup.sh` sets

| where | what | why |
|---|---|---|
| each edge | reset an EXPIRED trial (`POST /data/api/v1/trial`) | nothing resets a trial automatically; an expired Edge answers WebDev 402, stops Transmission and runs no timers |
| each edge | the Edge project | the one project it runs; the first deploy is applied with `POST /data/api/v1/scan/projects`, and a restart is only the fallback |
| each edge | WebDev enabled in `modules.json` | the observer is a WebDev route |
| hub + edges | `wd` / `sparkplug-token` | guards the action route |
| each edge | tags, via the `provision` action | tags are config, not project files |
| each edge | transmitter: `groupId`, `edgeNodeId`, `tagProvider`, `tagPath`, `alarmEventEnable`, `alarmJournalName`, `historyStore`, optionally `convertUdts` | publish the station and its alarms |
| each edge | server-set `rpcClientEnabled` | `system.cirruslink.transmission.publish` needs it |
| each edge | the Transmission server's RPC-client fields -- `rpcCaCertFile` (the broker CA), `rpcUsername`, `rpcPassword` -- via `ign-mqtt.sh setup <edge>`, only when they differ from the main client's | `publish()`'s own connection: left at the defaults it fails TLS and delivers nothing (C2) |
| each edge | `sf-arm.sh <edge>` | the three-setting store-and-forward rule, proven from the log |
| hub | Engine `general`: `enableAlarmEventPublishing`, `blockNodeCommands` / `blockDeviceCommands` false, `alarmDisplayPathType` **ENGINE** | alarms in, commands out, readable paths |
| hub | alarm journal `AlarmDemoJournal` on `Postgres`, own tables, 14-day pruning | the hub had no journal, so propagated alarms were journaled nowhere |
| hub | Engine custom namespace `AlarmDemoNotify`: `notify/AlarmDemo/#` -> `[MQTT Engine]Notify` | the raw-MQTT notification road (C2) |
| hub | project `SparkplugCloud` (pipeline `CloudNotify`, library `sp_cloud`), deployed by name from `ignition/edge-projects/` | the hub half of the notification demo (C3, C4) |
| hub | `[default]SparkplugDemo/<node>/PumpFault` reference tags on each edge's Engine PumpFault, alarmed `Pump Fault (cloud)` -> `project:SparkplugCloud:/pipeline:CloudNotify`; `CloudNotifyLog` | an alarm that arrives over Sparkplug never runs a hub pipeline; a hub alarm does |
| hub | `[default]SparkplugDemo/<node>/Online` reference tags on each edge's Engine `Node Info/Online`, alarmed `Edge N offline`, false = Active, Critical, auto-ack | during an outage every propagated alarm row keeps its pre-cut state and nothing marks it; the cloud has to raise its own alarm (*What to tell a customer*) |

The edge project carries its own pipeline, `EdgeNotify` (C1), so the edge rows
need nothing extra for it; the UDT's alarms name it on `provision` and `udt_restore`.

`--hub-only` applies the hub rows alone, without re-saving any transmitter and
so without re-birthing an edge. `--deploy-only` pushes the project and nothing
else.

## What we measured

Live tests T1-T8 (tag latency, alarm propagation, cloud and edge
acknowledgement both ways, the block flags, cloud writes, a broker cut with
reconciliation, the JVM flag, and the UDT question in both transmitter modes).
Recorded here with dated evidence as they are run. Wire evidence is decoded
from EMQX by a throwaway subscriber on `backbone`.

### Engine tag paths (measured 11/09/2026, `convertUdts=true`)

```text
[MQTT Engine]Edge Nodes/AlarmDemo/Edge3/Pumps/North/<member>     the station (South under Edge4)
[MQTT Engine]Edge Nodes/AlarmDemo/Edge3/Node Info/Online          node birth/death state
[MQTT Engine]Edge Nodes/AlarmDemo/Edge3/Node Info/Data Latency    Engine's own measure (4 ms)
[MQTT Engine]Edge Nodes/AlarmDemo/Edge3/Node Control/Rebirth      write true -> NCMD rebirth
[MQTT Engine]Edge Nodes/AlarmDemo/Edge3/Pumps/Device Info/Online
[MQTT Engine]Edge Nodes/AlarmDemo/Edge3/Pumps/Device Control/Rebirth
```

On the wire: `spBv1.0/AlarmDemo/NBIRTH/Edge3`, `DBIRTH/Edge3/Pumps`,
`DDATA/Edge3/Pumps`, with the UDT instance flattened into metric names
`North/Level`, `North/Mode`, and so on. Engine tag timestamps carry the
edge's metric timestamp. Edge to broker took 3-6 ms.

### T1 -- tag data, edge to cloud (11/09/2026, 18:19)

Both edges' stations reach `[MQTT Engine]` at the paths above, all Good.
Sampling the Engine `Heartbeat` from the hub every 20 ms for 15 s: **hub
receive minus edge metric timestamp was 1032-1100 ms (median 1039)**, and
that second is **not the network**. The recorded DDATA shows it all:

| leg | measured |
|---|---|
| tag change -> DDATA leaves the edge (payload ts - metric ts) | 1026-1075 ms, median 1027 |
| DDATA payload ts -> broker delivers it to a subscriber | 2-4 ms median |

The whole second is Transmission's `tagPacingPeriod` (1000 ms, shipped
default): it batches changes and publishes once a second, stamping each
metric with the time it changed. Engine's own `Node Info/Data Latency` reads
4 ms, which measures the second leg only. To show sub-second latency, lower
`tagPacingPeriod`. Nothing in the network needs changing.

### T2 -- alarms, edge to cloud (11/09/2026)

**Edge alarms become real alarms in the hub's own alarm status** -- 26 events
from both edges by 19:10, priority and label intact, raised by the simulation
alone (Level High, Level Low, Pressure High) and by a pump trip.

**The hub keeps the edge's own alarm event id**: 16 of 16 of edge3's ids appear
unchanged on the hub. So a cloud acknowledgement addresses exactly the event
the edge holds, and the console can join the two lists by `id`.

Hub alarm **source path**, measured (as the PAGE agent also measured):

```text
prot:MQTT:/src:Ignition-Edge3:/prov:MQTT Engine:/edgeProv:edge:/group_id:AlarmDemo:/edge_node_id:Edge3:/device_id:Pumps:/tag:AlarmDemo/Pumps/North/Level:/alm:Level High
```

**That is the `convertUdts=true` form. Under Templates -- the demo's default --
the same alarm arrives as a METRIC**, and a consumer that parses one form
misses every alarm in the other:

```text
prot:MQTT:/src:Ignition-Edge3:/prov:MQTT Engine:/edge_nodes:Edge Nodes:/group_id:AlarmDemo:/edge_node_id:Edge3:/device_id:Pumps:/metric_name:North/Level:/alm:Level High
```

In the hub's status table at 20:52 every event raised from 19:31 on (32) carried
`metric_name`, and the five `edgeProv/tag` ones were all raised 19:22-19:29,
before the switch. Key on `edge_node_id` and `alm`, which both forms share.

The display path under each `alarmDisplayPathType`, for the same edge4 Pump
Fault (19:29-19:30):

| type | display path at the hub |
|---|---|
| `IDS_AND_EDGE` (shipped) | `edge/AlarmDemo/Edge4/Pumps/AlarmDemo/Pumps/South/PumpFault/Pump Fault` -- repeats itself |
| `EDGE` | `edge/AlarmDemo/Pumps/South/PumpFault/Pump Fault` -- no edge node: two edges built alike collide |
| **`ENGINE`** | **`Edge Nodes/AlarmDemo/Edge4/Pumps/South/PumpFault/Pump Fault`** -- names the node, and is the Engine tag's own path |

**The demo uses `ENGINE`** (`sparkplug-setup.sh`'s default): an operator reads
the same path in the alarm table as in the cloud's tag browser. The type
applies to events raised after the change. Each save of Engine's `general`
re-births every edge.

**On the wire, an alarm is not a separate "Alarm" metric.** It is a SECOND metric
carrying the tag's own name (`North/Level`), in the same DDATA as the value
that raised it, datatype String, with `MetaData.content_type = "Alarm"` and
`MetaData.description = {"prot":"MQTT","src":"Ignition-Edge3","group_id":"AlarmDemo","edge_node_id":"Edge3","device_id":"Pumps"}`.
That description is exactly the prefix of the hub's source path. The value is
the serialised alarm event as JSON: `id`, `acked`, `cleared`, `priority`,
`notes`, `displayPath`, `sourcePath`, `activeDataRef` / `ackDataRef` /
`clearDataRef` (setpoint, mode, eventValue, eventTime, ackMode...), `values`.
Level High went out 3 ms after the edge stamped it, in the next paced DDATA.
Each birth also carries an `AlarmReconciliation` DataSet in both NDATA and
DDATA -- a snapshot, not the event channel.

**The hub had no alarm journal**, so propagated alarms were in its status table
and journaled nowhere. `sparkplug-setup.sh` now creates `AlarmDemoJournal` on
the `Postgres` connection, with its own tables (`sp_alarm_events`,
`sp_alarm_event_data`) and 14-day pruning.

### T3 -- acknowledge in the cloud (11/09/2026, 19:14, block flags OFF)

`system.alarm.acknowledge` on the hub, on a cleared, unacknowledged edge3
Pressure High, produced a **DCMD** (not an NCMD) on
`spBv1.0/AlarmDemo/DCMD/Edge3/Pumps` **47 ms later**, with metrics `id` (the
edge's event id), `ackUser` = `usr:cloud-operator@Ignition-Standard`, `state` =
`ClearUnacked`, `edgeAlarmSource` (the edge source path as JSON parts) and
`alarmPriority`. The edge applied it: the event left its alarm status, which is
what cleared-and-acknowledged does.

An ACTIVE alarm makes the round trip visible (19:19): after the cloud ack,
edge3's Pump Fault read **Active, Acknowledged, `ackedBy =
usr:cloud-operator@Ignition-Standard`**, with the **same ack timestamp as the
hub** (the edge adopts the cloud's ack time). Engine appends
`@<hub gateway name>` to the user. The edge then sent the acknowledged event
back in its next paced DDATA (+557 ms), `ackDataRef.ackUser =
cloud-operator@Ignition-Standard`.

**The block flags do NOT stop acknowledgements** (19:23, both flags set TRUE):

| from the cloud, flags TRUE | wire | edge |
|---|---|---|
| acknowledge an alarm | DCMD with `id`/`ackUser`/`state`, +15 ms | applied: Cleared, Acknowledged, `ackedBy` cloud-operator |
| write `LevelSetpoint` 55 | **nothing** | unchanged; hub logs `W CommandWorker: This Gateway Instance is not enabled to publish device level commands ... DCMD/Edge4/Pumps` |

So `blockNodeCommands` / `blockDeviceCommands` gate TAG WRITES only. A customer
who blocks commands to protect the field still lets the cloud acknowledge the
field's alarms. Restored to false afterwards.

**Saving Engine's `general` sends an NCMD `Node Control/Rebirth` to every edge
node**, even with `blockNodeCommands` true (19:23:17 and 19:24:24). Each save
therefore re-births every edge, as does saving a transmitter.

### T4 -- acknowledge at the edge (11/09/2026, 19:20)

An edge4 pump trip reached the hub's alarm status within 975 ms (the poll
interval bounds that figure). Acknowledged LOCALLY at edge4 (`system.alarm.acknowledge` in
the edge's gateway scope, user `edge-operator`), the hub showed **Active,
Acknowledged, `ackedBy = usr:edge-operator@Ignition-Edge4`**, with the same ack
time. No command is involved: the acknowledged event rides the edge's next
DDATA (+844 ms), `ackUser = edge-operator@Ignition-Edge4`.

### T5 -- write from the cloud (11/09/2026, 19:21)

`system.tag.writeBlocking` on `[MQTT Engine]...Edge4/Pumps/South/LevelSetpoint`
= 60:

| | after the hub's write |
|---|---|
| DCMD `South/LevelSetpoint=60.0` on the wire (unsequenced) | +9 ms |
| the edge's tag changes (its own timestamp) | +12 ms |
| DDATA echo, `seq=154` | +940 ms (the next paced publish) |
| Engine's tag reads 60.0, timestamped by the edge | once the echo lands |

`Mode` = MANUAL went the same way as a string DCMD, and the station held its
pump stopped until `Mode` = AUTO came back.

### The hub's journal (11/09/2026, 19:22)

After `AlarmDemoJournal` was created, the hub journaled the propagated edge
alarms -- **under the EDGE's source path** (`prov:edge:/tag:AlarmDemo/Pumps/South/PumpFault:/alm:Pump Fault`),
not the `prot:MQTT:/src:...` path the same event carries in the hub's alarm
status. A journal query or table filtered on the status-table form matches
nothing. Some rows are cleared and acknowledged by `tag:Live Event Limit`: once
an alarm has more live events than its limit, the system acknowledges the
oldest.

**And `displaypath` is EMPTY on every journaled row** (read straight out of
`sp_alarm_events`, checked across 9,500 rows): the display path the hub shows in
its status table is composed at query time from the Engine tag's own path, and
none of it is persisted. So a journal table or report grouped by display path
gets one blank bucket -- group on `source`, or parse the `edge_node_id` and
`alm` out of it, which is also what survives the switch to Templates.

Trap in creating it: **`profile.queryOnly` is required.** Without it the POST
answers 200 and the journal is listed and enabled, then fails to start with
`NullPointerException ... AlarmJournalConfig.queryOnly() is null`, and every
query answers "profile does not exist". `sparkplug-setup.sh` repairs such a
journal.

### T6 -- cut Edge3 off the broker (11/09/2026, 19:26)

*History: measured on EMQX with its ban-and-kick cut. Since 18/09/2026 the cut
is a network one and the broker DOES publish the Last Will, ~15 s in -- see
The page above and MQTT-DISTRIBUTOR.md T-D12.*

The store-and-forward demo's mechanism and API key: a 60 s `peerhost` ban on
edge3's `backbone` address, plus a kick of its open client. The pump was
tripped (19:26:22.991) and reset (19:26:31.353) AT THE EDGE during the cut.

| | during the cut | after |
|---|---|---|
| the edge | `MQTT connection lost` at once; retries every 3 s; observer `transmission.connected` **false** | reconnected 2 s after the ban lapsed; `connected` true |
| MQTT Engine | edge3 **still Online, last values still Good, for the whole 62 s** (`Death Count` unchanged) | NBIRTH/DBIRTH 19:27:18.457; `bdSeq` 3 -> 24 (one per failed retry) |
| the hub's alarm status | no trace of the trip | the Pump Fault event, **with its original edge times** (active 19:26:23.160, cleared 19:26:31.549), Cleared, Unacknowledged |
| the hub's journal | -- | the clear, at 19:26:31.549 |
| tag history | buffered by Transmission (`historyFlushType=ASYNC`) | ONE DDATA of **192 historical metrics** at 19:27:19.255, carrying the three buffered alarm events -- and **stored nowhere**: the hub historian holds 0 AlarmDemo tags, because Engine only historises a historical metric into a tag that has history enabled |

**A broker kick publishes no Last Will, so the cloud never learns the edge
went.** EMQX treats a server-side kick as a clean disconnect and discards the
will. The edge cannot send its NDEATH -- it has no connection to send it on --
so Engine keeps edge3 ONLINE, showing stale values as Good, for the entire
outage. (sparkplug-lab saw ~46 s of the same thing ending in an NDEATH at
keepalive; a kick gives no death at all.) The edge-side
`transmission.connected` is the honest signal; Engine's `Node Info/Online` is not.

**The edge's `connected` is read from the module's own status tag**,
`[MQTT Transmission]Transmission Info/Transmitters/<transmitter>/Edge Nodes/<group>/<node>/MQTT Client/Online`,
with the path built from the transmitter config every time -- see "The edge's
own connection state" below. The first build searched four levels deep for a
tag seven levels down, found nothing, and reported `null`, which the console
showed as NOT CONNECTED.

The expired ban stays listed after its `until` (the EMQX trap in CLAUDE.md).

### T7 -- the JVM flag (11/09/2026)

`--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED` is on the
gateway JVM itself -- the Tanuki `WrapperSimpleApp` java process -- in all four
containers that run a Cirrus Link module (`ignition`, `ignition-backup`,
`ignition-edge3`, `ignition-edge4`), and **no** `AgentAlarmListener` or
`JsonIOException` line appears in any of their logs, with alarms flowing both
ways. The flag was not removed to see the failure: it is on the hub by design.

### The edge's own connection state

On the edge, the module's own status folder is
`[MQTT Transmission]Transmission Info/Transmitters/<transmitter>/Edge Nodes/<group>/<node>/`,
holding `MQTT Client/Online` and `Refresh Edge Node`. There is no tag called
Rebirth in 5.0.4.

### T8 -- the UDT question (11/09/2026)

> Two edge nodes, the same UDT on both, modify one of them. What happens on
> Engine to the UDT and to the instances of both edges? Do they sync -- should
> they?

The definition was changed on **edge4 only**, one variant at a time, with edge3
left on the shared definition.

**Transmission does not re-birth by itself after a UDT definition changes.**
For every variant, edge4 published no birth in the 10 s after the edit, so the
cloud learns of a changed definition only at the next birth. The edge-side
lever, `Refresh Edge Node`, is a full session bounce (NDEATH, NDEATH, then
NBIRTH/DBIRTH). Engine's `Node Control/Rebirth` NCMD re-sends the births with
no death at all.

#### With `convertUdts=true` (the shipped default, 19:32-19:33)

| variant on edge4 | edge4's instance on Engine | edge3's instance on Engine | `[MQTT Engine]_types_` |
|---|---|---|---|
| baseline | a plain **Folder**, no `typeId`, 8 members | the same | **empty** |
| `add_member` | gains `Vibration` | unchanged | empty |
| `alarm_setpoint` (Level High 85 -> 75) | **no visible change** -- Engine tags carry no alarm config; only the moment edge4 raises Level High moves | unchanged | empty |
| `remove_member` (drop `Inflow`) | `Inflow` **stays**; so does `Vibration` from two steps back | unchanged | empty |
| restore | back to 8 live members; `Vibration` lingers as **`Bad_Stale`, value null** | unchanged | empty |

In this mode **there is no UDT in the cloud at all.** Transmission flattens
each instance into ordinary metrics (`South/Level`...), so Engine has nothing to
sync or collide: two edges whose "same" UDT has diverged simply become two
differently shaped folders. **Engine never deletes a member that drops out of
a birth**: the metric stays on Engine, `Bad_Stale`, until somebody deletes the
tag by hand. Alarm configuration never crosses: the cloud sees a different
alarm only when the edge raises one.

#### With `convertUdts=false` (19:35 onwards)

**`convertUdts=false` on its own puts no UDT in the cloud either.** Edge3's
DBIRTH (19:35:37.341) carries the station as a Sparkplug **Template instance**
-- metric `North`, `template_ref=PumpStation`, `is_definition=false`, eight
members with their values -- but the NBIRTH carries **no Template definition**.
The transmitter's `publishUdtDefinitions` ships false. With nothing to bind the
instance to, Engine again holds a plain Folder (`typeId` none) and an empty
`[MQTT Engine]_types_`.

**`publishUdtDefinitions=true` as well is what puts a UDT in the cloud**
(19:38). Each NBIRTH then carries a Template **definition** -- metric
`PumpStation`, `is_definition=true`, eight members -- and each DBIRTH the
instance (`North`/`South`, `template_ref=PumpStation`). Engine now holds
**one** `[MQTT Engine]_types_/PumpStation` UdtType -- a single flat namespace
for the whole provider, not one per group or node -- and **both edges'
instances are UdtInstances of that one type**. It takes BOTH settings: neither
alone does it. `sparkplug-setup.sh` sets the second with
`SPARKPLUG_PUBLISH_UDT_DEFS`.

**Then the same four divergences on edge4 (19:39-19:41) changed nothing in the
cloud** -- when the rebirth was `Refresh Edge Node`:

| variant on edge4 | edge4's own definition | what edge4 PUBLISHED after the rebirth | Engine |
|---|---|---|---|
| `add_member` | 9 members | definition 8 members, md5 `2b1b88d7...`; instance 8 members | unchanged |
| `alarm_setpoint` | Level High 75 | the same md5 | unchanged |
| `remove_member` | 7 members | the same md5, still 8 members | unchanged |
| `default_value` | LevelSetpoint default 65 | the same md5 | unchanged |

Every birth edge4 sent after its UDT changed was byte-for-byte the birth from
before the change: the same definition md5, the same eight-member instance.
Engine logged no collision because it never saw a different definition.
**With Templates, `Refresh Edge Node` re-sends a cached UDT; it does not re-read
it.** (With `convertUdts=true` the same lever did carry the new member.)
Transmission again never re-birthed on its own: zero births before asking, two
after, for all four variants.

**Two levers DO make Transmission re-read the UDT** (19:42-19:43, edge4 on
`add_member`):

| lever | what edge4 published | Engine |
|---|---|---|
| `[MQTT Transmission]Transmission Control/Refresh` (module-wide) | definition **9 members, new md5 `cbeaee1e...`**; instance 9 members incl. `Vibration` | `W SparkplugBPayloadHandler: UDT definition collision detected for PumpStation. Set log level to 'TRACE' for details.` (19:42:31.866) -- **kept its 8-member definition** |
| re-save the transmitter | the same 9-member definition | the same collision warning (19:43:04.804), the same 8-member definition |

**This is the answer to the customer's question.** Engine keeps ONE
`_types_/PumpStation` for the whole provider. When a second edge births a
different definition under the same name, Engine logs a collision warning and
**keeps the definition it already had**. The diverged edge's instance stays
bound to the old shape: edge4 publishes `Vibration`, and the cloud simply
does not have it. There is no merge, no sync, no error on either edge, and
nothing ever flows from Engine back to the edge. The only trace is one
WARN line on the hub.

The sp_actions `rebirth` / `udt_diverge` / `udt_restore` actions take
`"scope": "module"` for this lever; the default `node` scope is the session
bounce that re-sends the cache.

**Which definition wins is decided by the order of births, so a restart can
flip it** (19:44, edge4 on `add_member`). Engine was made to forget the type
-- `[MQTT Engine]_types_/PumpStation` deleted, which is exactly Engine's state
after a hub restart -- and the edges were re-birthed in each order:

| first to birth | Engine's `_types_/PumpStation` | the OTHER edge's instance on Engine |
|---|---|---|
| edge4 (diverged) | edge4's **9 members** | edge3 -- which publishes 8 -- **gains `Vibration`**; collision logged when edge3 births (19:44:22) |
| edge3 (shared) | the shared **8 members** | edge4 -- which publishes 9 -- **loses `Vibration`**; collision logged when edge4 births (19:44:44) |

So **should they sync? They do not, and nothing tells the edges.** The cloud's
picture of BOTH edges depends on which one happened to birth first since
Engine last started. A hub restart during which the diverged edge reconnects
first silently reshapes every other edge's instance in the cloud. The edges
never learn it happened. The safe design is not to diverge a UDT that two edges
publish under the same name. Version it by name instead (`PumpStation_v2`), so
each shape gets its own `_types_` entry.

One more cache trap: restoring edge4's definition and re-birthing with
`Refresh Edge Node` (19:44:50) drew another collision at 19:44:52. Edge4 was
defined as base again but still published its cached nine-member Template.
Restore with `scope: module`.

**Deleting Engine's `_types_` entry while its instances exist kills those
instances, and a later birth does not revive them.** The order test deleted
`[MQTT Engine]_types_/PumpStation` to make Engine forget it. Engine re-learnt the
type from the next birth, and the instances kept their shape, but **every
member of both instances then read `None, Uncertain_InitialValue` and never
moved** (found 20:11, while both edges were live and publishing). The console's
cloud column was dead with nothing in any log -- the toolkit's dead-UdtInstance
trap by a new road. **The fix is to delete the instances together with the
type** (`.../Edge3/Pumps/North`, `.../Edge4/Pumps/South`, `_types_/PumpStation`)
and re-birth: at 20:12 every member was Good and moving again, with no
collision. The "clean final state" above had the right shape and dead values
until this repair.

> **Corrected by T10 (15/09/2026).** A type-only delete does **not** kill
> same-named instances: an Engine instance re-binds by NAME to a type it
> re-learns from the next birth, and both instances came back as
> `UdtInstance`s of `PumpStation` with every member Good. It is **deleting the
> instances** that leaves the damage -- an instance deleted just before a birth
> comes back as a plain `Folder`, and one whose type is gone while its edge now
> names another type stays an empty `UdtInstance` of the deleted type. So
> delete the **type**, not the instances (*Recommendation for UDT changes*, B),
> and check each instance afterwards. The two runs disagree on what a type-only
> delete does, and nothing measured here explains why 20:11 came out dead;
> T10 is the controlled repeat, and it is what `udt_rollout_all()` and
> `udt_reset_baseline()` implement -- delete the type, re-birth, then check and
> repair any instance that is a `Folder`, bound to the wrong type, or empty.

#### The demo runs Templates (`convertUdts=false` + `publishUdtDefinitions=true`)

`sparkplug-setup.sh` enforces this pair by default, for three reasons:

- **The question can only be shown in this mode.** With `convertUdts=true`
  there is no UDT in the cloud at all, so the page's "definition Engine holds"
  column would be empty and the answer would be an absence. With Templates,
  Engine holds one `_types_/PumpStation`, and a divergence produces Engine's
  own collision warning.
- **The answer is honest either way.** The customer's default (`true`) is
  measured and documented above. It is the mode where nothing can collide, and
  also the one where nothing in the cloud tells you the two edges differ.
- **The cost is known.** A divergence reaches the cloud only through the
  module-wide refresh (`scope: module`); `Refresh Edge Node` re-sends the cache.

For the PAGE agent: the Engine-side definition is `[MQTT Engine]_types_/PumpStation`,
and each instance is a `UdtInstance` with `typeId` `PumpStation`. The Diverge
button should send `{"action":"udt_diverge","variant":...,"rebirth":true,"scope":"module"}`,
and Restore `{"action":"udt_restore","rebirth":true,"scope":"module"}`. A
collision is visible only in the hub log (`UDT definition collision detected
for PumpStation`) and in the shape difference: the edge's member list against
the Engine instance's.

### T9 -- versioned UDT rollout (A) (15/09/2026, 10:01-10:07)

> Never edit `PumpStation`. Create `PumpStation_v2` with the changed layout,
> move the instances edge by edge, retire v1 once nothing uses it.

The changed layout is `add_member` (9 members, adds `Vibration`). Each step is
one observer action (`udt_version` / `udt_retire`, `rebirth: true, scope:
module`), driven through the hub's `sparkplug/udt` route; the pause is the
longest silence in that edge's DDATA/DBIRTH on the wire witness, measured from
the last message before the action. The baseline pause is 1.0-2.0 s (the 1 s
`tagPacingPeriod`).

| step | Engine `_types_` | the moved edge's instance on Engine | pause | collision |
|---|---|---|---|---|
| baseline | `PumpStation` (8) | both `UdtInstance` of `PumpStation`, 8 members | -- | -- |
| Edge4 -> v2 (10:01:10) | `PumpStation` (8) **and** `PumpStation_v2` (9) | Edge4: `UdtInstance` of `PumpStation_v2`, 9 members, Good and moving; Edge3 unchanged on `PumpStation` | 4.4 s | none |
| Edge3 -> v2 (10:02:57) | the same two | Edge3: `UdtInstance` of `PumpStation_v2`, 9 members, Good and moving | 4.9 s | none |
| retire v1 at both edges (10:03:21) | **both still**: `PumpStation` lingers | both unchanged on `PumpStation_v2` | 5.5 s / 4.8 s | none |

**Engine holds both definitions side by side, cleanly.** With Edge3 on v1 and
Edge4 on v2, each NBIRTH carried only its own definition (`PumpStation` 8 from
Edge3, `PumpStation_v2` 9 from Edge4), each instance bound to its own type, and
the hub logged no `collision` line from 10:00 to 10:06. An unplanned re-birth
of both edges in the middle (Engine reconnecting after a 29 s JVM pause on the
hub, 10:05:48) re-sent both definitions and still drew none.

**Cloud-side references survive the move.** The Engine instance path does not
change (`Edge Nodes/AlarmDemo/Edge4/Pumps/South/...`): only its `typeId` does.
The hub's reference tags (`[default]SparkplugDemo/Edge3/PumpFault`,
`.../Edge4/PumpFault`) read Good throughout. What does change is anything that
names the TYPE: a cloud-side query, template or UDT-typed parameter keyed on
`_types_/PumpStation` sees the edge leave when it moves.

**What a module refresh costs.** The pause is 4.4-5.5 s per edge, every time:
`Transmission Control/Refresh` stops the clients (NDEATH twice, +0.05 s and
+1.05 s), restarts them, and re-births at +4.2-4.4 s. Engine logs a burst of
`W EngineAlarmStoreManager: Failed to find alarm event (id=...) in the alarm
event cache` on each refresh (10, then 16 lines) -- noise, not a fault. The
edge being moved logs `E AgentEdgeNode: Failed to build the UDT metric based on
the path=South/Level` for each member in the second between re-pointing the
instance and the refresh. Store-and-forward replays that second as historical
metrics.

**Retiring v1 at the edges does not remove it from Engine.** Both edges
deleted `PumpStation` and re-birthed; Engine still held `_types_/PumpStation`
(8 members), unused by any instance. Engine never deletes a type.

**Whether that matters (10:04-10:06): not until something binds to that name
again.**

- A definition no instance uses is **not published**. Edge4 was given a 7-member
  `PumpStation` (`remove_member`) with no instance on it and module-refreshed:
  its NBIRTH carried only `PumpStation_v2`, and Engine logged nothing.
- Once an instance is bound to it, it is the T8 trap. Edge4's instance moved to
  that 7-member `PumpStation` (10:06:34):
  `W SparkplugBPayloadHandler: UDT definition collision detected for PumpStation.`
  (10:06:39.196), Engine kept its stale 8-member type, and Edge4's cloud
  instance kept `Inflow`, which the edge no longer has.

So a lingering v1 is harmless while the name is retired, and a trap the day
anyone reuses it. `udt_retire_v1()` deletes it from Engine once no Engine
instance's `typeId` names it. Deleting a type with no instances does not
disturb anything else.

### T10 -- scripted UDT rollout (B) (15/09/2026, 10:10-10:15)

> The changed definition held once (in git), pushed to BOTH edges, then
> `[MQTT Engine]_types_/PumpStation` deleted on the hub, then every edge
> re-birthed.

The definition is `sp_udt.definition()` in the edge project, the same file on
both edges. Each run: `udt_diverge` / `udt_restore` on both edges with no
re-birth, the Engine delete, then `rebirth` with `scope: module` on both. The
push-to-rebirth took 21-33 ms. No gateway was restarted at any point.

| run | Engine delete | Engine `_types_/PumpStation` after | each instance on Engine | pause per edge | collision |
|---|---|---|---|---|---|
| B1: 8 -> 9 (10:10:31) | type only | 9 members | `UdtInstance`, 9 members, Good and moving | 4.3 / 4.8 s | none |
| B3: 9 -> 8 (10:11:14) | **nothing** | **still 9** | `UdtInstance`, **9 members: `Vibration` stays** | 4.8 / 4.3 s | **two**, 10:11:18.617 and .623 |
| B1 again: 9 -> 8, repairing B3 (10:13:29) | type only | 8 members | `UdtInstance`, 8 members, **every member Good** with real values | 4.6 / 4.9 s | none |
| B2: 8 -> 9 (10:14:18) | type **and** both instances | 9 members | **plain `Folder`, no `typeId`**, 9 members, Good | 4.4 / 4.7 s | none |

**The procedure works without a restart.** After B1, both edges and Engine
agreed on the new layout -- the edge's member list, the Engine type's and the
Engine instance's were the same set -- in both directions (a member added, a
member removed).

*Corrected 22/09/2026 (Three field questions, C): the replay does not cover a
module refresh -- 3 heartbeats were lost in each of two measured refreshes.*

**The data interruption is the module refresh, 4.3-4.9 s per edge**: NDEATH
twice (+0.04 s, +1.05 s), NBIRTH/DBIRTH at +4.3 s. Both edges re-birth together,
so both stations are dark for the same five seconds. Store-and-forward replays
the gap as historical metrics, and Engine logs the same `Failed to find alarm
event` burst as in T9.

**If the Engine definition is NOT deleted, it keeps the stale one -- the
documented trap, confirmed (B3).** Both edges published 8 members, the hub logged
`W SparkplugBPayloadHandler: UDT definition collision detected for PumpStation.
Set log level to 'TRACE' for details.` once per edge, and both cloud instances
kept `Vibration`, which no edge sends any more. Nothing on either edge shows it.

**Deleting the Engine instances is unnecessary on 5.0.4, and deleting them right
before the birth is harmful.** The vendor list says the instance delete is not
required on Engine 4.0.16 or later. That holds: B1 with the instances kept
re-bound them to the re-learnt type both times. With the instances deleted in the
same step (B2, twice: 10:07:12 in a reset, 10:14:18), Engine rebuilt each station
as a plain **Folder**, with values Good but no `typeId`, so nothing that works by
type sees it. A later type delete plus a re-birth does not convert it back (10:15:09),
and the Folder keeps members the edge no longer sends (`Vibration`, Bad). The
repair is to delete the Folder on its own with the type still held: Engine
rebuilt a `UdtInstance` from the next DDATA inside 5 s, with every member Good
and no birth needed (10:08:40, 10:15:26).

**A type-only delete does not kill instances when the type NAME stays the
same.** The same delete, with a module refresh, left every member Good in both
directions (10:10:31, 10:13:29).

**It does kill them when the edge re-births under a DIFFERENT name** -- which is
T8's dead instance, by the road the demo's own Reset first took (10:20:11). Both
instances were bound to `PumpStation_v2`, the reset moved both edges back to
`PumpStation`, deleted both types on Engine and re-birthed. Engine re-learnt
`PumpStation` (8), but each instance stayed a `UdtInstance` of the deleted
`PumpStation_v2`, with **no members at all**, and `Heartbeat` read
`Bad_NotFound`. A further re-birth did not re-bind it. Deleting the two instances
on their own, with `PumpStation` held, rebuilt both as `UdtInstance`s of
`PumpStation` with every member Good, again with no birth (10:21:35).

So the rule on Engine is: **re-bind happens by name.** An instance re-binds to a
re-learnt type of the same name. An instance whose type is gone and whose edge now
names another type, or one deleted just before a birth, has to be deleted again
once the new type is held. `udt_rollout_all()`, `udt_rollout_v2()`,
`udt_retire_v1()` and `udt_reset_baseline()` all check each Engine instance after
the births (Folder, wrong `typeId`, or no members) and do that delete.

Between runs, and once in the middle of this work, the hub answered WebDev with
HTTP 402 `Trial Expired` for a few minutes at a time. Nothing was measured in
those windows.

### Recommendation for UDT changes

| | use it when | cost | how it fails |
|---|---|---|---|
| **A. New name** (`PumpStation_v2`, T9) | edges are changed at different times, by different people, or some may be offline | one module refresh per edge, when that edge moves (about 5 s); a cloud consumer keyed on the type name has to learn the new one | quietly, if anyone ever re-uses a retired name: Engine still holds the old one. Retire it on Engine too |
| **B. Same name, every edge at once** (T10) | every edge publishing the type is online, is changed in the same window, and gets the one definition from git | about 5 s on every edge at the same moment | quietly, if a step is skipped: the Engine delete (stale type, collision warning) or an edge (T8's first-birth-wins). Since 23/09/2026 `make mqtt-udt-rollout` refuses while any edge is not publishing, because a silent edge would otherwise read as a success with a 0.0 s pause (*Three field questions*, B) |
| **C. No UDTs in the cloud** (`convertUdts=true`, T8) | the cloud only needs the values, not a type to query or build on | none: nothing to sync | a removed member lingers on Engine as `Bad_Stale` until deleted by hand, and the cloud never shows that two edges differ |

- **Default to A for anything in the field.** It is the only one of the three
  where an edge that misses the change cannot damage what the cloud believes
  about the others, because each shape has its own name. The price is a type
  name that changes, and a clean-up step (retire v1 at the edges AND on Engine)
  that is easy to forget and harmless until the name is re-used.
- **Use B only as a script, never by hand, and only for a fleet you can reach
  all at once.** It keeps one name, but correctness depends on every step
  running: the definition to every edge, then the type delete, then a MODULE
  refresh (`Refresh Edge Node` re-sends the cached Template, T8). Delete the
  type, not the instances. Then check that each Engine instance is a
  `UdtInstance` of the edge's type with the edge's members. Delete any that came
  back as a Folder, bound to another type, or empty; Engine rebuilds it in
  seconds.
- **Use C when there is no cloud-side consumer of the type.** It removes the
  problem instead of managing it, but leaves the cloud unaware that two edges
  differ.

## Notifications over MQTT (Phase C, 11/09/2026)

Nigel's question: *what can we do about passing alarm pipeline notifications
via MQTT?* Everything below is measured on this stack.

### C1 -- pipelines on an Edge gateway

- **Edge 8.3.8 runs alarm notification pipelines.** The Alarm Notification
  module is enabled on both isolated edges, and a pipeline deployed in the
  `Edge` project logs `[name=EdgeNotify] Alarm pipeline started` on each (20:08).
  The runtime route `GET /data/alarm-notification/api/v1/pipelines` lists it as
  `project:Edge:/pipeline:EdgeNotify`.
- **Pipelines are PROJECT resources, not gateway config:**
  `com.inductiveautomation.alarm-notification/alarm-pipelines/<Name>/data.bin` plus a
  `resource.json` (scope `G`). `data.bin` is Ignition's **gzipped BINARY
  serialisation** of a `PipelineDescriptor`, so it cannot be hand-written. The
  same bytes as IA's shipped templates carry the header `98 29 8f aa`.
  `serializeAndGZip` writes gzipped **XML**, which is the wrong format.
- **One can be built without the Designer, in gateway scope.** The module's
  classes do not import by name ("No module named alarming"), but
  `IgnitionGateway.get().getModuleManager().resolveClass(...)` returns them from
  the module's own classloader. From there: a `BasicPropertySet` block (factory
  `com.inductiveautomation.scriptableBlockFactory`, a `blockId` UUID, the `script`
  body -- the handler is `handleAlarm(event)`), `addBlock`, then the parts that
  are easy to get wrong:
  - **`STARTING_BLOCK` is the Start node, a SEPARATE `BasicPropertySet`** whose
    `CommonBlockProperties.OUTPUT_ID` is the first block's UUID -- read out of a
    Designer-built pipeline (`HomeAlarm`, via `IgnitionGateway.get().createDeserializer()`;
    a bare `XMLDeserializer` cannot read module classes). Setting it to the
    block itself loads cleanly and logs `Evaluating AlarmEvent` for every event,
    then runs nothing: the Start has no output.
  - **`ENABLED` set to true.** It defaults to null, and a pipeline without it
    loads, logs "started", and ignores every event. The runtime route's
    `active` does not tell them apart: it means "holding an event now", and
    an enabled, idle pipeline reads false too.
  - `DROPOUT_CONDITIONS` an empty `EnumSet` of `DropoutCondition`, as the
    Designer writes it -- every transition (active, clear, ack) goes through.
  - `XMLSerializer().serializeBinary(pd, True)`, round-tripped through the
    gateway deserializer before it is written.
  A Script block runs under the pipeline's OWN project's script manager
  (`system.util.getProjectName()` inside it is that project), so it can call that
  project's library.
- **An alarm names its pipeline as a QualifiedPath**: `activePipeline` /
  `clearPipeline` / `ackPipeline` = `project:<project>:/pipeline:<name>`, the
  form the pipeline runtime prints. A tag STORES any string, but a bare
  `EdgeNotify` or `Edge/EdgeNotify` is dropped before the pipeline manager
  logs anything, even at TRACE. Each of the three is separate; an alarm with
  none set runs no pipeline.
- The pipeline's one Script block only calls `sp_notify.notify(event, "EdgeNotify")`.
  All the logic stays in the project library, so the committed `data.bin` never
  needs editing.
- Diagnostics without a restart: `POST /data/api/v1/logs/loggers/<logger>?level=DEBUG`,
  read back with `GET /data/api/v1/logs?logger=<logger>`, then
  `POST /data/api/v1/logs/levelreset`. The pipeline loggers are
  `alarm.Notification.Pipeline` and
  `com.inductiveautomation.ignition.alarming.pipelines.AlarmPipelineManagerImpl`.

- **Proven end to end on both edges (20:41 onwards):** every UDT alarm names
  `project:Edge:/pipeline:EdgeNotify` (tag export of `_types_`), and each
  transition runs the Script block. The pipeline receives three kinds of event.
  The ordinary active, clear and ack. An **ack of the previous instance** when
  an alarm re-activates while it was cleared and unacknowledged: Ignition acks
  the old one, a genuine transition. And, when the previous instance was already
  acknowledged, a **replay** of it, its newest stamp 97 min old, 6 ms after the
  new activation. `sp_notify` classifies by the newest of the active, clear and
  ack stamps -- the state string cannot, because an ack after a clear and a clear
  after an ack both read "Cleared, Acknowledged" -- and drops a transition older
  than 60 s.

### C2 -- edge to cloud

One Script block (`sp_notify.notify`), two roads, side by side:

- **Sparkplug**: the JSON document is written to `EdgeNotice`, a String memory
  tag beside the station inside the transmitter's folder, so it crosses as an
  ordinary DDATA metric.
- **Raw MQTT**: `system.cirruslink.transmission.publish` to
  `notify/AlarmDemo/<node>`, turned back into tags on the hub by Engine's
  custom namespace `AlarmDemoNotify`. Its `rootFolder` prefixes the WHOLE
  topic, so the tags land at `[MQTT Engine]Notify/notify/AlarmDemo/<node>/<key>`.
  `qos1: true` logs "Client ID not set" and subscribes at QoS 0 anyway, so it is
  false.

**Latency, Sparkplug road** (run 2, 20:46; T = the trip request):

| step | active | clear |
|---|---|---|
| alarm transition at the edge | T+196 ms | R+196 ms |
| Script block starts | +18 ms | +9 ms |
| `EdgeNotice` written | +1 ms | +0 ms |
| DDATA delivered by the broker | T+951 ms | R+725 ms |
| hub Engine tag changed (150 ms poll) | T+1053 ms | R+736 ms |

End to end it is 0.5-1.0 s, and nearly all of that is Transmission's publish
pace: DDATA leaves on a roughly one-second beat (T+797, +1797, +3801 in run 1).

**The raw road first delivered nothing -- a config gap in this stack, now
closed.** Until 21:29 the recorder on `notify/#` saw zero messages while
`publish()` returned normally after 1.1-3.1 s. The edge log said why every 2 s.
Transmission's RPC client -- a SECOND MQTT connection, the one `publish()`
uses -- failed TLS with `SSLHandshakeException: (certificate_unknown) PKIX
path building failed`.

The server resource gives that client its OWN settings: `rpcCaCertFile`,
`rpcUsername`, `rpcPassword`, `rpcHostnameVerification`, `rpcClientId`,
`rpcClientCertFile` (`TransmissionServerResource` in 5.0.4). `ign-mqtt.sh`
set only the main client's, which left it with no CA and `rpcUsername` at its
default `admin`. It now sets both, and `sparkplug-setup.sh` checks them and
hands a wrong edge to it. Both edges then logged `RPC client is connected to
the MQTT Server at ssl://emqx:8883`, and no PKIX line since. The RPC client
subscribes to nothing.

While the client was broken, two publishes in flight together raised
`NullPointerException: ... "this.rpcClientThread" is null` inside it. With it
healthy, five publishes on five threads at once (the `raw_burst` action) all
returned in 0 ms, all five reached the broker within 2 ms of each other, and
nothing was logged.

**Latency, both roads, connected** (run 5, 21:34:57; five notifications; the
Script block start is the zero):

| | raw MQTT | Sparkplug `EdgeNotice` |
|---|---|---|
| `publish()` / tag write returns | 0-1 ms | 0-1 ms |
| delivered by the broker | +8 to +30 ms | +750 to +1022 ms |
| hub tag stamped | +10 ms | the edge's own stamp (the metric carries it) |

The raw road is roughly 100 times faster, because it leaves at once rather than
on Transmission's publish beat.

**What the raw road costs.**
- **The tags are not atomic.** Engine's custom namespace explodes each message
  into one tag per JSON key, `[MQTT Engine]Notify/notify/AlarmDemo/<node>/<key>`,
  and a key absent from the next message keeps its old value. The burst's
  `test`, `i` and `of` stayed beside later alarm fields. A consumer that reads
  several key-tags can mix two notifications, so read the document from one
  place, or key on `ts`.
- **The tags are stamped when the hub receives the message**, not when the
  edge sent it.
- **Nothing is birth-certified, sequenced or marked historical.**

`sp_notify` still writes Sparkplug FIRST and runs the raw publish on its own
thread, one at a time, at most 3 waiting. While the RPC client is disconnected,
`publish()` blocks 1-2 s per call. Inline, that held the pipeline's next event
behind it.

**Broker cut, healthy RPC client** (run 6, 21:35:46: a 45 s `peerhost` ban on
Edge3 alone, both its clients kicked; the pump tripped at C+5.2 s and reset at
C+20.4 s, inside the cut; seven notifications in all):

| | during the cut | after the ban lapsed (C+45 s) |
|---|---|---|
| edge pipeline | ran every transition in real time | -- |
| raw publish | returned "ok": the first in 0 ms, the rest after 1.1-2.1 s each while the RPC client retried (`Not authorized to connect` -- the ban) | **queued, in order**: "Publishing queued RPC message on topic: notify/AlarmDemo/Edge3", all 7 delivered at C+45.9 s within 7 ms of each other |
| `EdgeNotice` (Sparkplug) | written locally, held by store and forward | NBIRTH C+46.5 s; all 7 documents flushed at C+47.0 s as `is_historical` DDATA with their original stamps |
| hub live tags | stale | each changed ONCE: the `Notify` key-tags to the last raw message (stamped C+45.9 s, the receipt time), `EdgeNotice` to the DBIRTH's current document |
| hub pipeline (C3) | silent | still silent -- see C3 |

**Both roads got every notification through this cut**, the raw one a second
sooner. The difference is what arrives:
- The Sparkplug documents come flagged historical, with the time they happened,
  and can land in Engine history if `EdgeNotice` has it enabled.
- The raw ones arrive as seven fresh messages that look live, and the hub's live
  tags keep only the last.

**Where each is held.** The raw queue is the RPC client's own, in memory. The
server's `rpc*` settings offer no store and no size, and its bound, and
whether it survives a gateway restart, were not measured. Store and forward
here is also in memory (`Default In-Memory Store`), but a history store is a
setting the transmitter has.

Run 4 (21:02, the same cut with the RPC client still broken) lost every raw
message: the queue is only as good as the connection behind it.

### C3 -- cloud pipelines for alarms that arrived over Sparkplug

- **A propagated alarm never runs a hub pipeline.** Engine's alarm events carry
  the edge's pipeline properties verbatim (`activePipeline:
  project:Edge:/pipeline:EdgeNotify`). A throwaway hub project named `Edge`, with
  a pipeline named `EdgeNotify`, was never evaluated -- the hub TRACE in runs 2
  and 3 shows only the pipeline bound to a hub alarm.
- **A HUB alarm does.** A reference tag on the Engine tag, alarmed and bound to
  a hub pipeline, fired it 801-955 ms after the trip at the edge (runs 1-3). An
  alarm configured directly on the Engine tag (a UdtInstance member, so it is an
  override) also fired (T+850 ms) and survived a node rebirth. The demo uses the
  reference tags, because they live in the hub's own provider, which Engine never
  rewrites: `[default]SparkplugDemo/<node>/PumpFault`, alarm `Pump Fault (cloud)`,
  auto-acknowledged, bound to `project:SparkplugCloud:/pipeline:CloudNotify`.
- **Neither fires for what happens during a cut.** A hub alarm follows the live
  value. Store and forward replays history (run 4: `Pump Fault (cloud)` never
  went active and `CloudNotify` never ran).

### C4 -- cloud to edge

- **Transmission subscribes to nothing but its own commands.** EMQX lists
  exactly three subscriptions per edge client:
  `spBv1.0/AlarmDemo/NDEATH/<node>`, `.../NCMD/<node>`, `.../DCMD/<node>/#`. No
  raw topic can reach an edge.
- **The practical pattern works.** `CloudNotify` (`sp_cloud.notify`) writes
  `[MQTT Engine]Edge Nodes/AlarmDemo/<node>/Pumps/CloudNotice`, and Engine sends
  it as a DCMD. Transmission writes the edge's `[edge]AlarmDemo/Pumps/CloudNotice`,
  which the edge's Station view shows. Run 2: hub alarm T+955 ms, DCMD on the
  wire T+958, edge tag written T+959 (its echo DDATA's stamp). This needs
  Engine's `blockDeviceCommands` false, which setup sets.
- It is sensible for an advisory to the local HMI ("the cloud has seen your pump
  fault"). It is a tag write, not a message: the latest wins and nothing
  queues. And since the hub pipeline is silent through a cut (C3), it cannot
  reach an edge that is cut off anyway.

### C5 -- the per-edge list the console shows

The observer's `notifications` (`GET <edge>/system/webdev/Edge/sparkplug/state`)
is filled by the edge's own pipeline: newest first, at most 20 (`LOG_MAX`), kept
in `[edge]SparkplugDemo/NotifyLog`, outside the transmitter's folder so the log
is never published. Measured at 21:05: edge4 holds 20 of 20 from `EdgeNotify`
-- Level High / Level Low active, clear and ack. Each entry: `nid`, `ts`,
`transitionTime`, `event` (active / clear / ack / test), `alarm`, `priority`,
`state`, `displayPath` (falls back to `<tag path>/<alarm>`, because the edge
reports ""), `source`, `eventTime`, `pipeline`, `node`, `site`, `sparkplug.ok`,
and `published` (`pending`, then `ok` / `ms` / `error`).

**An alarm that flaps is a notification storm.** With the well high, the
discharge pressure rides 4.31 +/- 0.15 bar (the simulation's 7 s ripple) and
crossed Pressure High's 4.4 every cycle: 14 of the 20 log entries in 144 s. The
alarm now has a 0.3 bar deadband, which still lets the peaks raise it.

### What the console should show

| what | where |
|---|---|
| each edge's notification list | observer `notifications` (above) |
| the latest notification from an edge, as the cloud holds it | `[MQTT Engine]Edge Nodes/AlarmDemo/<node>/Pumps/EdgeNotice` (JSON) |
| what the cloud last said to an edge | `[MQTT Engine]Edge Nodes/AlarmDemo/<node>/Pumps/CloudNotice` (text); the edge's copy is observer `notices.cloudNotice` |
| the hub pipeline's own record | `[default]SparkplugDemo/CloudNotifyLog` (JSON list, at most 20) |
| the raw-MQTT road | topic `notify/AlarmDemo/<node>` -> `[MQTT Engine]Notify/notify/AlarmDemo/<node>/<key>`, one tag per JSON key, NOT atomic -- key on `ts` (C2) |
| NOT a propagated alarm | `Pump Fault (cloud)`, source `prov:default:/tag:SparkplugDemo/<node>/PumpFault:/alm:Pump Fault (cloud)` -- the hub's own trigger |

### Recommendation

To get alarm notifications across an MQTT-only architecture, run the
notification pipeline AT THE EDGE. Its Script block sends each notification up
one of two roads, chosen by what the notification is for:

- **As a Sparkplug metric**: write it into a string tag inside the
  Transmission folder.
  - Sequenced and birth-certified.
  - Store-and-forwarded through the transmitter's history store.
  - After a cut it arrives flagged historical, with its original time, so it
    can land in Engine history.
  - The live hub tag holds only the latest.
  - It waits for Transmission's publish beat: 0.75-1 s here.
- **As a raw MQTT message**: `system.cirruslink.transmission.publish` to a
  topic of your own, turned into tags by an Engine custom namespace.
  - About 10 ms.
  - Queued in order by the RPC client through a 45 s cut.
  - Not Sparkplug: nothing marks a replay as late, the per-key tags are not
    atomic, and the queue is in memory with no settings.
  - The RPC client needs its OWN CA and credentials (`rpc*` on the Transmission
    server). Left at the defaults, it reports success and delivers nothing.

Use the metric for the record, the raw message for speed, or both from the one
Script block, as this demo does. Whichever road you choose:
- An alarm that arrives over Sparkplug never runs a hub pipeline. Where the
  cloud must act itself, give the hub an alarm of its own: a reference tag on
  the Engine tag, bound to a hub pipeline.
- To answer the edge, write an Engine tag (a DCMD). Transmission subscribes to
  nothing else.
- Page site staff from the edge. The cloud's pipelines stay silent for whatever
  happens while the WAN is down.

## Three field questions (22/09/2026)

Measured on this rig with MQTT Distributor as the broker, Engine on the hub pair
and Transmission 5.0.4 on Edge 3 and Edge 4, keepalive 10 s, rolling buffer 60 s.
Evidence came from four places at once: a second MQTT subscriber in wd-control
recording every message (time, topic, bytes, metric count, the Heartbeat values
it carried, every alarm metric); `system.alarm.queryStatus()` on the active hub,
with each edge's own list from its observer; a Perspective Alarm Status Table on
the active hub, screenshotted; and the Postgres history and journal. Loss is
counted the T-D11 way: `Heartbeat` goes up by one a second, so the distinct values
stored between two times against `last - first + 1`.

### A. Stale alarms when the broker goes down -- ANSWERED, one part OPEN

Each run did the same on Edge 3: 5 s into the outage trip the pump (event A), at
+12 s reset it (A clears during the outage, unacknowledged), at +16 s acknowledge
at the edge one event the cloud still showed unacknowledged, at +25 s trip again
(event B, still active when the link comes back).

| cause | started | cloud learns | during (+36 s) | at reconnect | +60 s |
|---|---|---|---|---|---|
| 1. Edge 3 cut off `wd-mqtt` | 16:52:36.6 | Offline 16:52:51.4 (+14.8 s, the broker's Last Will) | Edge 3's tags `Bad_Stale`; its 2 alarm rows keep their pre-cut state; 4 new events missing | NBIRTH 16:53:43.9, 6.6 s after Restore: all 6 rows match the edge | all 6 match |
| 2. broker restart: `enableDistributor` false, then true | broker stopped 16:56:22.5 | Offline 16:56:22.9, both edges (+0.4 s: Engine lost its own connection) | both edges' tags `Bad_Stale`; rows frozen; 2 new Edge 3 events missing | broker up 16:57:46.8 (84.3 s down), NBIRTH 16:57:51.6 / .8: every row on both edges matches | all match |
| 3. planned hub failover and hand-back | master `STATE offline` 17:23:30.9 | backup NBIRTH 17:23:32.9 (2.1 s) | on the backup: every row on both edges matches, 0 differences | hand-back `STATE offline` 17:25:00.0, master NBIRTH 17:25:02.2 (2.1 s): 0 differences | 0 differences |

The broker restart is **two writes, each restarting the broker** (every
Distributor write does): 16:56:22.548 `Stopping Chariot MQTT Server`,
16:57:46.832 `Starting Chariot MQTT Server`, Engine connected 16:57:49.870.

- **An alarm that cleared during the outage shows cleared afterwards, with its
  original times** -- confirmed under Distributor for all three causes. Cause 1,
  event A: `be0bbc08` active 16:52:42.363, cleared 16:52:49.364, both inside the
  cut, shown `Cleared, Unacknowledged` at 16:53:47.8 with those times. Cause 2:
  `33403280`, 16:56:28.775 / 16:56:35.575. Cause 3: `e446fe8f`, raised and
  cleared while the backup was active (17:23:37.778 / 17:23:44.749), shown the
  same on the master after the hand-back, which never saw it live.
- **An acknowledgement made at the edge during the outage lands at reconnect.**
  `28b78a59` (cause 1, acked 16:52:53) and `334e8e2a` (cause 2, acked 16:56:39)
  left the cloud's list at the birth.
- **During the outage the cloud's alarm rows are stale and nothing marks them.**
  The tags under them go `Bad_Stale` when the node goes offline; the alarm rows
  keep whatever state they had, and the table shows them in the same colours.
  At 16:53:12 the table showed Pump Fault `28b78a59` `Cleared, Unacknowledged`
  20 s after the edge had acknowledged it, and none of the 4 events raised since
  the cut. Scenario 4's *Alarms during the outage* shows this beside the
  quality.
- **After the birth: 0 stale rows** in 3 causes x 2 moments. The birth replays
  the edge's current table, up to 5 unacknowledged events per alarm (the Live
  Event Limit). Those are the edge's own unacknowledged events, not stale ones:
  every replayed row matched the edge's list by id and state.
- **Two kinds of stale row that are not about outages.** (1) Edge 4 Level High
  `7799272a` was in the cloud as `Cleared, Unacknowledged` from at least 16:47:53
  while the edge no longer listed it; the 16:57:51 birth removed it. How the edge
  dropped it without the cloud hearing was not traced. (2) The retired
  `AlarmDemoDist/Edge4Dist` node from the 18/09/2026 Distributor spike still has 5
  rows in the hub's table on 22/09, one `Active, Unacknowledged` since
  18/09 14:27. Now cleared -- see *Decommissioning an edge
  node*, which also says why deleting its tags did not do it. A node that never births again never clears them. The
  backup kept its own 5 (all cleared, none active) until its own last birth on
  23/09/2026, because alarm status is per half (T-D4).
- **The hub journal misses the activation of an event that changed state again
  before the birth.** In each case the only row journaled is the state the event
  had in the birth, which reached Engine before the replayed history (cause 1:
  DBIRTH 16:53:43.884, replay 16:53:44.964). Cause 1: `e360c857`, `be0bbc08`,
  `d2899cd4` (active and cleared inside the cut, unacknowledged at the birth)
  have a clear row and no active row; `47d1e78a` (active then acknowledged in
  the 16:48 run) has only its ack row. An event the birth no longer carries
  (`ff5c0d40`, acknowledged inside the 16:48 cut) gets all three rows from the
  replay, with original times. The status table is right in every case; a report
  that counts activations from the journal is short.
- **Nothing was lost in history.** Edge 3 Heartbeat: cause 1 140 of 140; cause 2
  188 of 188 (Edge 4 189 of 189); cause 3 189 of 189. Edge 4 in cause 3 stored
  188 of 189: value 479352 reached the broker live (17:25:03.686, stamped
  17:25:02.648) and is not in the table, while the birth's 479351 is stored at
  17:25:02.728. Why the historian skipped it is not verified. What is measured
  (23/09/2026, five handovers and hand-backs between 15:05 and 15:23, Edge 4
  `Heartbeat` in `sqlt_data_1_2026_09`): every birth stores the value
  Transmission last published, 1-2 counts old, stamped with the birth time --
  534359 at 15:09:28.720, after 534361 at 15:09:28.218 -- and none of those
  five lost a value (the only 6 missing of 1,678 were the two module refreshes'
  3 each, *C* below). On 22/09 the skipped value was stamped *earlier* than the
  birth row before it (02.648 against 02.728); the one value lost is the one
  that arrived out of time order. **Reproduced 28/09/2026** at the 13:00:42
  handover: the store-and-forward replay ended at 662636 (13:00:43), the birth
  stored 662637 stamped with the birth time, 13:00:45.523, and the next live
  value, 662638, carrying its own earlier time (about 13:00:45.1), is not in the
  table; 662639 at 13:00:46.128 is. One value in one of that day's six
  transitions. So a value published between the birth's snapshot and the birth
  itself arrives older than the row the birth wrote and is dropped. Which layer
  drops it -- Engine, or the tag refusing an older timestamp -- is not verified.
- **A first failover run showed an extra re-birth of every edge after each
  takeover**, and a second did not. Run 1: the backup went Active at 17:15:51 and
  at 17:16:10.790 logged `New license state` / `Starting the MQTT Clients`, which
  re-created Engine's client and re-birthed both edges (NBIRTH 17:16:16.7); after
  the hand-back the master did the same on its next `Trial time reset`
  (17:20:05.390, NBIRTH 17:20:11.6). The master's trial resets at 16:49:04,
  16:59:04 and 17:09:08, with no transition before them, re-birthed nothing.

  **Measured 24/09/2026: the extra birth needs a trial reset that actually
  succeeds, not a transition by itself and not a reset attempt by itself.** A
  takeover moves Engine's STATE offline then online and always re-births both
  edges once, on its own; the extra birth only follows a genuine `LicenseState`
  flip from `expired=true` to `expired=false`, which is what makes Engine log
  `Starting the MQTT Clients` a second time. This run isolated it because no
  reset could succeed: 8.3.9 resets only an expired trial, and the keep-alive
  then in use cancelled the trial's expiry task, so each gateway's
  TrialManager never counted itself expired while `/data/api/v1/trial`
  reported `expired: true` -- every reset was refused with `Trial has not
  expired yet.` (`TrialManager.java:77`) until a container restart
  ([TRIALS.md](TRIALS.md) has the cause and the fix):

  | gateway / cycle | event | time (ACST) | result |
  |---|---|---|---|
  | master | boot-time reset succeeds, 119 min | 16:29:03.096 | `LicenseState` flips; `Starting the MQTT Clients` 16:29:03.723 |
  | edge 3 / edge 4 | boot-time reset succeeds | 16:31:00.422 / 16:31:11.110 | NBIRTH 16:31:03.614 / 16:31:14.537 |
  | master | every later attempt, ~1/min, 16:39-20:44 | -- | all fail (`IllegalStateException`); no flip, no re-birth |
  | backup | its own first attempt, 16 s after taking over | 19:36:32.267 | fails the same way |
  | cycle 1, failover | master (Active) -> backup | issued 19:35:57.412, Active 19:36:16.641 | takeover NBIRTH only -- Edge 3 19:36:19.118, Edge 4 19:36:19.113; nothing further to 19:38:08 |
  | cycle 1, hand-back | backup -> master | issued 19:39:04.702, Active 19:39:38.499 | takeover NBIRTH only -- Edge 3 19:39:42.011, Edge 4 19:39:42.008; nothing further to 19:40:58 |
  | cycle 2, failover | master -> backup | issued 19:42:08.404, Active 19:42:27.771 | takeover NBIRTH only -- Edge 3 19:42:29.932, Edge 4 19:42:29.929; nothing further to 19:44:02 |
  | cycle 2, hand-back | backup -> master | issued 19:44:25.786, Active 19:44:45.447 | takeover NBIRTH only -- Edge 3 19:44:47.612, Edge 4 19:44:47.614; nothing further to 19:46:19 |
  | cycle 3, failover | master -> backup | issued 19:46:42.275, Active 19:47:01.601 | takeover NBIRTH only -- Edge 3 19:47:03.748, Edge 4 19:47:03.747; nothing further to 19:48:36 |
  | cycle 3, hand-back | backup -> master | issued 19:48:58.689, Active 19:49:18.230 | takeover NBIRTH only -- Edge 3 19:49:20.403, Edge 4 19:49:20.402; master held Active with no further birth to at least 20:44 |

  Six transitions, six single-birth takeovers, no extra birth -- because every
  reset attempt across the run failed, transition or not, including the
  backup's own attempt 16 s after it took over. That is the same shape as the
  16:49/16:59/17:09 no-transition resets above and the mirror of run 1's
  reset-after-takeover, and between the two runs they support the hypothesis.
  This run could not repeat run 1's positive case -- a reset succeeding right
  after a takeover -- because no reset succeeded at all after boot; the
  boot-time resets are the only `expired=true -> expired=false` flips this rig
  produced today, and they did re-birth both edges, off a pair that did not
  yet exist. **OPEN:** whether a licensed pair does this at all -- it needs a
  licensed Engine, and there is still no Cirrus Link licence on this rig to
  test it.

**T-D4's "each Engine alarm twice" is an artefact of `queryStatus`, not a
duplicate event.** At 16:43 the hub returned 38 rows for 20 events: every event
from the `MQTT Engine` provider twice (`provider=["MQTT Engine"]` 36 rows,
`["default"]` 2). The two rows are different `PyAlarmEventImpl` objects
(`identityHashCode` differs, `equals` false) identical in every field (source,
display path, state, times, count 8). The Alarm Status Table shows each event
once (20 rows), and the journal has one active row per event. A script that
counts or acknowledges from `queryStatus` must de-duplicate on the event id,
which `ack_cloud_all()` already does.

**Why: MQTT Engine registers its alarm provider with the gateway twice**
(23/09/2026, read in process on both halves through a throwaway WebDev route).
The AlarmManager's provider list holds four entries: `local`,
`RemoteGatewayAlarms[Ignition-Edge1]`, and `MQTT Engine Alarm Provider` twice
-- the same `EngineAlarmProvider` object (one identity hash, its static
`instance` pointing at itself). `queryStatus()` asks each registered provider,
so every Engine event comes back twice (44 rows for 24 events; the 4 `default`
rows once each). It stayed at two across four "Starting the MQTT Clients"
restarts on the master since 14:17, so it is registered twice once, not once
per client start. It is inside Engine 5.0.4 and nothing in this repo's
configuration causes or can change it.

### B. UDT, same name, slight modification -- ANSWERED

**A datatype change collides like any other change, and the two halves go
different ways** (17:27:08, Edge 4 on the new `datatype` variant, module
refresh):

```text
17:27:13.420  NBIRTH Edge4   PumpStation definition: Level datatype 3 (Int32), was 10 (Double)
17:27:13.642  hub  W SparkplugBPayloadHandler: UDT definition collision detected for PumpStation.
17:27:15      [MQTT Engine]_types_/PumpStation/Level              Float8     <- the old type, kept
              [MQTT Engine]...Edge4/Pumps/South/Level             Int4, 19, Good   <- the instance takes the edge's
              [MQTT Engine]...Edge3/Pumps/North/Level             Float8, Good
17:27:13.728  history for Edge 4 Level stored as intvalue from here
```

Engine neither refuses nor coerces the value: it keeps the stale Float8 type and
lets Edge 4's instance member take Int4, so one type name now has two datatypes
under it and nothing but the log line says so. Putting the variant back
(17:27:46) returned the member to Float8 with no collision.

**One edge off the broker during Fix 2**, measured two ways:

- **As the command runs it** (`make mqtt-udt-rollout` with Edge 3 cut, 17:28:02,
  31.0 s): the push reached Edge 3 anyway, because the cut takes it off
  `wd-mqtt` only and the definition travels over `backbone` to its observer. So
  when the link came back (NBIRTH 17:29:03.4, 6.4 s after Restore) Edge 3 births
  the new 9-member layout, every member Good, no collision. But the command said
  `ok rolled out` and, for Edge 3, *"matches the edge; values NOT moving; data
  paused 0.0 s"* -- 0.0 s because it measures the longest gap between messages,
  and Edge 3 sent none. While it was cut, Engine's Edge 3 instance showed 9
  members: 8 `Bad_Stale`, and `Vibration` **Good = 1.8**, a value that edge never
  sent.
- **As it fails in the field -- the edge misses the push** (17:29:37: Edge 3 cut
  and left on 9 members; Edge 4 given 8, Engine's type deleted, Edge 4 module
  refresh): Engine learnt 8 from Edge 4. Edge 3 came back at 17:30:07.9 with its 9:
  `UDT definition collision detected for PumpStation` at 17:30:08.047, Engine kept
  8, and Edge 3's cloud instance has no `Vibration` although the edge sends it.
  Everything else read Good. This is T8's first-birth-wins, and the failure Fix 1
  exists to avoid.

The Recommendation stands. The command's false success is fixed: it now
refuses while an edge is not publishing (*What to tell a customer*, *UDT
changes*).

### C. Rebirth = gap -- ANSWERED

Four kinds on Edge 3, twice each (17:33-17:36 and 17:37-17:40), times from the
write or POST:

| kind | on the wire | Edge 3 silent for | Edge 3 Heartbeats lost (history) |
|---|---|---|---|
| device: write `Pumps/Device Control/Rebirth` on Engine | DCMD, then DBIRTH at +0.66 / +0.39 s; no death, no NBIRTH; DDATA every second throughout | 2.0 s both runs (the normal pacing) | 0 of 49, 0 of 49 |
| node, from the cloud: write `Node Control/Rebirth` | NCMD, then NBIRTH at +0.74 / +0.48 s, DBIRTH, AlarmReconciliation; no death | 2.2 / 2.0 s | 0 of 50, 0 of 50 |
| node, at the edge: `Refresh Edge Node` (scenario 4's button) | NDEATH twice (+0.7, +1.7 s), NBIRTH at +3.0 / +2.9 s, then the rolling buffer's replay (532 / 711 metrics) | 3.0 / 2.4 s | 0 of 50, 0 of 49 |
| module: `Transmission Control/Refresh` | NDEATH twice, NBIRTH at +4.9 / +8.9 s, replay of 195 / 191 metrics | 5.4 / 8.7 s | **3 of 49, 3 of 52** (480864-480866, 481115-481117), not replayed |

- **Only the device rebirth keeps the node's data flowing.** Edge 3's DDATA never
  paused beyond its normal 1-2 s, and no node-level message was sent. The other
  three re-send the node birth; the two edge-side ones also publish a death, so
  the cloud shows the node Offline for their gap (the page showed Offline at
  +2.5 s, Online by +11.5 s). Edge 4 was unaffected by all four.
- **What a birth costs.** NBIRTH 1,404 bytes, 5 metrics (one of them the
  `PumpStation` Template definition); DBIRTH 1,558-1,632 bytes, 10-11 metrics once
  the instance is flattened (1,531 / 1,765 bytes with the 9-member layout). And
  after every node birth the edge sends an `AlarmReconciliation` DataSet in one
  DDATA of **162,636-181,408 bytes** -- over a hundred times the birth itself. A
  device rebirth sends none.
- **The rolling buffer covers the node rebirths, not the module refresh.** A module
  refresh stops Transmission, and what changes while it is stopped is not buffered:
  3 seconds lost each time. This corrects T9/T10's "store-and-forward replays
  the gap".

## What to tell a customer

Two of the three field questions are real customer concerns, and each answer is
something to configure, not a caveat. Rebirth (C) is not: a module refresh is a
deliberate act, and a few seconds of data around it is expected. The evidence
for every number here is *Three field questions* above.

### Alarms during an outage

**What freezes.** While an edge is off the broker, the cloud's alarm rows for it
keep the state they had when the link dropped, drawn exactly like live rows.
At 16:53:12 (22/09/2026) the cloud showed a Pump Fault `Cleared,
Unacknowledged` 20 s after the edge had acknowledged it, and none of the 4
events raised since the cut. The gateway does know: the tags under those alarms
go `Bad_Stale`, and Engine's `Node Info/Online` goes false once the broker
publishes the edge's Last Will, 14.8 s into a cut at keepalive 10 s. Nothing in
the alarm system passes that on.

**What reconciles.** Everything, at the edge's next birth (6.6 s after the link
came back): 0 stale rows in 3 causes x 2 moments, an event that came and went
inside the outage shown cleared with its original times, an acknowledgement
made at the edge during the outage applied, and no history lost (140 of 140
heartbeats through the cut). The damage is confined to what an operator sees
during the outage, and to the journal.

**Configure two things.**

1. **An alarm on the link, in the cloud.** A reference tag on each edge's Engine
   `Node Info/Online`, alarmed false = Active, priority above the process
   alarms -- the row in *The reference setup*, written by `sparkplug-setup.sh`.
   A propagated alarm cannot tell you its own edge has gone; the cloud has to
   raise its own. It is the only row that is right during the outage.
2. **Mark the stale rows on the screen.** Wherever a cloud operator watches
   alarms, show per edge that its rows are not current and the time they are as
   at, from the same two readings the demo uses: Engine's `Node Info/Online`,
   and how long since anything arrived from that node (the second sees the cut
   from its first seconds; the first waits for the Last Will).

**The journal caveat.** An event that comes and goes inside an outage is
journaled only in the state the birth carried: a clear row and no active row
(`e360c857`, `be0bbc08`, `d2899cd4` in cause 1). The status table is right; a
report that counts activations from the journal comes up short. To keep every
transition, send each one up as its own message from an edge pipeline --
scenario 6's raw road (`system.cirruslink.transmission.publish` to a topic of
your own, an Engine custom namespace turning it into tags), queued in order by
the RPC client through a 45 s cut. It is not Sparkplug, so a replayed message is
not marked late (*Notifications over MQTT*, Recommendation).

Shown by **2 Alarms** (the marking and the `Edge 3 offline` row) and **4
Outages** (the cut itself, the four readings that disagree, and the same
marking in the alarm table's title). Measured on the page twice (23/09/2026).
At 11:15:16 Edge 3 was cut: the block read `Offline — cloud rows as at
11:15:22` (Edge 3's last message), its two `Active, not acknowledged` cloud
cells were drawn grey, and `Edge 3 offline` read `Active`. At 14:21:57
again: `Offline — cloud rows as at 14:21:58`, `Edge 3 offline` `Active`, Edge
4's two active alarms still in colour at both ends, scenario 4's four alarm tags `Bad_Stale`
under `Alarms during the outage — cloud rows as at 14:21:58`; restored at
14:22:52, and by 14:23:20 the note and the title suffix were gone, `Edge 3
offline` read `Cleared` and the tags `Good`. Both panels fit 1366x640 in the
cut state as well.

### UDT changes

The *Recommendation for UDT changes* table is the choice: **A**, a new type name
per shape, for anything in the field; **B**, one name rolled out to every edge
at once, only as a script and only for a fleet reachable in one window; **C**,
no UDTs in the cloud, when nothing cloud-side uses the type. Two things now back
it up.

**The safety net: `make mqtt-udt-check`.** Engine keeps the first definition it
learns and says so once, in the log. The check compares, per edge, the
definition the edge publishes with the type Engine holds and with that edge's
Engine instance, and prints `matches` or the first difference in plain words
(`Level Int4 at the edge, Float8 in the template`, `Vibration missing in the
cloud's template`). It exits 1 on any difference, so it can gate a release.
It cannot see an alarm limit: a Sparkplug Template carries no alarm
configuration, so the cloud never receives one to compare (T8). Scenario 5 shows
both edges' layouts side by side for that.

**The fix to B's silent failure.** `make mqtt-udt-rollout` refuses while any
edge is not publishing (Engine says the node is offline, or nothing has arrived
from it for 5 s), and where an edge sends nothing during a run it says `SENT
NOTHING` rather than a 0.0 s pause. Measured 23/09/2026 with Edge 3 cut at
14:21:57: at 14:22:45 the command answered `Refused: Edge 3 not publishing`
within 5 s and changed nothing, and at 14:22:50 `make mqtt-udt-check` gave
Edge 3 `matches, but this edge is not publishing`. With Edge 4 on the
`datatype` variant (14:25:48), the check and scenario 5's table both read
`Level Int4 at the edge, Float8 in the template` (14:26:13).

Shown by **5 UDT changes**: *Change layout (diverge)* on Edge 4, the **Drift
check** naming the difference, then Fix 1 (a v2 per edge) or *Reset*.

### Decommissioning an edge node

A node that never births again never clears its alarm rows, and neither
deleting its tags nor acknowledging the rows does it. Measured 23/09/2026 on the
retired `AlarmDemoDist/Edge4Dist` node from the 18/09/2026 Distributor spike:
5 rows in the hub's alarm status, 1 of them active, surviving two host reboots.
Its Engine folder was already gone, so the rows outlive the tags; and a cloud
acknowledgement of all 5 (14:36:55) changed nothing, because a cloud ack of a
Sparkplug alarm is a DCMD to the edge, and there was no edge to answer it.

What cleared them was one last birth. The node's own group and node id were put
back on Edge 4 as a second transmitter (`groupId` `AlarmDemoDist`,
`edgeNodeId` `Edge4Dist`) over a folder holding the same device (`Probe`) and
metric path (`Station/Level`) as the rows, with no alarms on it and
`alarmEventEnable` true. A birth of the
node alone did not clear them (14:38-14:41, 0 of 5); after Edge 4's module
refresh (14:41:22), which births the device too, all 5 were gone by 14:41:29.
Then the transmitter was deleted and Engine's `Edge Nodes/AlarmDemoDist` folder
with it (14:42:48), and nothing came back.

So retiring an edge is a step, not an absence:

1. **Before** the edge goes, clear or acknowledge its alarms at the edge and let
   it publish that, so the cloud's rows reconcile while the node can still
   answer. Then stop its transmitter and delete `[MQTT Engine]Edge
   Nodes/<group>/<node>`.
2. `make mqtt-udt-check` lists any node the cloud still holds alarm rows for
   that is not one of the demo's edges, with the count and how many are active.
3. If rows are left behind, birth the node once more with its devices and no
   alarms, as above, then remove it again.

**Alarm status is per redundant half (T-D4), so each half needs its own last
birth.** The above was done on the master with the backup stopped. On
23/09/2026 the backup, started again, still held all 5 rows (0 active) both as
the Warm standby (15:08:56) and as the Active half after a planned handover
(15:10:07), while the master held none. With the backup Active, the same
transmitter on Edge 4 cleared them -- but only with `alarmEventEnable` **true**:
a module refresh at 15:14:23 with it false birthed the node (Engine's folder
came back) and left 5 of 5; with it true, a refresh at about 15:15:43 had them gone
by 15:15:51. Without alarm events the birth carries no `AlarmReconciliation`,
which is what clears the rows. The transmitter, Engine's `Edge
Nodes/AlarmDemoDist` on both halves and Edge 4's `[edge]DecomEdge4Dist` folder
were then deleted; after the hand-back (15:23:50) neither half held a row.

So step 3 above is per half: with the pair running, birth the node once with
each half Active. The backup also needs the action token in its own volume
for the MQTT page's guarded buttons to work while it is Active;
`sparkplug-setup.sh` now copies it there (it was missing on this rig).

## Traps

Carried in from elsewhere in this repo and from `sparkplug-lab`, each of which
fails silently:

- **`alarmEventEnable` ships false.** Tags reach the cloud; the alarms on them
  never do.
- **Engine's `blockNodeCommands` / `blockDeviceCommands` ship true.** A cloud
  write produces no traffic at all and the value snaps back.
- **Store and forward is three settings**, and the third is re-saving the
  transmitter -- `sf-arm.sh`.
- **An Edge runs one project, named `Edge`.** And the first deploy onto its stub
  cannot be scanned in, because the AutoScan timer arrives inside it.
- **WebDev was trimmed.** `ign-modules-trim.sh` disabled it on every gateway
  before this demo existed; a disabled module 404s every route exactly like a
  deploy that did not land. It is on the keep-list now, and a gateway trimmed
  earlier needs the trim re-run and a restart.
- **A script that rebuilds the `wd` secret provider wholesale silently drops
  the token on every bootstrap.** `ign-secrets.sh` and the token-writing script
  merge into it instead of replacing it.
- **A UdtInstance configured over a Folder is dead** -- Good on write, members
  at Uncertain_InitialValue for ever. `sp_udt.provision` deletes first.
- **A WebDev handler must start with its `def` at byte 0**, or the route
  answers an empty 200 and logs nothing.

Found running this demo live, 11/09/2026:

- **Both new edges arrived trial-EXPIRED.** They ran four hours on the stub
  `Edge` project, and nothing resets a trial automatically. An expired Edge is dead in every
  way this demo needs -- WebDev answers `402 Trial Expired`, Transmission logs
  "Trial license is expired" and stops, no gateway timer runs -- while the
  project deploys and scans perfectly. `POST /data/api/v1/trial` resets it, and
  only while it is at zero.
- **A REST project scan exists**: `POST /data/api/v1/scan/projects` (and
  `/scan/config`), listed in 8.3.8's `/openapi.json`. It applied a first
  deploy onto a stub `Edge` with no restart. CLAUDE.md's "a script can drive
  neither trigger" is out of date.
- **The cloud learns an edge is gone from the BROKER, about 1.5 x keepalive
  late.** On MQTT Distributor with keepalive 10 s, a network cut gets the
  broker's Last Will to Engine in 14.9 s and the edge notices its own socket at
  18.5 s (T-D12); at keepalive 30 s it was 44.7 s and 38.3 s. Until then Engine
  shows the node ONLINE with stale values reading Good, and nothing in any log
  says otherwise. A **silent broker-side drop would never tell it at all**:
  that is what EMQX's per-client kick did (T6), and Distributor has no kick to
  do it with. So judge an edge's link from the edge side, or from the wire --
  never from Engine's Online tag alone.
- **The block flags gate tag WRITES, not alarm ACKS.** A cloud ack goes out as
  a DCMD whatever `blockDeviceCommands` says.
- **Every save of Engine's `general`, and of a transmitter, re-births every edge**
  -- the first by NCMD Rebirth, the second by a session bounce. Test runs
  started right after a config save measure the rebirth.
- **The hub had no alarm journal**, and the journal type's `profile.queryOnly`
  is required even though a POST without it answers 200.
- **The journal files a propagated alarm under the EDGE's source path**
  (`prov:edge:/tag:...`), not the `prot:MQTT:/src:...` path the status table uses.
- **`getState()` renders as "Active, Unacknowledged"**, not `ActiveUnacked`.
- **5.0.4 has no tag called Rebirth** on the edge. `Refresh Edge Node` under
  the node's status folder is the lever, and it is a full session bounce.
- **Transmission does not re-birth after a UDT definition changes**, and Engine
  never deletes a member that drops out of a birth -- it goes `Bad_Stale` and stays.
- **Tag history from store-and-forward reaches Engine and is stored nowhere**
  unless the Engine tags have history enabled.
