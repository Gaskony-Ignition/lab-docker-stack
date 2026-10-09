# Redundancy — the third demonstration

Two Standard gateways acting as one. The backup holds the same projects, the same
configuration and the same modules, and takes over when the active half stops.

```
make redundancy            pair them (idempotent — safe on every bootstrap)
make redundancy-status     what each half reports, side by side
make redundancy-failover   hand responsibility over — nothing restarts
make redundancy-fail       stop the ACTIVE half for real, then bring it back
```

The page is **GatewayAdmin → Redundancy**. Open it on whichever half is **active** —
a standby cannot serve a Perspective session at all, for a reason explained under
"The unplanned failure" below.

| | container | host HTTP | role |
|---|---|---|---|
| master | `ignition` | 29088 | Master, Active |
| backup | `ignition-backup` | 8388 | Backup, Warm standby |

---

## How the pair is configured

Redundancy is the one piece of gateway state that is genuinely file-based —
`data/redundancy.xml`, a plain Java properties file — and it is still wrong to write it
directly. The gateway reads that file at startup and on save, so a hand-edited copy needs
a restart; the REST route applies the same settings **live** -- a PUT
flips a running gateway from Independent to Backup in under a second, module sync and
all, with no restart anywhere.

```
GET  /data/api/v1/redundancy                      role, activity, peer, sync state
GET  /data/api/v1/redundancy/config               settings
PUT  /data/api/v1/redundancy/config               settings, applied immediately
GET  /data/api/v1/redundancy/providers            per-subsystem sync metrics
GET  /data/api/v1/redundancy/events               the redundancy log
POST /data/api/v1/redundancy/gwaction/failover
POST /data/api/v1/redundancy/gwaction/resync
```

Those last two are `gwaction`, not the bare verb. `/redundancy/failover` and
`/redundancy/resync` both 404, which reads exactly like the route not existing in this
version. The paths are in `RedundancyRoutes` in `gateway-8.3.8.jar`.

`gatewayNetworkSetup` in the config applies to the **backup only** — the gateway's own
OpenAPI description says so. The master never dials out; it is dialled into.

The backup reaches the master as `ignition:8060` — **a container name on the shared
`backbone` network, never a host port**. Host mappings differ per machine and are for a
browser on that machine; a pair that depended on them would not survive being cloned to
someone else's laptop.

### Three things block the pair, in the order you hit them

Every one of them fails in a way that looks like a different problem.

1. **TLS trust.** Redundancy rides the Gateway Network transport with SSL on, so each
   half must approve the other's certificate. The backup fails first with
   `InvalidAlgorithmParameterException: the trustAnchors parameter must be non-empty` —
   its trust store is **empty**, which is a different thing from a rejected certificate
   and sends you looking for the wrong fix. Once that is approved the **master** rejects
   the backup with `Received fatal alert: certificate_unknown`. Two approvals, one per
   direction, and the second is only possible after the first: the backup's certificate
   does not exist on the master until the backup has tried to connect, and it cannot try
   until it trusts the master. `ign-redundancy.sh` therefore runs `ign-gan.sh certs`
   **twice** with a wait between.

2. **Connection approval.** A trusted certificate is not an approved connection. Until
   the incoming connection is approved the handshake dies with
   `UpgradeException: ... 403 Forbidden`, which reads like a credentials problem.

3. **Module parity.** The backup then reports `Incompatible (Modules)` and **restarts
   itself** to install the master's third-party modules. This is Ignition doing the right
   thing unprompted — Embr Charts, the Architecture Builder and MQTT Engine all arrived
   on a bare backup with no certificate wizard and no intervention, which is worth
   knowing because installing them by hand raises the commissioning gate that
   302-redirects every request to `/welcome`. It does mean `setup` has to tolerate the
   backup disappearing for about a minute in the middle. **That restart is progress, not
   a failure.**

### The pair share one gateway name, and the backup carries no `-n`

On the first sync the backup takes the master's system name and appends its role:
`-n Ignition-Backup` became `Ignition-Standard-Backup` within seconds of pairing.

Because a compose `-n` argument re-applies on **every** container start, keeping it would
mean compose renaming the gateway on each boot and redundancy renaming it back on each
sync — a fight with no winner, visible to a customer as a name that changes while they
watch. So `stacks/ignition-backup/compose.yaml` deliberately has no `-n`. Compose owns
the name for an independent gateway; the master owns it here.

### It works on a trial licence

