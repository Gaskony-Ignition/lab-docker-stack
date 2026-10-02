# MQTT Distributor — the broker as an Ignition module

What happens when the broker is Cirrus Link's MQTT Distributor instead of EMQX,
in the two shapes customers buy: **T1**, Distributor on the main gateway (here
the hub's redundant pair), and **T2**, a small standalone Ignition gateway that
runs nothing but Distributor. Round 1 (T-D1 to T-D8) measured T1 and found
eight problems; round 2 (T-D9 to T-D17) set out to resolve each one with a
configuration that was tested, because a customer on the module will hit the
same eight. All measured 18/09/2026 on 8.3.8 and Cirrus Link 5.0.4, beside the
working Sparkplug demo, which stayed on EMQX throughout.

## Recommendation

**Both topologies work, and six of the eight round-1 problems now have a
measured fix.** T2 is the one to recommend. The demo moved to **T1** anyway,
on purpose (*The migration* below).
Do the migration as the tasks below. Don't switch the demo by flipping URLs:
two of the fixes are settings every edge needs, and one is a database change.

| # | round-1 problem | status | the fix, measured |
|---|---|---|---|
| 1 | in-flight loss on failover | **resolved across failover** | Transmission's **Rolling History Buffer**, plus a de-duplicating history table: 0 alarm activations and 0 messages lost across a real stop, its automatic fail-back and six planned handovers, while the same station without it lost 2 activations (T-D11). **Not resolved for the case the buffer does not cover**: the T2 station still lost 2 messages, both in the 16:47 split-brain (measured in T-D11's table, cause in T-D16) -- with both halves active there is no handover for the buffer to replay into |
| 2 | Engine server set with no namespace consumes nothing | **resolved** | bind the namespace per set; `verify-demos` and `sparkplug-setup` now fail on it (T-D10) |
| 3 | no per-client kick on Distributor | **resolved** | cut the edge off a network that carries MQTT only; keepalive 10 s gets the cloud's LWT in 14.9 s (T-D12) |
| 4 | THE WIRE needs EMQX's rule engine | **resolved, differently** | not by an Engine custom namespace: `spBv1.0` is not a legal tag name. A 40-line MQTT-client sidecar posting to the existing route matched the rule 229/229 (T-D13) |
| 5 | standby runs no broker; lapsed trial runs none | **explained, not removable** | it's how the module is built; `verify-demos` now checks the licence of whatever gateway holds the broker (T-D14) |
| 6 | split-brain under host load | **rig artefact** | GAN ping timeout 300 ms under CPU starvation (PSI 33 %); tune `pingTimeout` on a VM (T-D16) |
| 7 | raw-MQTT notifications | **work** on both, and the RPC client follows a failover (T-D15) |
| 8 | TLS | **resolved** on both: the broker's certificate is the gateway's web certificate (T-D9) |

### What a customer should run

**T2, a standalone broker gateway (recommended).**

- Ignition **Edge** + MQTT Distributor. Distributor isn't part of Edge IIoT
  (that's Transmission only): it's licensed on top, per Inductive Automation.
  On the trial it runs (T-D9). A Standard gateway also works and costs more.
- TLS: install a CA-signed certificate as the gateway's **web** certificate
  (SANs = every name a client dials), then turn on TLS 8883 and turn off TCP,
  WebSocket and anonymous. One user per client role. Delete `admin/changeme`.
  Plan user changes for a quiet moment, because each one restarts the broker.
- Every edge: server set with **Primary Host ID**, a history store with the
  **Rolling History Buffer** on (max age ≥ 2 × keepalive), keepalive 10–30 s.
- Engine: the broker's server in a server set with **Sparkplug B bound to it**.
- History: a de-duplicating history table, or a historian that ignores
  duplicate rows. Without it the replay aborts whole batches (T-D11).
- Limits: the edges still re-birth on every hub failover, because Engine's
  STATE goes offline and online again, but the broker doesn't move. The
  broker gateway is itself a single point of failure: licence it, and watch
  its trial on a demo rig (T-D14).

**T1, Distributor on the main gateway (small sites).**

- Everything in T2, plus: each half needs its **own** web certificate (it
  lives under `data/config/local`, which redundancy doesn't sync). Engine's
  server is `ssl://localhost:8883` in its own set. Every edge lists both halves.
- **Both halves licensed**: the standby's broker starts only when it goes
  active, and a lapsed licence starts none (T-D1, T-D7).
- Limits: every hub failover moves every edge (3.5–6.3 s, T-D2), and every
  split-brain gives two live brokers. The Rolling History Buffer is what makes
  the failover lossless (T-D11). Set the GAN `pingTimeout` for the host
  (T-D16).

### What the demo migration would take, with these fixes

| # | task | size |
|---|---|---|
| 1 | Promote `labs/ignition-broker` to `stacks/` + a demos.json place + `broker.test` alias; `distributor-setup.sh` already does the certificate, TLS, user and `wd-mqtt` network | S |
| 2 | `ign-mqtt.sh` / `sparkplug-setup.sh`: edge servers `ssl://mqtt-broker:8883` with `primaryHostId`, keepalive 10, a rolling-buffer history store (in `sf-arm.sh`); Engine server in its own set + namespace binding (now checked) | M |
| 3 | History: `stringvalue` wide enough for EdgeNotice (or stop historising it), and the duplicate-ignoring rule on each new `sqlt_data` partition, created by a script, not by hand | M |
| 4 | THE WIRE: the bridge container (T-D13) as a stack on `wd-mqtt`, posting to `sparkplug/wire`; retire `sparkplug-witness.sh` | S |
| 5 | Cut Edge 3 / Store & Forward MQTT cut: a `wd-control` action `docker network disconnect/connect wd-mqtt <edge>`; rewrite the "cloud never learns" scenario as "the cloud learns after the keepalive, the edge replays" (T-D12) | M |
| 6 | Edges and the hub join `wd-mqtt` in their compose files instead of by `docker network connect` | S |
| 7 | Retire EMQX: the stack, its API key (`sf-emqx-key.sh`), its certs and port; update SPARKPLUG.md, STORE-FORWARD.md, MQTTS.md | M |
| 8 | Re-run SPARKPLUG.md T1–T10 and the Store & Forward measurements on the new road | L |

About a week and a half of rig time, down from two to three weeks: the risky
items from round 1 (THE WIRE, the cut) now have a working mechanism.

## The migration: T1, phase A (18/09/2026)

The owner chose **T1** over T2, knowing it couples the broker's failover to the
hub's and needs both halves licensed. The demo now runs on it. Phase B (the
wire panel's MQTT listener in `wd-control`) and phase C (the console off EMQX,
and EMQX retired) followed the same day; see *Phase C* below.

| where | what | made by |
|---|---|---|
| network | `wd-mqtt`, MQTT only, joined in compose by `ignition` (alias `mqtt-master`), `ignition-backup` (`mqtt-backup`), `ignition-edge2`, `-edge3`, `-edge4`; created beside `backbone` by `lib.sh:ensure_mqtt_net`. `ignition-edge1` isn't on it: its road is the Gateway Network and its transmitter stays disabled | compose |
| broker | Distributor 5.0.4 on both halves, TLS-only 8883, user `ignition` (password `MQTT_PASSWORD` in `.secrets.env`). Each half's own web certificate: `ignition localhost mqtt-master` / `ignition-backup localhost mqtt-backup` | `distributor-setup.sh <half>` |
| edges | server set `Default`, `primaryHostId IamHost`; `Hub A Master` `ssl://mqtt-master:8883`, then `Hub B Backup` `ssl://mqtt-backup:8883`; keepalive 10; RPC client CA and credentials; `Default In-Memory Store` with the Rolling History Buffer, 60 s | `ign-mqtt.sh setup`, `sf-arm.sh` |
| Engine | server `Distributor` `ssl://localhost:8883` in set `Distributor`, with `Sparkplug B` and `AlarmDemoNotify` bound to it before the server exists. `Chariot SCADA` (EMQX) deleted | `ign-mqtt.sh setup` |
| history | every `sqlt_data_*` partition skips a duplicate `(tagid, t_stamp)` (row trigger) and has `stringvalue text`; an event trigger on `CREATE TABLE` does both to each partition Ignition creates later | `pg-history-guard.sh` |
| EMQX | out of the `sparkplug` and `store-forward` demos; kept in the core until phase C retired it | `demos.json` |

All of it is idempotent. `ign-gw.js mqtt-server` reads each resource and writes
only what differs: a second `sf-setup` made 0 writes on four gateways.
Bootstrap runs `pg-history-guard` after `pg-ensure`, `distributor-setup
ignition` then `ign-mqtt setup` at step 13, and `distributor-setup
ignition-backup` after pairing. `sparkplug-setup` and `sf-setup` also run the
broker and guard steps.

**The history guard, proved.** `pg-history-guard.sh --prove` creates next
month's partition (`sqlt_data_1_2026_10`) as Ignition's own role, exactly as
Ignition does (`varchar(255)`, no trigger), then drops it: it came out `text`,
and 5 rows offered in two batches (a duplicate inside one batch, a replayed one
in the next, a 600-character string) stored 3 with no error. It's a row
trigger, not round 2's rule, because a row trigger also sees rows inserted
earlier in the same statement. If it fails it raises a warning and never blocks
Ignition's `CREATE TABLE`.

**Failover, measured.** A throwaway `Pumps/Probe` folder on each edge
(`Level` alarms every 8 s at :08, :16 … :56, auto-ack) was counted against
`AlarmDemoJournal`'s active rows, per expected slot. Planned handovers through
`./wd redundancy-failover`:

| run | broker up on the new half | edges' NBIRTH there | activations expected / missing |
|---|---|---|---|
| P1 master → backup, 19:37 | 19:37:32.4 | 19:38:09.6 (see below) | not counted: the probe wasn't in a device yet |
| P2 backup → master, 19:45 | 19:45:12.9 | STATE online 19:45:16.4 (3.5 s) | 15 / **0** |
| P3 master → backup, 19:47 | 19:47:30.5 | history flushed 19:47:35.1 (4.6 s) | 18 / **0** |
| P4 backup → master, 19:49 | 19:49:50.7 | 19:49:53.0 (2.3 s), 526 historical metrics | 15 / **0** |
| 19:44:20–19:51:15 | three handovers | | 48 / **0** per edge |

No `duplicate key`, `value too long` or `Error forwarding` on either half
throughout. Two rig effects stretched the timings. First, the half that has
just gone active resets its trial, and a licence event restarts Engine's
clients (T-D5): the edges re-birth a second time 20–35 s later (P2, P3). No
loss. Second, P1's backup JVM stalled for about 15 s under host load (log lines
printed 15 s late, load average 22). The edges reached its broker but got no
answer, hit the keepalive timeout at 19:37:55, and re-birthed at 19:38:09.
That window (19:37:32–56) lost 4 of the probe's activations. At that point
the probe folder hadn't been in any birth, so the rolling buffer had no metrics
of it to replay. How its alarm events reached Engine at all wasn't traced.
That's why the probe was moved under `Pumps` and re-birthed before P2. The
demo's own stations are in `Pumps`.

**What the console lost at the cut-over** (phase C's list, all fixed in
*Phase C* below):

- *Cut Edge 3* (Sparkplug) and Site 2's MQTT break (Store & Forward) ban and
  kick at EMQX. No edge is on EMQX now, so the call succeeds and nothing is
  cut. Phase C: `docker network disconnect wd-mqtt <edge>` (T-D12).
- The Store & Forward card's view of the ban list comes from EMQX, so it can
  show a cut that isn't happening.
- THE WIRE: the EMQX rule sees no traffic. Phase B's listener
  (`wd-control-wire`) was seen connected to the active half's Distributor.
- Text and links: `sparkplug_demo` still says `Sparkplug B over
  ssl://emqx:8883`, and the Gateways popup links to the EMQX dashboard.

## Phase C: the console off EMQX, and EMQX retired (18/09/2026)

| was | now |
|---|---|
| Cut Edge 3 / Site 2's MQTT break: a `peerhost` ban + kick at EMQX, which cut nothing once the edges had moved | `wd-control` takes the edge off `wd-mqtt` (`POST /mqtt/cut`, token-protected, deadline persisted and restored after a restart; `control/mqttcut.py`, DEMO-CONSOLE.md *Cutting an edge off MQTT*) |
| the Store & Forward card read EMQX's ban list | it reads the cut from wd-control's `/state`, with docker's own network membership beside it |
| THE WIRE: an EMQX rule, `scripts/sparkplug-witness.sh` | `wd-control`'s listener (phase B); the script is deleted, the WebDev route stays |
| "Sparkplug B over ssl://emqx:8883"; Gateways popup → EMQX dashboard; EMQX credential chip | "MQTT Distributor on the hub (ssl://mqtt-master:8883, fails over to mqtt-backup)"; Gateways → `https://console.test/_wd/login?next=/app/mqtt-distributor`, the module's own settings page (its mount, from the hub's `/data/app/navigation`, opened signed in); no chip |
| `sf-emqx-key.sh` in `sf-setup` and bootstrap | deleted |
| the listener's login from `stacks/emqx/.env` | `MQTT_PASSWORD` from `.secrets.env`, which `make env` now generates without any stack's `.env.example` |
| EMQX in the core, `stacks/emqx/`, `emqx.test`, its certs, ports, `make certs` step | gone. Container stopped and removed; its volumes `emqx_emqx_data`, `emqx_emqx_log` and `wd-emqx-certs` left for the owner to delete |