Both halves run the ordinary rolling 2-hour Perspective trial and redundancy is entirely
unaffected — roles, sync, module install and failover all work on two
unlicensed 8.3.8 gateways.

**The backup cannot maintain its own trial.** A Warm standby executes **no project
scripts at all**. Measured 07/08/2026 — six hours of the backup's log contains not one
line from any project script, while the master logged its own activity every minute. The
trial then expires exactly 7200s after whatever last reset it:

```text
16:26:23  Trial time reset.  Time remaining = 7200 ... route-path=/v1/trial
18:26:23  Trial expired
```

> Supersedes the 06/08/2026 note that a timer fired *erratically* here. It does not
> fire at all — the resets counted then were the moments this half was active. Worth
> the correction because it rules out a class of fix: no threshold and no in-project
> change can help when nothing in the project runs.

**Nor can the master do it for the backup — a redundant pair is one Gateway Network
server, not two.** Tested 07/08/2026 by asking the master what it can address:

```text
KNOWN SERVERS: ['_0:2:Ignition-Standard', '_0:0:Ignition-Edge2', '_0:0:Ignition-Edge1']
sendRequest 'Ignition-Standard'        -> HANDLER RAN on Ignition-Standard (Master)
sendRequest 'Ignition-Standard-Backup' -> IllegalArgumentException: Unknown gateway
```

The pair's logical name routes to whichever half is **active**, so the request comes
straight back to the sender, and the role-suffixed form is not a server at all. The
`sendRequest` trick that tops up the edges therefore cannot work here: the standby has
no Gateway Network identity to send to. It appears in redundancy's own log as
`gan-remote-gateway-name=Ignition-Standard-Backup`, which is precisely what makes it
look addressable when it is not.

Every reset also arms an expiry two hours out, so the backup is on a two-hour fuse from
each reset. On 8.3.9 nothing may cancel that expiry ([TRIALS.md](TRIALS.md)): a trial
whose expiry task never runs refuses every reset until the gateway restarts.