**The cut, measured** (click-tested in the page, keepalive 10 s):

| | cut → Last Will (NDEATH) | Restore → NBIRTH | inside the cut |
|---|---|---|---|
| Edge 3, 40 s | 15.7 s after the click (T-D12 had 14.9 s from the disconnect) | 3.4 s | 41 heartbeats stored with original timestamps; 111 of 111 across the 2-minute window |
| Site 2, 40 s | 15.4 s | 2.7 s | 40 FlowRate values, largest gap 2.0 s |

So the "cloud never learns" scenario of SPARKPLUG.md T6 (EMQX) is now "the
cloud learns after 1.5 x keepalive, and the edge replays": the page's words say
so.

**The listener across a real failover** (planned handover and hand-back): it
moved to `ignition-backup:8883` one second after the broker did, and back the
same second, posting to whichever half was active; received = forwarded, 0
dropped, 0 rejected. A Transmission JVM stalled 19 s once during the test
setup (two gateways starting under load) and re-birthed by itself: rig load,
not the cut.

## How it was built

Nothing on the existing road changed: Edge3/Edge4's `Example Transmitter` → `ssl://emqx:8883`,
and the hub Engine's `Chariot SCADA` → EMQX. The spike ran beside them:

| where | what |
|---|---|
| hub + backup | MQTT Distributor **5.0.4** (b2026072816), via `modules.manifest` → `get-modules.sh` → `ign-modules.sh --yes ignition` + `ign-gw.js commission`. The backup got it by redundancy module sync |
| hub Distributor | user `ignition` (the EMQX credentials, encrypted through `/data/api/v1/encryption/encrypt`, never printed); default `admin/changeme` deleted. Plain TCP 1883 on `backbone`; nothing published to the host |
| hub Engine | server `Distributor Spike` → `tcp://localhost:1883`, so each half's Engine talks to its own broker |
| edge4 | a second transmitter `Dist Spike`: group `AlarmDemoDist`, node `Edge4Dist`, tag path `DistSpike`, history store `Dist Spike Store`, server set `DistSet` (`primaryHostId IamHost`) listing `Dist A Primary` `tcp://ignition:1883`, then `Dist B Backup` `tcp://ignition-backup:1883` |
| edge4 tags | UDT `SpikeStation`: `Level` (expression, alarm `Level High` > 85, manual ack) and `Clock` (`toMillis(now(1000))`). Its own tags, so no spike alarm reaches the demo's alarm panels or `EdgeNotify` |
| hub | a throwaway WebDev project `DistSpike`: Engine tags, alarm status and `queryTagHistory` for `AlarmDemoDist`, read in process on whichever half answered |
| probes | a throwaway `python:3.14.7-slim-trixie` + `paho-mqtt 2.1.0` image on `backbone`: connect probes, and a Sparkplug decoder recording each broker |

**Version.** 5.0.4 is the newest Cirrus Link 8.3 release (28/07/2026, release notes on
docs.chariot.io), and it matches Engine and Transmission anyway, as it must: *"When
installing Cirrus Link modules in Ignition, all module versions must match. If they
are not, class loading issues may result in system instability."*

**TLS.** Plain 1883 was used for the spike. TLS needs a keystore resource and a new
leaf certificate with two container SANs, and that is migration work (task 2), not a
question this spike had to answer.

**`ign-modules-trim.sh` would have disabled it** on its next run: every module not
on the keep-list is set `disabled`. Distributor is on the keep-list now.

All of it was removed afterwards except the module and its user (see *Undo*).

## What we measured

### T-D1: the Distributor only listens on the ACTIVE half (Q1)

```text
12:51:36  hub  DistributorGwHook: Not starting MQTT Server because redundancy state is not active
12:52:16  hub  DistributorGwHook: Transition to Active State ... Attempting to start the MQTT Server
          hub  /proc/net/tcp: 0.0.0.0:075B (1883) LISTEN
13:13:58  probe ignition-backup:1883  ConnectionRefusedError      (Warm)
13:16:32  backup DistributorGwHook: Transition to Inactive State ... Stopping MQTT server...
13:16:41, 13:24:54, 13:45:40, 13:55:41  probe ignition-backup:1883  ConnectionRefusedError  (Warm, trial valid)
```

**No. A Warm standby's Distributor refuses connections.** The module is installed
and configured, but the broker only starts on the transition to Active and stops on
the way back. Engine is the same: `Not starting MQTT Clients. Redundant instance not
active`. Cirrus Link's *"the Standby Activity Level ... has no impact on the MQTT
modules"* holds only because the modules skip every standby, whatever its level
(Cold wasn't tested). On a Warm standby, the MQTT modules are inert. The edge's server list is the only thing that
gets it to the other broker.

**A Warm standby does serve WebDev.** The `DistSpike` route answered on the backup
throughout (it reads tags; it runs no project scripts). That is how the backup's Engine
state was read while it was Warm.

### T-D2: failover and fail-back times (Q2)

With the final configuration (Distributor server in its own Engine server set, T-D6):

| event | gateway | Engine subscribed | edge connected | edge NBIRTH | total |
|---|---|---|---|---|---|
| planned handover, 14:06 | backup Active 14:06:49.46 | 14:06:50.84 | 14:06:52.21 | 14:06:52.94 | **3.5 s** (Online 14:06:53.22) |
| planned hand-back, 14:09 | master Active 14:09:43.44 | 14:09:44.43 | 14:09:46.34 | 14:09:47.07 | **3.6 s** |
| `redundancy-fail` 90 s, 14:19 | master TERM 14:19:44, backup Active 14:19:45.55 | 14:19:47.51 | 14:19:48.75 | 14:19:49.47 | **5.5 s** from TERM |
| automatic fail-back, 14:23 | master Active 14:23:23.02 | 14:23:25.10 | 14:23:28.64 | 14:23:29.35 | **6.3 s** |

How the edge learns to move: on a clean stop, Engine publishes `STATE/IamHost
{"online":false}` on its way down, and the edge acts on that, not on a TCP error:

```text
14:19:47.398  edge4  Critical/Primary app went OFFLINE - disconnecting from this server for AlarmDemoDist/Edge4Dist
14:19:48.745  edge4  Connected to tcp://ignition-backup:1883
14:19:49.473  edge4  Publishing NBIRTH on Topic: spBv1.0/AlarmDemoDist/NBIRTH/Edge4Dist
```

With the **first** configuration (both Engine servers in `Default Set`), the same
failover took **62 s**, and the fail-back after a master restart never completed:

```text
13:40:00      master  TERM trapped.  Shutting down.         (redundancy-fail SECS=150)
13:40:04.677  backup  Redundancy state changed: Role=Backup, Activity level=Active
13:40:05.974  backup  Adding MQTT Client 'ME-f0fa3a10…' for server name: Distributor Spike    <- added, not started
13:40:06.830  backup  Distributor CONNECT MT-5344… (edge4)  -> SUBSCRIBE spBv1.0/STATE/IamHost -> waits
13:41:00.698  backup  Trial time reset ... Adding MQTT Client … Distributor Spike              <- a licence event starts it
13:41:01.866  backup  ME-f0fa3a10…: Connected to tcp://localhost:1883
13:41:01.918  wire    spBv1.0/AlarmDemoDist/NBIRTH/Edge4Dist
13:45:30.056  master  Adding MQTT Client 'ME-5126ad06…' for server name: Distributor Spike    <- never started
13:45:32 → 13:53  edge4  reconnects every 35 s, "Waiting for primary host IamHost to be online"
```

In four transitions with two servers in one set, Engine started the second server's
client once promptly (13:16:30, +3 s), once only because a trial reset happened to
fire (13:41:00, +56 s), and twice not at all. After the move to its own set, it
started within 1–2 s on every one of the seven transitions measured.

**The `./wd` wrapper was the slow part.** Under this host's load, `./wd
redundancy-failover` took up to ~2 minutes to issue a request the gateway acted on in
under a second.

### T-D3: buffered values and alarms arrive with original timestamps (Q3)

**Yes, for what the edge knew it couldn't deliver.** No, for what it handed to a
broker that was already dying.

The unplanned stop at 13:40, decoded from the recorder on the backup's broker:

```text
13:41:02.304  DDATA/Edge4Dist/Probe  seq=4  61 metrics, all is_historical, ts 13:40:05.663 .. 13:41:01.773
              includes Station/Level alarm metrics  ts 13:40:20 (clear)  and  13:41:00 (active), historical
```

and what the master's broker accepted in its last seconds (it took TERM at 13:40:00):

```text
13:40:01.703  DDATA  alarm metric Station/Level ts 13:40:00.636 (the 13:40:00 activation)   live, QoS0
13:40:02.698  DDATA  live
13:40:04.715  DDATA  live
```

Those three never reached a historian or an alarm table. The history has a hole
13:40:02.663 → 13:40:05.663. **The 13:40:00 alarm event exists on neither half.**
The backup later got its historical *clear* (13:40:20) for an event it had never seen.

The other buffers flushed exactly as documented for EMQX:

| when | why buffered | flush |
|---|---|---|
| 13:08:54 → 13:16:35 | connected, but no Engine on that broker yet (primary host offline) | **435** historical metrics, 13:16:37 |
| each planned handover | the ~2 s between brokers | 2 metrics, e.g. ts 14:06:50.252..14:06:51.253 |

History across the two planned handovers (14:05:38–14:11:50, 1 Hz source): 335 rows;
gaps 304×1 s, 21×2 s, 6×3 s, 2×4 s. That's the baseline of a 1 s clock under a 1 s
`tagPacingPeriod`. The handovers add at most one 4 s gap.

One gap not caused by the broker: after the 14:19 takeover, the history misses
14:19:52 → 14:20:14. The wire shows **the edge itself** published nothing from
14:19:50.7 to 14:20:13.3. Host load average was 40–43 all afternoon.

### T-D4: the cloud rebuilds from the edge, not from its peer (Q4)

- **Templates and instances rebuild from the NBIRTH.** On whichever half was active:
  `_types_` = `[PumpStation, SpikeStation]`, and the instance
  `Edge Nodes/AlarmDemoDist/Edge4Dist/Probe/Station` was a `UdtInstance` of
  `SpikeStation` (backup 13:55, master 14:05).
- **Alarm status is per half, and redundancy does not carry it.** After the 13:45
  fail-back the master listed only its own pre-failover event (13:39); the five
  events the backup had raised (13:41–13:45) weren't there. What restores it is
  the edge's rebirth: the NBIRTH replays the edge's **current** alarm table, which
  holds up to the Live Event Limit (5) of unacknowledged events per alarm. At
  14:05:42 the master gained 14:02–14:06, which had never reached it. `8bca59eb`,
  raised while the backup was active (14:07), was on the master after the 14:09
  hand-back.
- **Ack state survives, because the edge holds it.** A cloud ack on the backup went
  out as a DCMD and landed on the edge in 61 ms:

  ```text
  14:09:08       backup  system.alarm.acknowledge(['6bf59eed…'])
  14:09:09.070   edge4   Alarm (id=6bf59eed-…) has been acknowledged by usr:dist-spike@Ignition-Standard
  ```

  After the hand-back the event was gone from the master, while its unacknowledged
  siblings were still listed. An ack on the master before the 14:06 handover left
  the backup the same way.
- **Engine tag history settings don't survive a restart.** After the master's
  restart the `Clock` tag had to have `historyEnabled` set again. It's the same
  trap `sparkplug_demo.ensure_history()` exists for.
- Pre-existing, not the spike's doing: `system.alarm.queryStatus()` returns each
  Engine alarm **twice**. The demo's own EMQX alarms: 50 rows, 25 distinct ids (14:18).
  *Settled 22/09/2026 (SPARKPLUG.md, Three field questions, A): two objects per
  event from `queryStatus` only; the Alarm Status Table and the journal show one.*

### T-D5: what does not carry across, and what that costs (Q5)

| thing | observed |
|---|---|
| retained `STATE/IamHost` | none on any freshly started broker (backup 13:40:06; master 14:03:16, 14:09:43). Each new active Engine publishes it within 1–2 s. The edge waits for it and buffers meanwhile, so nothing is lost by the absence |
| NBIRTH / DBIRTH | not retained (the spec). Every broker switch = a full rebirth with a new `bdSeq`; this edge's reached 66 |
| sessions and subscriptions | clean sessions; the edge re-subscribes `NCMD`/`DCMD`/`STATE` on every connect (Distributor `SUBSCRIBE` lines) |
| in-flight QoS0 to a stopping broker | **lost**: three DDATA including an alarm activation (T-D3) |
| alarm table | per half, rebuilt only from the edge's birth (T-D4) |
| Last Will | the Distributor publishes it. A client cut by `docker network disconnect` at 14:16:28.490: `Keep Alive Timeout detected` 14:17:06.171, LWT 14:17:06.174, **37.7 s** (keepalive 30). An EMQX kick publishes none (SPARKPLUG.md T6) |

**Trap: an Engine server in a set with no namespace publishes STATE online and
consumes nothing.** Moving `Distributor Spike` into its own set `DistSet` (13:53)
left `Sparkplug B` bound only to `Default Set`. Engine then connected, published
`STATE/IamHost {"online":true}` and subscribed to **nothing but** `STATE/IamHost`.
The edge trusted the STATE, published live and buffered nothing:

```text
13:55:36 → 14:03:17  247 DDATA (31 alarm metrics) delivered to the backup's broker; consumed by no one
14:03:19             edge flushes 2 historical metrics, because it never knew
```

Seven and a half minutes gone, and every status read Good. Binding the namespace
(`Sparkplug B-DistSet`, 14:05:20) fixed it at once (`Subscribing on default namespace
topic: spBv1.0/# (server name: Distributor Spike)`, 14:05:38). The alarm half of the loss
came back on the next birth (T-D4); the tag history did not.

**Trap: every Transmission server, server-set or transmitter create/delete restarts
every transmitter on that edge.** Adding the spike's resources bounced the demo's own
`Example Transmitter` three times (NDEATH/NBIRTH on EMQX at 13:07:53, 13:08:13,
13:08:33), and removing them twice more (14:27:43, 14:28:18).

**Trap: any change to a Distributor user restarts the whole broker.** Deleting
`admin` (12:56:42): `UserResource removed: admin` → `Stopping the MQTT Server` →
`Starting Chariot MQTT Server`. Every client is dropped. There is no per-client ACL
change without a full outage.

**The pair split-brained four times on this rig.** The Gateway Network link timed
out (`onClose ... Connection Idle Timeout`) at 13:24:13, 13:39:36, 13:46:22 and 13:53:45.
Each time the backup went **Active beside an active master** for 7–23 s. Both
Distributors then accepted connections (probe 13:24:47, 13:46:37), and each Engine
published `STATE/IamHost online` on its own broker. The edge would have been
content on either. Host load average was ~43; this is the rig's load, but a
broker-in-gateway design turns every such blip into edges moving.

**Trial rig only: a licence event restarts Engine's clients.** Each trial reset on the
active half runs `Starting the MQTT Clients`. At 14:20:31 that bounced the
Distributor session and the edge rebirthed. At 13:55:30 it reconnected under the
same client id, and the Distributor ended the old session (`Active client session ...
already exists, ending it [LWT`). A licensed gateway has no trial resets.

### T-D6: the configuration that works (Engine side)

```text
server-set            DistSet               primaryHostEnabled true, primaryHostId IamHost
server                Distributor Spike     tcp://localhost:1883, serverSet DistSet
namespace-server-set  Sparkplug B-DistSet   namespace "Sparkplug B" -> DistSet     <- without this, T-D5's trap
```

`localhost` is deliberate: the Engine config is synchronised to the backup, so each
half's Engine talks to its own broker, which is the only one listening when that half is active.

### T-D7: licensing (Q6)

- **It runs on the rolling 2-hour trial like the other Cirrus Link modules:**
  `Trial license is active`, `License State Trial Active`. A reset on a running
  broker is a no-op (`The MQTT Server is already running`, 12:58:28, 13:08:38).
- **An expired trial means no broker at all.** The backup came up expired and
  logged `License State Trial Expired ... Not stopping the MQTT Server, it is not running`
  (13:13:37). When it went Active with that trial at 13:15:17, the edge got `Unable
  to connect to server` from **both** brokers until the swap's trial reset started it
  (13:15:49, `Attempting to start the MQTT Server`). A running broker hitting
  expiry wasn't observed.
- REDUNDANCY.md already says a Warm standby can't keep its own trial alive. With
  Distributor that stops being cosmetic: a lapsed backup is **a failover target
  with no broker**. `verify-demos` would have to treat the backup's trial as a
  Sparkplug readiness check, not just a Perspective one.
- For sale: Distributor Standard supports 50 simultaneous clients, Plus 250
  (Cirrus Link, *MQTT Distributor License Options*). Whether the backup half needs
  its own Distributor licence isn't documented. Ask Cirrus Link before quoting.

### T-D8: what the demo loses leaving EMQX (Q7)

| EMQX feature | replacement tried | result | cost |
|---|---|---|---|
| **THE WIRE**: rule engine POSTs every message to the witness route | Engine **custom namespace** on `spBv1.0/<group>/#`, `jsonPayload false` | ~~**works with `charset ISO_8859_1`**~~ **REVERSED by T-D13**: `spBv1.0` is not a legal 8.3 tag name, so the namespace creates no tag at all, and while it was bound Engine re-birthed every edge about every 10 s. What round 1 recorded: one tag per topic; the value re-encoded as Latin-1 decoded byte-exact (DDATA, 145 bytes, `seq 23`, ts 14:15:50–51). With the default `UTF_8` the protobuf is mangled into U+FFFD | a namespace + a gateway tag-change script calling `record_wire(topic, bytes)`. Holds only the latest message per topic, so a burst on one topic may coalesce (not measured). Saving a namespace bounces Engine's EMQX session (rebirth requests 14:13:34). Creating one binds it to **every** server set |
| | Paho from gateway Jython | **no**: `No module named paho`; `ClassNotFoundException` via the context and the Engine module's classloader | — |
| | Distributor-side hook | **none**: the `[MQTT Distributor]` provider has `Distributor Control/Enabled` and `Distributor Info/{Connected Clients, MQTT Clients (dataset), Total Clients}`, and nothing else | — |
| | a sidecar subscriber POSTing `{topic, payload_b64, qos}` to the existing route | feasible: the probe container above subscribes and decodes; not pointed at the live route, to keep the panel clean | one more container that must follow the **active** half (subscribe to both brokers) |
| **Cut Edge 3**: ban + kick one client | per-client kick or ban on the Distributor | **none exists**; a user/ACL change restarts the whole broker (T-D5) | — |
| | `docker network disconnect backbone <edge>` from `wd-control` | works as a cut; Distributor sends the Last Will after 37.7 s | **both edges are on `backbone` only**, so the console's observer (the honest edge-side signal) goes dark too. The scenario's point, "a kick gives no death, Engine keeps the edge Online", stops being true: the cloud learns in ~38 s |
| | disable the edge's transmitter / server | not tried: STORE-FORWARD.md already rejects a config write per click, and it publishes a clean NDEATH | — |
| **Store & Forward MQTT road**: same ban + kick on edge2 | as Cut Edge 3 | buffering itself works over Distributor (T-D3: 61 and 435 metrics, original timestamps) | the cut mechanism is the problem, as above. **Since measured, in T-D12**: a network disconnect does leave the edge publishing into a dead socket -- 38.3 s at keepalive 30 s, 18.5 s at keepalive 10 s, because a vanished interface does not error the socket. The rolling buffer covers those seconds as long as its max age is at least 1.5 x keepalive |

The reversal is marked in the THE WIRE row itself, so a reader who stops at the
table still gets it; T-D13 has the evidence.

## Round 2: resolving the eight

### The round-2 rig

Beside the EMQX demo, as round 1 was. Everything marked throwaway was removed
afterwards (see *Undo*).

| where | what |
|---|---|
| T1 | the hub pair, Distributor TLS-only on 8883 (`distributor-setup.sh`); Engine server `D2 T1` → `ssl://localhost:8883` in set `D2 T1 Set` |
| T2 | `labs/ignition-broker`: Ignition **Edge** 8.3.8 + Distributor 5.0.4, on `backbone` and on `wd-mqtt` as `mqtt-broker`; Engine server `D2 T2` → `ssl://mqtt-broker:8883` in `D2 T2 Set` |
| edge4 | three extra transmitters on one source: **Plain** (`Edge4Plain`, T1, ordinary in-memory store), **Roll** (`Edge4Roll`, T1, Rolling History Buffer on, max age 60 s), **T2** (`Edge4T2`, T2, rolling). Group `AlarmDemoDist`, server sets with `primaryHostId IamHost` |
| edge4 tags | `DistSpike/{Plain,Roll,T2}/Station/{Clock,Level}`: `Clock` = `toMillis(now(1000))`; `Level` alarms (auto-ack) every 4 s, so any failover window of a second or more holds an alarm transition |
| hub | throwaway WebDev project `DistSpike2`: Engine history of `Clock` per node, the journal's **active** rows per node, a copy of `sparkplug_demo.record_wire` |
| probes | `python:3.14.7-slim-trixie` + `paho-mqtt 2.1.0`: a recorder on each broker (both hub halves, T2, EMQX), and the WIRE bridge (T-D13) |

"Lost" below means a message the edge published live that neither Engine
stored in any form: none of its `Clock` values in history, and, for an alarm,
no **active** row in `AlarmDemoJournal`. That's measured per message, because
Engine keeps only the last value when one DDATA carries two values of a
metric (16:13:26 and :27 in one DDATA, only :27 stored). At 1 s pacing that
drops 2–4 values a minute in steady state, whatever the broker.

### T-D9: TLS on 8883, both topologies (issue 8)

```text
DistributorGwHook  IGNITION_KEYSTORE_FOLDER = "data/config/local/ignition/webserver/keystore/ssl.pfx"
                   and a fixed keystore passphrase, Ignition's own default    (javap, md-gateway-5.0.4.jar)
15:14     hub  POST /data/config/ssl/transition/ca-signed-certificate -> CA_SIGNED_CERTIFICATE
15:17     probe  ignition:8883  TLS ok TLSv1.3, subject CN=hub-broker, verified against the repo CA
15:32     ignition-broker  /proc/net/tcp: only :22B3 (8883) listening; TCP 1883 and WebSocket off
15:44     backup  web certificate NO_CERTIFICATE while Distributor general and users had synced
```

**Distributor has no keystore setting. Its TLS certificate is the gateway's own
web (HTTPS) certificate**, read from `ssl.pfx` with Ignition's default keystore passphrase, which the module hard-codes.
Install a CA-signed one the way the Config UI's Web Server page does (the
route above, found in `WebServerPage.chunk.bundle.js`), with a SAN for every
name a client dials, and turn TLS on. A certificate change restarts the broker
only while TLS is on (`TlsHandler`). `ssl.pfx` lives under `data/config/local`,
which redundancy doesn't sync: **each half needs its own certificate** while
every other Distributor setting follows the master. `scripts/distributor-setup.sh`
does all of it, idempotently.