**A person resets it.** The standby's trial is its own and lapses on its own clock, like
any other gateway's. When it expires, press Reset on the Trials page
(https://console.test/_wd/trials, [TRIALS.md](TRIALS.md)), which signs in to the standby
directly and POSTs the reset. Nothing resets it automatically.

**`make verify-demos` fails an expired standby** and points at the Trials page as the
repair. A standby with little time left passes with the minutes left noted. Check it
before showing a failover, not during.

---

## Every file deploy throws a sync error, and that is expected

A `make deploy` is followed within ~10s by a stack trace in the **backup's** log:

```text
[projects] Error executing redundant state sync. 4 attempt(s) remaining.
  RedundancySyncException: PushException thrown
  Caused by: PushException: MODIFY illegal: signature mismatch for
    'ResourceId{resourcePath=<some resource>, collectionName=<some project>}'
[projects] Will perform full pull. Reason: redundant provider on local side has no data.
```

It looks alarming and it is not. Redundancy's project sync is **differential**
and its signature is an **optimistic lock**; a file deploy replaces resources
wholesale — `ign-deploy.sh` removes the old tree, strips signatures and bumps
timestamps — so the partial push's expected "from" signature cannot match by
construction. Ignition falls back to a full pull, which succeeds.

Two things make it safe to leave alone, both measured 06/08/2026 straight after
a deploy that produced the error:

- **The halves converge byte-for-byte.** A checksum over every file of every
  project matches on both — including the 1,383 of the style-class parent and
  the project that had just been deployed.
- **The resource it names is not the one you deployed.** Deploying `Ops` named a
  style class from the parent project; it is whichever resource the sync reaches
  first, so reading it as a clue about the deploy sends you the wrong way.

Do not "fix" it by re-running the deploy or by forcing a resync — both just do
the full pull again. The cost is one retry and a full pull per deploy, and the
alternative would mean fighting a sync protocol we do not control. What *would*
be worth investigating is a deploy that leaves the checksums differing; that is
the check to run, not the log.

## The pair's front door

`https://ignition.test` reaches **whichever half is active**, so a Perspective session
survives a failover instead of hanging at `Connecting`:

```text
https://ignition.test  ->  npm  ->  ignition-ha (HAProxy)  ->  the Active half
```

Ignition has no built-in answer to this. Inductive Automation's position is that HTTP load
balancing is a solved problem they will not try to improve on, and that a browser pointed
at one URL cannot find the backup without something in front to send it there; their
recommendation is an external reverse proxy in **active-passive**, selecting on
`/system/gwinfo`. `stacks/ignition-ha/conf/haproxy.cfg` is that proxy, and it carries the
reasoning in full.

Four things make it work, and every one of them is silent when wrong:

- **Both halves must carry `publicAddress: ignition.test`** (ports 80/443, auto-detect
  **off**). A standby redirects the client to the active node's *public address*; unless
  that is the shared origin, the browser blocks it. This setting is **not** synchronised
  by redundancy — it is per-gateway, and the backup was still auto-detecting its own
  container address long after the master had been set.
- **The health check reads the role out of the response body, not the status code.** Both
  halves answer 200; only the body differs (`RedundantNodeActiveStatus=Active` against
  `Warm`). A liveness check would keep sending traffic to a master that has handed over —
  which is exactly what the button below does, with nothing stopping to notice.
- **…and the body must also prove the half is IN THE PAIR.** `Active` alone does not: a
  gateway with no redundancy configured reports `RedundancyStatus=Independent` *and*
  `RedundantNodeActiveStatus=Active`, since from its own point of view it is the one in
  charge. Every Ignition container on the network therefore passes an Active-only check —
  **both edges included**. `http-check expect ! string RedundancyStatus=Independent`.
- **HAProxy must be told to re-resolve DNS**, because by default it resolves each server
  name once at its own startup and uses that address for the life of the process. The
  gateways restart routinely — the demo stops one from a button, `make up` re-creates them
  — while the proxy is a separate stack that stays up across all of it. `resolvers docker`
  (Docker's embedded DNS at `127.0.0.11:53`, short hold timers) plus `check resolvers
  docker` on each server.

**How that pair of gaps actually failed, 31/08/2026.** The gateways had restarted; Docker
handed their former addresses to other containers; HAProxy was still pointed at one of
them. The front door served **ignition-edge1** — Independent, Active, and so passing the
health check — which answered `project "GatewayAdmin" does not exist`. The stats page
showed a healthy backend, `make redundancy-status` showed a correctly paired pair, and
`make verify-demos` reported *"front door is serving the Active half"*, because it was
asking the same Active-only question. Everything on the rig was right except the one
address a customer would use. Both ends are closed now, and the verification names the
proxy restart as the repair.

**With the Redundancy demo stopped, `ignition.test` serves the hub** (23/09/2026). HAProxy
is part of that demo, so for most of the day the front door answered 502. `ignition-ha`'s
manifest carries `TEST_FALLBACK=ignition`, and `make proxy-hosts` writes a named location
into the proxy host: a 502 or 504 nginx makes itself (the proxy's name does not resolve,
or does not answer) is served by the hub instead. A 503 from a running HAProxy is not
caught, so while the pair's proxy is up nothing reaches the hub behind its back.
Measured 23/09/2026: demo stopped, `https://ignition.test/system/gwinfo` 200 from the
master and the console rendered through it (503 components, 0 error boxes); demo started,
the npm log reads `Sent-to ignition-ha`, and `make redundancy-failover` at 15:09:02 had
the front door reading `RedundancyStatus=Backup ... Active` by 15:09:32.

A planned handover moves the front door onto the backup in **4s** and
back in **5s**, and the full dashboard renders from the backup through the proxy.
`http://localhost:29404` is HAProxy's own page — it names the half in service and why the
other one is not. Reaching one half directly is what host ports 29088 and 29388 are for.

---

## The pair is also the MQTT broker

Since 18/09/2026 the Sparkplug and Store & Forward edges publish to MQTT
Distributor on this pair ([MQTT-DISTRIBUTOR.md](MQTT-DISTRIBUTOR.md), *The
migration*). A Warm standby runs no broker, so every handover moves every MQTT
edge as well: they follow in 2–5 s by their server list and replay the gap
from their rolling buffer. The standby's trial now matters to Sparkplug too,
because a lapsed standby that goes active starts no broker until it is reset. `verify-demos` checks it. Each half carries its own broker certificate
(`make distributor-setup`), because `data/config/local` doesn't sync.
wd-control's wire listener follows too, within a second each way, and so do
the console's MQTT cuts: the `wd-control-token` secret file lives in each
half's own volume, so `make secrets` writes it to the backup whenever the
backup is running (docs/DEMO-CONSOLE.md).

## Driving the demonstration

### 1. The planned handover — nothing goes down

**Hand over to the standby** on the page, or `make redundancy-failover`.

The gateways move responsibility in about a tenth of a second (*The proof*, below: 63-130 ms
from the logged request to the new half logging Active, six runs). Nothing restarts, and
pressing it again hands responsibility straight back. What takes longer is everything
around it: `./wd redundancy-failover` spends ~15-20 s starting the toolbox and logging in
before it sends the request, and the front door follows a few seconds after that. This is the version to show first: it is the
maintenance case (patch the master, hand over, patch it, hand back) and it is completely
safe to repeat in front of anyone.

**The screen you are showing goes blank for about half a minute, and it comes back on its
own.** A Perspective session is served by the half that was active when it connected, so a
handover drops it — that part is inherent, and no proxy can carry a websocket across two
JVMs. What Ignition *does* do is reconnect by itself: measured 31/08/2026 through the front
door, a session that was never touched came back **~30 seconds** after the handover, live
and current.

What it cannot carry across is **where you were**. The reconnect is a NEW session, so it
lands on the console's default tab rather than the Redundancy tab you were demonstrating
from — which is exactly what makes it feel like a manual refresh is required, and why the
first report of this was "I had to refresh the page". Reloading is not a repair, it is
simply faster than waiting. Say "watch it come back" and let it, or reload and click
Redundancy again.

> Corrected the same day. The page briefly told the demonstrator to "reload the page",
> which was written from the report rather than from a measurement. The first attempt to
> measure it was worse than none: it matched the string `No Connection to Gateway` in
> `body.innerText`, which Perspective carries in a **hidden** overlay at all times, and so
> reported the session as dead 17 seconds *before* the failover was issued. Screenshot the
> page and look at it; do not scrape it for text that is always there.

The button names its direction — **Hand over to the standby** while the master holds it,
**Hand back to the master** once the backup does. A button that describes the opposite of
what it does is worse than an unlabelled one: in front of a room it invites you to talk
the audience through a move that is not the one happening.

It is a **request to the peer**, so it must be issued from the half that currently holds
responsibility. Issued from a warm standby it is accepted and does nothing visible —
`redundancy_demo.failover()` guards against that and says which half to use, because a
silent no-op reads as a broken button.

> Perspective sessions on the half that gives up responsibility are restarted — the page
> stays up but returns to its first tab. That is Ignition stopping the projects on a
> gateway that is no longer active, not a fault.

**A handover can come back on its own.** Measured 28/09/2026, one of six: the backup went
Active at 12:50:19, then logged nothing for 30 s; at 12:50:51 its broker timed out the
edges' keep-alives and the master closed the Gateway Network link as idle. The link
re-formed at 12:50:54, the pair resynced, and at 12:51:14 the master took responsibility
back (automatic recovery) with nobody asking. It came 1 minute after the backup's first
full sync; a repeat 2 minutes after a backup restart held, the backup answering
`/system/gwinfo` every 2 s through a 4 s burst of 535% CPU. What silenced it is not
verified. If a handover reverts by itself, this is why: hand over again.

### 2. The unplanned failure — something really goes down

Press **Restart this gateway**, then open the other half's URL and watch. It is a
real gateway restart, not a container stop: the half goes down, its sessions drop, the
other half takes over, and it is back by itself in about a minute (with *Master returns*
set to Automatic, the master then takes control back).

The page is served by the active gateway, so it drops when you press the button. That is
not a limitation of the page — **a standby serves no Perspective session at all**, and the
reason is worth knowing before you plan a demonstration around it:

> A Perspective client that connects to a standby is redirected to the active node, which
> it reaches at the active node's *public address*. That is a different origin from the
> standby's, so the browser blocks the call and the session hangs at "Connecting" forever:
>
> ```text
> Access to XMLHttpRequest at 'http://ignition.test/data/perspective/redundancy/status'
> from origin 'http://localhost:29388' has been blocked by CORS policy
> ```
>
> This is not a DNS problem, even after a 45-second wait — `ignition.test`
> resolves fine. In production both halves sit behind one load balancer, the origins
> match, and the redirect is exactly the feature you want: your session follows the
> surviving gateway. With two separate host ports, it cannot.

So the flow is: press the button on the active half, then open the other half. It is
already active by the time you get there — measured at **one second** (below).

`IgnitionGateway.get().restart()` is the lever: a gateway-scope script is already inside
the JVM, so this needs no host command, no credentials, and cannot be left half-done. Note
that it restarts the **JVM**, not the container — Ignition runs under a service wrapper
that brings the JVM straight back, so `docker ps` never shows the gateway as down and the
container's restart policy is not involved. The gateway comes back in about a minute and —
with recovery **Automatic** — takes its responsibility back on return.

`redundancy_demo.stop_peer()` exists for the load-balanced case, where a standby session is
possible and the survivor can take the other half down without losing the page. On this
stack it is unreachable, and it is kept and commented rather than deleted because the
deployment it is right for is the one this stack imitates.

#### Measured, 05/08/2026

Both directions, from the button, on unlicensed 8.3.8 gateways:

| | master stopped | backup stopped |
|---|---|---|
| peer became Active | **same second** (08:46:15) | **+1 s** (08:50:05 → 08:50:06) |
| gateway back up | 47 s | 45 s |
| responsibility handed back | +14 s, automatically | already held by the master |

Nothing needed a host command, and neither container ever restarted.

The restart is **scheduled**, not performed inline. Restarting inside a Perspective script
action tears down the web server while it is still writing the response, and the click
then reports a failure for something that worked perfectly. Same trap, same fix, as the
Gateway Network cut in `edge_link`.

`make redundancy-fail` is the host-side version: `docker stop` the active container for
60 seconds, then start it. Use it when `docker stop` is the point — "this is the machine
losing power", rather than "this is the software choosing to restart" — or when a gateway
is too wedged to run a script.

### 3. Automatic versus Manual recovery

The dropdown is worth a minute of anyone's time.

- **Automatic** — the master reclaims responsibility the moment it returns. What most
  sites want, and what makes the demonstration self-healing.
- **Manual** — it comes back as a standby and waits for a human. What sites with
  expensive start-up sequences choose, so a flapping master cannot thrash the plant.

Switch to Manual, stop the master, watch it come back and *stay* standing by, then hand
over deliberately.

---

## The proof: how fast, and was anything lost

The questions asked, 22/09/2026: how fast did the changeover happen, and was any
data lost. Answered with a tag on the pair itself and a recorder outside it, using
only the Redundancy demo's own stacks (core + `postgres` + `ignition-backup` +
`ignition-ha`): no edges, no broker.

**The tag.** `[default]Redundancy/Clock` = `floor(toMillis(now(0)) / 1000)`, evaluated
every 500 ms, history on change, deadband 0, into the `Postgres` SQL historian.
The value *is* the second it was taken in, so both halves compute the same number for
the same second, and "missing" means one thing: a second whose value no historian
holds. The `RedundancyProof` timer (active half) creates or repairs the tag; tag config
reaches the standby by sync. The historian and its database connection come from
bootstrap (`scripts/sf-historian.sh`) and reach the backup by sync, so both halves
write to the one store and either can answer for it (a Warm standby queries it too).

**Why not a Core Historian.** Inductive Automation does not support the Core Historian
on a redundant pair in 8.3: each half keeps its own store and nothing syncs them
([forum](https://forum.inductiveautomation.com/t/core-historian-enterprise-features/113071)).
Using one here anyway (`Redundancy`) showed two further problems, measured
22/09/2026 on 8.3.8. The CPU cost is not about the pair (it reproduced on a clean
standalone gateway); the hidden history was only seen on the pair, and may be a
symptom of running it there:

- **It costs about five cores on this host.** A clean stock 8.3.8 gateway: 8% CPU with
  no historian, ~147% with an empty Core Historian, ~500% with ONE tag at one row a
  second. QuestDB sizes its `shared-write`/`shared-query`/`shared-network` pools to the
  core count (16 each here) and their idle back-off keeps them busy. Capping the pools
  and shortening the back-off in the historian's `server.conf` brought it to ~30%, but
  that file lives in each half's data volume, not in synced config.
- **A restart can hide a tag's whole history.** When the historian's startup cache load
  fails (`Error loading latest cache` ... `TableReferenceOutOfDateException` on the
  `metadata` table), it registers the tag path again under a new node. A query by path
  then returns only the newest node's rows. The definitions table held the same path
  in six rows: one created and retired at 09:41, and five live, each under its own
  node (09:41, 11:00, 11:06, 12:46, 14:09). The rows for
  14:05-14:07 were on disk under node 3 while `queryTagHistory` returned nothing before
  14:09:58. The rows are not lost; they cannot be reached. That is what the "lost"
  seconds in R7 and R9 below were.

Postgres has neither problem, and the page's count is then a count of rows a customer
could query.

**The tag stores history only while every one of the demo's stacks is up**
(`redundancy_demo.ensure_proof`, on the `RedundancyProof` timer's 30 s). Stopped, the
demo's Postgres may be down, and a row a second would pile into the hub's
store-and-forward buffer. `demo_live()` reads `partial` when the Redundancy demo is
stopped but the MQTT demo keeps the shared Postgres up; that counts as stopped too.
Measured 23/09/2026: Redundancy stopped at 15:34:19 with the MQTT demo up,
`historyEnabled` false by 15:34:31 and the last row 15:34:41; the MQTT demo stopped as
well at 15:36:58 (Postgres down), the hub's `tag_history_data.idb` held 0 batches and
did not change through 15:39:26; Redundancy started, `historyEnabled` true by 15:41:20
and rows landing about one a second from 15:41:28.

**The recorder** is `control/redproof.py` in wd-control, which is in neither half, so
it keeps timing while the viewer's session is dropped. Every 250 ms it reads
`/system/gwinfo` on both halves (open, the string HAProxy routes on). When the half
reporting `RedundantNodeActiveStatus=Active` changes, it records a changeover. 60 s
later it asks both halves, through the open WebDev route
`/system/webdev/GatewayAdmin/redundancy/proof?from=&to=` (it answers on a Warm standby
too), which seconds they hold, and counts the window from 60 s before to 60 s after
the new half was first seen in charge: expected 120. It keeps the last ten on its
volume, pending ones included, and `/state` carries them:

```json
"redundancy": {"holder": "master", "halves": {"master": {"answering": true, "active": true}, ...},
  "changeovers": [{"at": 1790038230553, "kind": "planned", "direction": "backup -> master",
    "activeAfterMs": 101, "historyAfterMs": 451, "timedBy": "gateway log",
    "window": {"from": 1790038170, "to": 1790038290}, "expected": 120, "stored": 120,
    "missing": 0, "missingSeconds": [], "missingWhy": "", "perHalf": {"master": 60, "backup": 62},
    "ranges": {"master": [[...]], "backup": [[...]]}, "state": "done"}]}
```

**How time is judged.** Planned: from the moment the old half logged the request
(`Peer activity level request initiated (force-failover)`) to the moment the new half
logged `Activity level=Active`, both read back through the same route. Not from when
anybody said they would ask: `./wd redundancy-failover` spends 15-20 s starting the
toolbox and logging in before it sends the request, and the first cut of this measured
that instead (16-19 s per handover). Unplanned and automatic: from the old half last
seen in charge to the new half first seen in charge, both by the recorder's polls
(±250 ms), because a half that has died logs nothing. `kind` is `planned` when a
request was noted (`POST /redundancy/request`, sent by the console's buttons and by
`make redundancy-failover|fail`) or logged; `unplanned` when the request was a stop or
the old half stopped answering; `automatic` when the old half stood down with nobody
asking, which is the master taking its responsibility back.

**How a missing second is explained**, one clause, from the recorder's own polls:
*neither half was in charge*, or *the master (or backup) was in charge and its
historian holds none of them*. The page never rounds a count to zero.

### Measured, 22/09/2026: Postgres

Only the core and the Redundancy demo running (host load average 5-9).

| # | time | kind | direction | changeover | first new row | expected | stored | missing |
|---|---|---|---|---|---|---|---|---|
| P1 | 14:32:32 | planned | master → backup | 33 ms | +246 ms | 120 | 120 | 0 |
| P2 | 14:34:35 | planned | backup → master | 41 ms | +118 ms | 120 | 120 | 0 |
| P3 | 14:36:34 | planned | master → backup | 42 ms | +88 ms | 120 | 120 | 0 |
| P4 | 14:38:32 | planned | backup → master | 30 ms | +374 ms | 120 | 120 | 0 |
| P5 | 14:40:20 | unplanned, `redundancy-fail` | master → backup | 724 ms | +555 ms | 120 | 120 | 0 |
| P6 | 14:42:52 | automatic | backup → master | overlap | +214 ms | 120 | 120 | 0 |
| P7 | 14:46:04 | unplanned, `redundancy-fail` | master → backup | 403 ms | +417 ms | 120 | 120 | 0 |
| P8 | 14:49:08 | automatic | backup → master | overlap | +23 ms | 120 | 120 | 0 |
| P9 | 14:52:48 | unplanned, `redundancy-fail` | master → backup | 525 ms | +116 ms | 120 | 120 | 0 |
| P10 | 14:55:17 | automatic | backup → master | overlap | +64 ms | 120 | 120 | 0 |

A planned handover is 30-42 ms at the gateways, a hard stop is taken over in 0.4-0.7 s,
and nothing was lost in any of the ten.

### Measured, 22/09/2026: Core Historian (superseded)

Kept for the record: these were counted against the Core Historian, before the two
problems above were found. The host was also overloaded (load 34-43), partly by that
historian. `timedBy` is gateway log for every planned row and the recorder for the rest.

| # | time | kind | direction | changeover | first new row | expected | stored | missing |
|---|---|---|---|---|---|---|---|---|
| R1 | 10:19:33 | planned | master → backup | 107 ms | +956 ms | 120 | 108 | 12 |
| R2 | 10:21:54 | planned | backup → master | 64 ms | +710 ms | 120 | 120 | 0 |
| R3 | 10:24:10 | planned | master → backup | 112 ms | +966 ms | 120 | 120 | 0 |
| R4 | 10:26:22 | planned | backup → master | 130 ms | +826 ms | 120 | 120 | 0 |
| R5 | 10:28:26 | planned | master → backup | 63 ms | +481 ms | 120 | 120 | 0 |
| R6 | 10:30:30 | planned | backup → master | 101 ms | +451 ms | 120 | 120 | 0 |
| R7 | 10:57:58 | unplanned, `redundancy-fail` | master → backup | 2.1 s | +106 ms | 120 | 63 | 57 |
| R8 | 11:02:10 | automatic | backup → master | overlap 7.0 s | +82 ms | 120 | 120 | 0 |
| R9 | 11:03:53 | unplanned, `redundancy-fail` | master → backup | 828 ms | +971 ms | 120 | 61 | 59 |
| R10 | 11:07:24 | automatic | backup → master | overlap 3.2 s | +520 ms | 120 | 120 | 0 |
| R11 | 11:09:16 | unplanned, `redundancy-fail` | master → backup | 1.6 s | +900 ms | 120 | 120 | 0 |

Before those, the same day, two changeovers nobody asked for, both from the host
(load average 34-43 on 16 cores, other projects' gateways included):

| time | what | missing | cause, from the gateways' own logs |
|---|---|---|---|
| 09:58:01 | master took charge at 09:57:57, then stored nothing for 29 s | 29 of 120 | `Pinging the JVM took 29 seconds to respond`, on BOTH halves at 09:58:30 |
| 10:07:02 | master stalled (`Pinging the JVM took 20 seconds`); backup took charge at 10:07:14, split-brain until the master reasserted at 10:08:38 | 10 of 120 | the stall, 10:07:03-10:07:12 |

R1's 12 missing seconds (10:19:40-10:19:51) fall while the backup was in charge and
its historian holds none of them. The backup's log names no cause; the same host's
master logged `Pinging the JVM took 9 seconds to respond` at 10:19:51.

**What the numbers say.**

- A planned handover is **~0.1 s** at the gateways (63-130 ms, six runs) and lost
  nothing in 5 of 6; R1 lost 12 s under the same host load.
- A hard stop is taken over in **0.8-2.1 s** (three runs) — faster than the
  configured 10 s ping budget because a `docker stop` closes the redundancy socket,
  which the backup notices at once.
- R7 and R9's missing seconds were not lost: the master's restart registered the tag
  under a new Core Historian node, which hid everything stored before it (see *Why not
  a Core Historian*). R11's restart did not, and kept all of it.
- The master's automatic take-back overlaps: both halves report Active for 3-7 s, both
  store, nothing is lost.

## The page

`GatewayAdmin/Redundancy`, backed by the `redundancy_demo` script library, which reads the
`RedundancyManager` **in process**. A gateway-scope script is already inside the JVM that
owns the manager, so nothing here needs HTTP, a session cookie, a CSRF token or a
credential — the same reasoning as `gw_trial.reset_local()`. The REST API above is what
`scripts/` uses from *outside* the gateway.

**It is written for the room, not for the engineer**, because the version before it could
not be shown to a customer without talking over it for a minute first. The left rail is
the console's shared one (see DEMO-CONSOLE.md, *One rail on every demo tab*): the demo's
state, *This half / Other half / In step*, the two groups of buttons — **Planned handover**
(hand over or back, force a sync, what happens when the master returns) and **Unplanned
failure** (restart this gateway, which asks twice) — and the gateways it opens. The content
column is one sentence and two blocks:

- **The road**, and it is the whole page: `YOUR BROWSER — ignition.test ▶ <the half
  serving this screen> ⇄ <the other half>`, under a one-word verdict (`PROTECTED`) and a
  pill saying whether there is actually a spare. `PROTECTED` is only claimed when the peer
  is connected *and* in step — a half-synced pair saying it in front of a customer is the
  one outcome worth guarding against.
- **Last changeover** (22/09/2026, replacing the four changeover figures and the
  gateway's log feed) — how long it took, how many of the 120 seconds around it are
  missing and why, a trend of the proof tag per half with the changeover shaded, and the
  three before it. Everything on it comes from wd-control's recorder; see *The proof*.

**A stopped demo reads NOT RUNNING, not RUNNING ON ONE HALF** (21/09/2026). With the
backup never started the pair looks, from inside, exactly like a pair that has lost its
peer — a master alone — and the page said so in its largest type with a `NO STANDBY`
pill beside it: every word true, and read by everybody as a fault. `story()` asks
`demo_control.demo_live("redundancy")` first; stopped, the headline is `NOT RUNNING`, the
pill and the backup's `STANDING BY` leave the layout, and the backup tile says *Not
started*. The EAM tab draws the same pair, and its chip and backup card follow the same
rule (`pair_state()`, `backup_card()`) instead of `PEER LOST` over three red tiles.

**The tiles name the ROLE, not just the gateway, and that is not decoration.** A redundant
pair shares one gateway name: whichever half you are talking to calls itself
`Ignition-Standard`, and only the *peer* carries a `-Master` / `-Backup` suffix. So the
serving tile reads `Ignition-Standard` before a handover and `Ignition-Standard` after it,
and a customer watching the thing move has no evidence on screen that it moved.
`SERVING THIS SCREEN · MASTER HALF` → `· BACKUP HALF` is the evidence. Verified through the
front door in both directions, 31/08/2026.

The feed's heading names the half whose log it is for the same reason: the events come from
this gateway and nowhere else, the lines cannot say so themselves, and "this gateway" means
a *different machine* either side of a handover.

### Five traps that cost a deploy each

- **A raised exception in a Perspective script transform renders the component as a red
  ERROR box and logs NOTHING.** The failure is reported to the browser and nowhere a
  script can see it. `redundancy_demo.summary()` therefore catches for itself and returns
  a legible message, so the reason lands in the gateway log where the next person looks.
- **The system name is not on `IgnitionGateway`.** It is
  `getSystemPropertiesManager().getSystemName()`. Calling the method that does not exist
  is what produced the red boxes above.
- **`PeerStatus.getPeerId()` returns the peer's ADDRESS and
  `getPeerAddress().toDescriptiveString()` returns its NAME.** They are the other way
  round from what the names suggest, and the page reads *wrong* rather than breaking — the
  backup's card carried an IP address as its title for one deploy.
- **`MetricValue.getName()` is a `LocalizedString`, not a `String`,** and its `toString()`
  renders the translated label. Keying on `str(...)` never matches `RedundancyManager`'s
  `METRIC_*` constants, which are the i18n **keys**, so every lookup silently returned its
  default and the sync table showed raw provider ids and plausible-looking zeroes.
  `getName().getKey()` is the one that matches.
- **The cards are ordered Master-then-Backup, so `cards[0]` is NOT "this gateway".** The
  fixed order is deliberate — it stops the two cards swapping places mid-failover, which
  would make a working handover look like the page had lost track of both halves. The cost
  is that the index coincides with the local half on the master and **inverts on the
  backup**: `story()` read `cards[0]` as "me" and so named the *warm master* as the half
  serving the screen — wrong only after a handover, which is the one moment anyone is
  looking, and invisible from the master where all the earlier verification was done. Every
  card now carries `isLocal` and `isActive`; read the flags, never the position.

### Verifying the page after a change

`make shot PROJECT=GatewayAdmin PAGE= TAB=Redundancy` photographs it — the console is one
page with five tabs, so `TAB=` presses the button first. To check the half of this that
only appears during a failover, photograph **the front door** rather than a gateway:

```bash
./wd shot URL=https://ignition.test PROJECT=GatewayAdmin PAGE= TAB=Redundancy \
      OUT=.shots/frontdoor.png
./wd redundancy-failover     # hand over, wait ~15s, shoot again
./wd redundancy-failover     # and hand back
```

That is the only way to see what the customer sees, and it is what caught both the
inverted names and the proxy serving an edge. **Deploy before handing over**, not after: a
Warm standby runs no project scripts, so `make scan` cannot run on a master that has handed
over and the deploy fails at the scan.

One more that is a shape rather than a bug: **in-process `getMetrics()` and the REST
`/redundancy/providers` route do not return the same set.** The route reports every
registered provider; `getMetrics()` reports only those that have published a metric.
Filtering the in-process list against names copied from the REST output emptied the table
completely, with no error anywhere — the rows simply were not there. The page now shows
everything the manager reports and merely *orders* the interesting ones first.