**Edge edition runs Distributor on the trial** (`License State Trial Active`,
15:28:35; module `licenseState Trial`). For sale it's a separate licence on
Edge: Edge IIoT is Transmission only (IA forum, P. Griffith: *"if you want the
extra functionality of the additional modules you need to talk to our sales
department to get them added to your license(s) individually"*).

### T-D10: the subscribe-nothing trap, reproduced and now detected (issue 2)

```text
15:51:21  hub   Distributor SUBSCRIBE ME-15bc8602 (D2 T1) on [[spBv1.0/STATE/IamHost][1]]   <- and nothing else
15:55:30  hubA  NBIRTH/DBIRTH Edge4Plain, Edge4Roll: published, consumed by nobody; Engine holds no AlarmDemoDist tags (15:55:46)
~15:53    verify-demos  FAIL Engine server 'D2 T1' (ssl://localhost:8883) is in set 'D2 T1 Set', which has NO Sparkplug namespace bound
15:55:53  POST namespace-server-set 'Sparkplug B-D2 T1 Set', 'Sparkplug B-D2 T2 Set'
15:56:16  Engine: Edge4Plain/Edge4Roll/Edge4T2 Online
15:56:17  flush: Edge4Plain 12 historical metrics, Edge4Roll 98, Edge4T2 100
```

**The binding is `namespace-server-set` `{namespace: "Sparkplug B", serverSet: <set>}`, one per
server set.** Nothing else subscribes a set to `spBv1.0/#`. The detector is two
checks in `verify-demos` (sparkplug section) and one in `sparkplug-setup.sh`:
every enabled Engine server's set must have a `Sparkplug*` namespace bound, and
every connected edge's Heartbeat on the **active** half's Engine must be under 30 s old.

The rolling buffer (T-D11) recovered the trap's last 60 s when the binding
fixed it; the plain store recovered 12 metrics of 46 s. Binding a namespace
re-births every edge on the gateway (EMQX NBIRTH Edge3/Edge4 15:56:13-14).

### T-D11: in-flight loss on failover, and the fix (issue 1)

**Where the loss is.** Not in the unplanned stop. On `docker stop`, Engine
publishes `STATE offline` before it stops consuming: TERM 16:18:14.9, `STATE
{"online":false}` 16:18:17.24, edges buffered from there, and nothing was lost
by any node (U1). The loss is on the **planned** handover and the automatic
fail-back. The half standing down stops consuming as it goes Warm, then
disconnects its Engine clients **one server at a time, about 1.25 s each**,
publishing each server's `STATE offline` only when it gets to it:

```text
16:30:53.010  backup  Activity level=Warm
16:30:53.023  backup  Attempting disconnect and sending LWT ssl://emqx:8883
16:30:54.315  backup  Attempting disconnect and sending LWT ssl://mqtt-broker:8883
16:30:55.585  backup  Attempting disconnect and sending LWT ssl://localhost:8883
16:25:28.665  hubB    DDATA Edge4Plain seq=94  Level alarm a91a3547 active   <- backup Warm at 16:25:26.68, STATE offline later
              journal  a91a3547: a clear row only, no active row
```

Every message an edge publishes to that broker in the window (0.9–3.8 s here,
longer with more Engine servers) is consumed by no one. QoS can't help:
Engine subscribes QoS 0 (`Subscribing ... spBv1.0/# ... with QoS0`), Sparkplug
data is QoS 0, and the broker that took the message has no subscriber. The
demo's own EMQX edges have the same exposure: their server set has no
`primaryHostId`.

**The fix is Transmission's Rolling History Buffer** (history store,
`rollingHistoryBufferEnabled`, `rollingHistoryMaxAge` 60 = 2 × keepalive). The
store keeps every tag change for the last 60 s whatever the connection state,
and replays it as historical on every reconnect, so whatever the edge sent
into the window comes again. Engine journals a replayed activation as an
active event: 17 Roll activations reached Engine only as replays, and all 17
have active rows.

**It needs a history table that ignores duplicates.** The replay re-sends
values Engine already stored. Postgres rejects the duplicate, and the whole
store-and-forward batch goes with it, new rows included:

```text
16:15:19  hub  ERROR: duplicate key value violates unique constraint "sqlt_data_1_2026_09_pkey"  -> Error forwarding data
```

That cost Roll and T2 two messages each in P1. Cirrus Link's documentation
calls these errors *"benign ... no data loss"*, which isn't true for a
batching SQL historian. Fixed on the rig at 16:17:47 with a Postgres rule on
the partition, `ON INSERT ... WHERE EXISTS (same tagid, t_stamp) DO INSTEAD
NOTHING`. It isn't in the repo: it has to be created on each month's
partition (migration task 3).

Results, per node, messages lost / alarm activations lost (activations every 4 s from P2):

| run | what | Plain | Roll (rolling) | T2 (rolling, T2 broker) |
|---|---|---|---|---|
| P1 16:14:55 | planned, backup → master, before the rule | 1 / 0 | 2 / 0 (duplicate-key abort) | 2 / 0 (same) |
| U1 16:18:14 | `docker stop` of the active master, 95 s | 0 / 0 | 0 / 0 | 0 / 0 |
| U1b 16:21:17 | automatic fail-back on its return | 0 / 0 | 0 / 0 | 0 / 0 |
| P2 16:23:04 | planned, master → backup | 0 / 0 | 0 / 0 | 0 / 0 |
| P3 16:25:07 | planned, backup → master | 2 / **1** (a91a3547) | 0 / 0 | 0 / 0 |
| P4, P5 16:28:59, 16:30:36 | planned both ways | 1 / 0 | 0 / 0 | 0 / 0 |
| P6, P7 16:58:16, 17:02:20 | planned both ways | 3 / **1** (624da5d0) | 0 / 0 | 0 / 0 |
| 16:13–17:35 overall | ~1,100 activations per node | **3 lost** | **0** | 2, both in the 16:47 split-brain (T-D16) |

**Target met for failover: zero alarm activations lost, and no message lost,
across a real stop, its automatic fail-back and six planned handovers (P2–P7)
with the fix.** The station without it lost an activation in two of the six.

Found on the way, a pre-existing demo defect that fakes loss:
`sparkplug_demo.ensure_history()` historises `EdgeNotice`, whose JSON is over
255 characters, into `sqlt_data_*.stringvalue varchar(255)`. Each notice
aborts its whole batch, other tags' rows included: 18 aborted batches in 40
minutes on the master (`value too long for type character varying(255)`).
Round 1's history gaps (T-D3's "304×1 s, 21×2 s") are partly this. The
current partition's column was widened to `text` on the rig (16:11).

### T-D12: cutting one edge without an EMQX kick (issue 3)

(a) **A network that carries MQTT only.** `wd-mqtt`: the T2 broker answers
there as `mqtt-broker`, the only name in the edge's server URL. The edges keep
`backbone`, so the console's observer still works through the cut.

```text
16:35:00.27  docker network disconnect wd-mqtt ignition-edge4
16:35:05     observer GET edge4 .../sparkplug/state: answers (backbone)
16:35:38.56  edge4  Timed out as no activity, keepAlive=30 s -> MQTT connection lost      (+38.3 s)
16:35:44.93  broker Keep Alive Timeout detected -> NDEATH Edge4T2 (LWT)                    (+44.7 s)
16:36:04.90  docker network connect; 16:36:15.95 NBIRTH; 16:36:17.09 825 historical metrics
             Engine history 16:34:50-16:36:20: 1 value missing of 88 (the two-in-one-DDATA effect); 23 alarm events journaled
keepAlive 10: cut 16:39:10.18 -> broker LWT 16:39:25.11 (+14.9 s), edge notices 16:39:28.64 (+18.5 s); history 1 missing of 88
```

The cloud learns after 1.5 × keepalive, from the broker's LWT. The edge learns
about as late, from its own keepalive: a vanished interface doesn't error the
socket, so for 38 s the edge publishes into nothing. The rolling buffer covers
that, as long as its max age is at least 1.5 × keepalive. **This is what a
customer sees when a WAN link drops**, and it's the mechanism the demo
should use.

(b) **A Distributor user change restarts the whole broker.** Confirmed on T2:
`UserResource added` 16:41:06.634 → `Stopping Chariot MQTT Server` .636 → `Starting` .718;
recorder disconnected 16:41:06.717, Edge4T2 re-birthed 16:41:09.57. A per-user
ACL or disable can't cut one client. Ruled out.

(c) **Disabling the edge's transmitter** is a different story: an edge-side
outage. NDEATH at once (16:42:50.87), so the cloud knows immediately, but
nothing is collected while it's off: history hole 16:42:51 + 68 s, not
replayed. A `PUT` of `enabled` didn't bounce the edge's other transmitters,
where creating resources does (T-D17).

### T-D13: THE WIRE without EMQX's rule engine (issue 4)

**Not by an Engine custom namespace. Round 1's T-D8 finding doesn't hold:**

```text
16:49:35  hub  E StringPayloadHandler: Invalid tag at path 'DistWire/spBv1.0/AlarmDemo/DDATA/Edge3/Pumps'
16:50     POST tags/import folder 'spBv1.0' -> "The name 'spBv1.0' is not a valid tag name"
16:49:35-16:54:30  EMQX: NBIRTH Edge3/Edge4 every ~10 s ("Requesting Rebirth ... Received a message for edge node that is offline")
```

A period is not legal in an 8.3 tag name, and every Sparkplug topic starts
`spBv1.0`, so a custom namespace can never hold one. **Worse, a custom
namespace overlapping the Sparkplug topics made Engine re-birth every edge
about every 10 s for as long as it was bound: 60 rebirths in five minutes.**
Notify topics (`notify/...`) are fine, which is why `AlarmDemoNotify` works.

**By a sidecar MQTT client instead.** `bridge.py` (paho, about 40 lines)
subscribes `spBv1.0/AlarmDemo/#`, `spBv1.0/STATE/#` and `notify/#`, and POSTs
`{topic, payload_b64, qos}`, exactly the EMQX rule's body, into a copy of
`sparkplug_demo.record_wire`. Both ran side by side over the same 120 s on
EMQX, 16:55:00–16:57:01:

| | EMQX rule → witness | bridge → record_wire copy |
|---|---|---|
| DDATA | 229 | 229 |
| NOTIFY | 7 | 7 |
| in one, not the other | 0 | 0 |
| arrival, bridge minus rule, matched by topic + seq | | median +8 ms, max +37 ms (236 pairs) |

Against Distributor it's the same client. For T2 it subscribes `mqtt-broker`
and never moves. For T1 it has to list both halves (the standby refuses, T-D1)
and post through the front door, `ignition-ha`, so it reaches the active half.
It sees everything the broker delivers, including what Engine's own Sparkplug
namespace consumes. There is no Engine-side view of raw bytes to compare it with.

### T-D14: licensing, and what the demo must show (issue 5)

```text
T2  17:27:13.33  ignition-broker  License State Trial Expired -> Stopping the MQTT Server   (a RUNNING broker stops)
    17:27:13.51  hub  Connection Lost to D2 T2 -> Staling tags for Edge4T2   (Engine shows it offline at once)
    17:28:38.39  after POST /data/api/v1/trial: Starting Chariot MQTT Server
    17:28:40.08  edge4 NBIRTH Edge4T2; 17:28:41.43 Publishing 1230 historical metrics
                 history 17:26:50-17:29:30: 3 of 158 missing (two-in-one-DDATA), 39/39 activations journaled
T1  (round 1)  a lapsed backup started no broker when it went Active (T-D7); a Warm standby runs none (T-D1)
```

Round 1 hadn't seen a running broker reach expiry. **It stops, dropping every
client.** Unlike an EMQX outage, Engine learns at once, because its own
connection drops. The edges store and forward through it, and nothing was
lost in 85 s. The trial is two hours, and a broker gateway runs no project
and nothing resets it for you, so on this rig **T2 dies two hours after its last reset**
until a person resets it on the Trials page.

The console's *STANDBY CANNOT TAKE OVER* now has an MQTT meaning in T1: the
standby's broker won't start. `verify-demos`' sparkplug section finds the
gateway holding each broker Engine uses (`localhost`/`ignition*` → the pair,
`mqtt-broker` → `ignition-broker`) and fails under 30 minutes of trial,
pointing at the Trials page. A licensed gateway has none of
this. The Cirrus Link question from T-D7 is still open: does the standby half
need its own Distributor licence?

### T-D15: raw-MQTT notifications over Distributor (issue 7)

`system.cirruslink.transmission.publish(server, topic, ...)` from edge4, through a
throwaway route:

```text
16:58:00.874  'D2 Broker'     -> t2    notify/AlarmDemoDist/Edge4 at 16:58:01.891 (+17 ms)   returned in 7 ms
16:58:01.302  'D2 Hub Master' -> hubA  at 16:58:01.309 (+7 ms)
16:58:01.646  'D2 Hub Backup' -> hubA  at 16:58:01.649 (+3 ms)   <- the RPC client is per SET: the server name picks the set
16:58:37.579  during a handover: 'D2 Hub Master' returned ok in 2 ms, queued;
16:58:37.934  RPC client for 'D2 Hub Backup' connected to ssl://ignition-backup:8883 -> "Publishing queued RPC message" 16:58:38.588
```

**It works on both.** In T1 the RPC client follows the failover to the other
half and delivers what it queued, even with `autoReconnectRpcClient false`.
The RPC fields (`rpcCaCertFile`, `rpcUsername`, `rpcPassword`) are copied from
the main client, as SPARKPLUG.md C2 requires.

### T-D16: split-brain under load (issue 6)

```text
16:31:05.924  backup  Redundancy state changed ... Activity level=Active   ("Remote server forcibly closed the web socket: Ping")
16:31:10.109  backup  Warm                                                   (4.2 s beside an active master)
16:47:23.718  recorder: master's broker "Keep alive timeout" (no answer for 15 s); backup Active 16:47:23.746
16:47:46.600  backup  Warm                                                   (23 s)
load average  18 (16:46:08) -> 39.4 (16:47:39);  /proc/pressure/cpu some avg60 32.75 % (16:33)
redundancy    pingRate 1000 ms, pingTimeout 300 ms, pingMaxMissed 10 (the defaults)
```

**A rig artefact, and a configuration choice.** Both events coincide with CPU
starvation. At 16:47 the master stopped answering for 15 s. With a 300 ms ping
timeout, ten slow pings make the backup take over. A healthy dedicated host
doesn't do that, and a customer on a busy VM should raise `pingTimeout` to
1000–2000 ms: detection stays about ten seconds, set by `pingRate` × `pingMaxMissed`.
It matters more with the broker in the hub. At 16:47 both brokers were live,
Plain lost an activation, and T2 lost two replayed ones. That's the one case
the rolling buffer didn't cover, so the pair's timeouts are part of the T1
configuration. Not chased further.

### T-D17: churn, for anyone repeating this

Each of these re-births the demo's own edges on EMQX:

| action | re-birth |
|---|---|
| POST Transmission server-set, server or transmitter on edge4 | Edge4 (15:54:47, 15:55:11; not the history-store POST) |
| PUT a Transmission server (keepalive) | Edge4 (16:38:03) |
| PUT a transmitter's `enabled` | none |
| POST/DELETE an Engine namespace or binding | both edges (15:56:13; 16:54:30) |
| a custom namespace overlapping `spBv1.0` | both edges, every ~10 s while bound |
| every hub failover | both edges (Engine's EMQX session moves) |

## Undo

Round 2's rig was removed. What the demo now runs on is the migration above,
not something to undo. Still there from the rounds:

| change | undo |
|---|---|
| `labs/ignition-broker` (T2): container **stopped**, volume `ignition-broker_broker_data`, `modules/ignition-broker/`. It joins `wd-mqtt` as external now | `docker compose -f labs/ignition-broker/compose.yaml down -v` |
| round 1's stale Engine custom namespace `DistWire` | deleted in round 2, nothing to undo |
| journal rows of round 2's `DistSpike` alarms in `sp_alarm_events` | harmless; the phase A probe's rows were deleted |
