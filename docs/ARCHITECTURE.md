# Architecture

Eleven independent Compose projects, one folder each, under `stacks/`. All join
a shared external network called `backbone` so they can reach each other by
container name. **The authoritative list is the folders themselves** -- every
stack carries a `stack.meta` manifest and everything that needs a list derives
from those, so a table in this document can only ever be illustrative.

> This document explains *why* each decision was made, corrected against a
> live 8.3.8 gateway where a correction was needed -- each is marked **[gateway
> behaviour]**. Stacks live in `stacks/<name>/`; most operations go through
> `make` (see the root README).

`make status` prints the live table -- one row per manifest, with the ports
this machine actually publishes. The default layout:

| Stack            | URL / port                     | Notes                            |
|------------------|--------------------------------|----------------------------------|
| NPM              | http://localhost:29081         | Front door stays on :80 / :443   |
| Ignition (hub)   | https://console.test           | **Standard**, also :29088        |
| Ignition backup  | http://localhost:29388          | **Standard**, redundant pair     |
| Ignition HA      | http://localhost:29404          | Pair front door -- ignition.test |
| Ignition edge1   | http://localhost:29188          | **Edge** edition                 |
| Ignition edge2   | http://localhost:29288          | **Edge** edition                 |
| Ignition edge3   | http://localhost:29588          | **Edge**, ROLE=edge-isolated      |
| Ignition edge4   | http://localhost:29688          | **Edge**, ROLE=edge-isolated      |
| Postgres         | localhost:29432 (loopback only) | `ignition` DB ready to use       |
| MQTT broker      | https://console.test/app/mqtt-distributor | MQTT Distributor on the hub pair, TLS 8883 on `wd-mqtt` only |

## Adding a stack (or a whole new demo)

A stack is a folder under `stacks/` with three files, and NOTHING else needs
editing -- start order, `make status`, the proxy host, the TLS SAN, the hosts
file, seeding and bootstrap all derive from the manifest:

```text
stacks/<name>/
  compose.yaml     container_name MUST equal the folder name
  .env.example     every ${VAR} the compose file interpolates, secrets as __GEN__
  stack.meta       the manifest -- grammar in docs/PORTABLE.md
```

1. Write the three files. Pick an unused `ORDER` (gaps of 10 leave room) and,
   if browsers should reach it by name, a `TEST_HOST` + `TEST_FORWARD_PORT`.
2. `make validate` -- the manifest grammar, uniqueness and every claim the
   manifest makes about its own compose file are checked here, so a typo dies
   in CI rather than as a name that silently never resolves.
3. `make env` (writes the new `.env`), then `make certs` if a `TEST_HOST` was
   added (the SAN list follows the manifests), then `make up STACK=<name>`.
4. If it has a `TEST_HOST`: re-run `bash stacks/npm/create-proxy-hosts.sh`
   (idempotent, reconciles) and `scripts/hosts-setup.sh` (or the .ps1) once
   per machine.

**A new gateway** additionally declares `KIND=gateway`, a `ROLE`, a unique
`STANZA` (credentials appear in `.gateways.env` on the next `make env`), and
-- for an edge -- `EAM_AGENT` (must equal the `-n` in its compose command),
`SITE_PROJECT` and `SF_TRANSPORT`. bootstrap then picks it up with no edit:
module install and commissioning iterate the gateways, GAN dial-in and EAM
registration iterate `ROLE=edge`, MQTT setup targets gateways declaring an
`MQTT_MODULE`, and the first push reads `SITE_PROJECT`. The one hand-written
place left is the Jython topology in `gateway_admin`/`sf_demo` (a gateway
script cannot read this repo) -- `make validate` fails until it agrees with
the manifests, and names exactly what disagrees.

## Retiring a stack

Deleting the folder and committing is **not** the whole job, and the part that
is left over surfaces on somebody else's machine rather than yours.

1. `make down STACK=<name>` first, then remove the folder and commit.
2. `make prune-names` on **every** machine — `hosts-setup` and
   `create-proxy-hosts` only ever add, so the `.test` name keeps resolving to
   the proxy and NPM keeps answering on it, pointing at a container that no
   longer exists. Both report a name no `stack.meta` declares on every run and
   remove it only on request.
3. Remove the stack's Docker volumes on every machine. They are named after the
   compose project, so nothing left in the repo refers to them and nothing will
   ever flag them.
4. **On every other clone, delete the leftover directory.** This is the one that
   bites, because it looks like the repo is broken rather than tidy.

That last one: `git pull` removes the tracked files — `compose.yaml`,
`stack.meta`, `.env.example` — and then *cannot remove the directory*, because
the gitignored `.env` is still sitting in it. So the retirement passes CI on the
machine that did it, and every other clone starts failing `make validate` on the
pull.

`validate` names it properly now — a stack directory holding nothing but `.env*`
is reported as *retired elsewhere and pulled in*, with the path to delete. It is
still an **error and not an automatic repair**, because that leftover is this
machine's credentials for the retired stack and deleting somebody's secrets is
not validate's job. Check the file before removing it; every shared value
(`POSTGRES_PASSWORD` and friends) also lives in the root `.secrets.env` keyed by
name, so only a key unique to the retired stack is actually lost.

## The shared network

Each Compose project normally gets its own isolated network, so containers in
different folders cannot see each other. `backbone` is created once, outside of
any project, and every stack joins it as `external: true`:

    docker network create backbone      # already done; only needed once

If you ever recreate it, every stack must be restarted to rejoin.

Keeping the stacks in separate folders is deliberate -- each one starts, stops,
upgrades and gets destroyed on its own. `backbone` is the only thing they share,
and it is what lets them talk. The trade-off is that `depends_on` cannot cross a
project boundary, so start order is on you (see below).

## Host ports vs container ports

This trips people up constantly, and it matters for every Nginx Proxy Manager
entry you create. The published port is **not** the port the container listens
on:

| Container         | Listens on (internal)         | Published as (host)           |
|-------------------|-------------------------------|-------------------------------|
| `ignition`        | 8088 / 8043 / 8060 / 62541    | 29088 / 29043 / -- / 29041    |
| `ignition-backup` | 8088 / 8043 / 8060 / 62541    | 29388 / 29343 / -- / 29341    |
| `ignition-edge1`  | 8088 / 8043 / 8060 / 62541    | 29188 / 29143 / -- / 29141    |
| `ignition-edge2`  | 8088 / 8043 / 8060 / 62541    | 29288 / 29243 / -- / 29241    |
| `ignition-edge3`  | 8088 / 8043 / 8060 / 62541    | 29588 / 29543 / -- / 29541    |
| `ignition-edge4`  | 8088 / 8043 / 8060 / 62541    | 29688 / 29643 / -- / 29641    |
| `postgres`        | 5432                          | 29432 (loopback only)         |
| `npm`             | 80 / 443 / 81                 | **80 / 443** / 29081          |

**Every published port sits in 29000–29999 except the proxy's 80 and 443**,
which stay put because they are what `https://ignition.test` means — the
redundant pair's front door. See docs/PORTABLE.md for why that block was chosen
and what moving the proxy costs.

All six gateways listen on **8088 internally**. Only the host mapping differs,
which is what stops them colliding. Container-to-container traffic over
`backbone` always uses the internal port and ignores the published one.

> **[gateway behaviour]** Every published port is
> `${VAR:-default}` in the compose file, with the default matching the table
> above. A machine where something else already owns a port overrides it in that
> stack's gitignored `.env` -- no change to a tracked file. Compose *project names* are overridable the same way, via
> `COMPOSE_PROJECT_NAME`, because Compose matches existing containers by
> project+service label -- a name collision makes `up -d` silently adopt another
> project's container.

## Resource footprint

Four JVMs is the bulk of it. Measured on the personal VM with every stack
up and idle:

| | heap floor | heap peak | `-Xmx` | container RSS |
|---|---|---|---|---|
| hub | 301 MB | 571 MB | 1024 MB | 2057 MB |
| backup | 305 MB | 595 MB | 1024 MB | 1914 MB |
| edge1 | 256 MB | 440 MB | 512 MB | 1563 MB |
| edge2 | 251 MB | 433 MB | 512 MB | 1525 MB |

Everything else together was under 550 MB (EMQX 330 -- since retired, NPM
126, Postgres 55, HAProxy 14).

> **Heap USAGE and heap RESIDENCY are not the same number.** The paragraph
> below reads usage; *The committed heap*, right after it, reads residency --
> and the two disagree about where the waste sits. Read both before concluding
> anything from either alone.

**The heap is not where the waste is, and that is the whole point of the table.**
An edge with a 512 MB heap and the hub with a 1024 MB heap both carry about
**1 GB of non-heap RSS** — identical, so it does not scale with `-Xmx` and
lowering `-Xmx` cannot reclaim it. Nor is there much slack in the heap itself:
the edges peak at 440 MB of 512, which is 86%. Lower them and you trade a little
RSS for GC pressure during exactly the moments a demo is busy.

### The committed heap

**A JVM's RSS carries the heap it has COMMITTED, not the heap it is using**, and
nothing above measures the first. Read straight off the hub's `smaps` after three
days up, `-Xms256m -Xmx1024m`:

```text
c0000000-fff00000 rw-p    1022 MB, fully resident
VmRSS 1849 MB   RssAnon 1806 MB   VmSwap 0
```

That single mapping is the Java heap: it had grown to its `-Xmx` ceiling and
**every byte of it was resident**, on a gateway whose measured heap *peak* is
571 MB and which was serving nobody. 58% of the hub's anonymous RSS was heap the
JVM had stopped using and never handed back. The table's own "heap peak 571"
and this "committed 1022" are both correct and describe different things — which
is exactly how the earlier conclusion went wrong.

G1 *can* return it (JEP 346, JDK 12+), but only at the end of a concurrent
cycle, and an idle gateway never starts one. So it is asked to:

```text
-XX:G1PeriodicGCInterval=300000        # a cycle every 5 min even when idle
-XX:G1PeriodicGCSystemLoadThreshold=0  # ... regardless of system load
```

and the hub and backup, which had real slack (571 and 595 of 1024), were capped
with the image's own `-m 768`. The edges keep 512: 440 of 512 is 86%, and there
the original reasoning holds exactly as written.

That non-heap gigabyte is metaspace, code cache, ~220 thread stacks and glibc's
per-thread malloc arenas — and the arenas are the tractable part. glibc allows up
to `8 × cores` of them (128 on a 16-core host), each growing independently, and
Ignition's thread count spreads allocations across all of them.

**A/B, edge2 changed and edge1 left as the control** (both idle, both healthy,
same image, same projects):

```
MALLOC_ARENA_MAX=2          # env -- glibc, not the JVM
-XX:ParallelGCThreads=2     # G1 sizes these from CORE COUNT, not heap size;
-XX:ConcGCThreads=1         # 13 parallel GC threads for a 512 MB heap is absurd
```

    edge1 (control)  1.56 GB
    edge2 (tuned)    1.05 GB      -33%, stable across 45 minutes

Store-and-forward survived the recreate — Sparkplug reconnected and the
transmitter still reported `historyFlushType=ASYNC`, which is the setting that
silently reverts to `NONE` if anything about the client is rebuilt wrong.

**Then measured through a real demonstration**, which is what the idle A/B could
not answer: two GC threads is a genuine reduction in collector throughput, and a
pause shows up as dropped or late samples rather than as memory. Both edges were
cut for 60s from the Store & Forward page and their recovery compared over the
same window — same broker, same 1/second sample rate:

| | samples recovered | largest gap | normal interval |
|---|---|---|---|
| edge1 (control) | 128 | 3.0 s | 1.09 s |
| **edge2 (tuned)** | **138** | **2.0 s** | **1.01 s** |

The tuned gateway recovered *more* samples with a *smaller* worst gap, and
neither log carried an OOM, a GC-overhead warning or a multi-second pause. RSS
held at 1.09 GB against the control's 1.49 GB through the load. Nothing here
suggests the reduced collector is costing anything at this scale.

**Applied to all four gateways.** Measured shortly after a recreate, with the
stack healthy and both roads flowing:

| | before | after |
|---|---|---|
| hub | 2057 MB | 1322 MB |
| backup | 1914 MB | 1250 MB |
| edge1 | 1563 MB | 1015 MB |
| edge2 | 1525 MB | 1097 MB |
| **all four** | **7059 MB** | **4684 MB** |

About **2.4 GB back for six lines of configuration**, and nothing in the demos
behaves differently. Treat the "after" column as a floor rather than a
steady state: it was taken minutes after a restart, so expect it to settle
somewhat higher — edge2, which had been up for hours, sat at 1097 MB, and that
is the honest number to plan against.

The pair must be **recreated, not restarted** (`./wd up`), because the JVM
arguments are compose `command:` args and the env var is an env var; a `docker
restart` re-runs neither.

**Do not compare these numbers across machines.** The same tuning is verifiably
applied on the Windows host — `MALLOC_ARENA_MAX=2` in PID 1's environ and both
`-XX:*GCThreads` flags on the JVM command line — yet `docker stats` there reports
1.1–1.9 GB per gateway against this VM's 1.0–1.3. Docker Desktop runs containers
inside WSL2 and accounts memory differently, so the absolute figures are not
comparable and the difference is not evidence of anything being wrong. There is
no pre-tuning baseline recorded for that machine, so **no saving is claimed for
it** — measure a machine against itself or not at all.

Anyone who needs the footprint smaller than this should drop gateways rather than
shrink them: the redundancy pair and the second edge exist to be *shown*, and a
styling-only demo needs the hub and one edge.

### Unused modules

A stock 8.3 gateway registers **~32 modules and starts them all**, whether or not
anything uses them. This stack uses eight. The rest are drivers for hardware that
does not exist, and they are not free:

```text
hub, idle, one Perspective project, nobody connected:
  343 threads      16.6% of one core
  of which OPC-UA -- with zero devices configured -- was 29 threads:
    16 opc-ua-scheduler   8 milo-netty-event
     3 milo-shared-sched  2 milo-trust-list
```

On a 16-core workstation that is invisible. On the **8 GB / 2-core** machines
this stack is meant to travel to it is ~6.5% of the whole machine per gateway.

`scripts/ign-modules-trim.sh` disables the other 27 by flipping `"onStartup"` to
`"disabled"` in `data/modules.json` — the gateway's own switch, so it is
reversible (`--restore`), needs no re-download, and leaves the `.modl` exactly
where `get-modules.sh` and `ign-modules.sh` put it. Deleting the files would
fight both and could not be undone without a network.

The keep-list is in the script with a reason per line: Perspective, EAM,
Historian, Historian SQL, PostgreSQL JDBC, MQTT Engine, MQTT Transmission,
Embr Charts and the Architecture Builder.

**It is gateway state, so `bootstrap.sh` calls it** (step 6b) — otherwise a
rebuild restores all 32, which is exactly how the Gateway Network remote tag
provider went missing on two machines for days. It runs before step 8's restart
for the same reason the themes do: `modules.json` is read at startup, so the
restart that already happens applies the trim for free.

Two things worth knowing:

- **The redundant backup needs no trim of its own.** Asked to trim it, the script
  reported `NOCHANGE already trim` — redundancy had already synchronised the
  master's module set to it.
- **A trim needs a RESTART, not a recreate** (unlike a JVM-arg change, which is a
  compose `command:` arg and so needs `./wd up`).

## Ignition editions

Edition is chosen by `IGNITION_EDITION` at commissioning time and is baked into
the data volume from then on:

    ignition/         IGNITION_EDITION=standard    the hub
    ignition-edge1/   IGNITION_EDITION=edge        spoke
    ignition-edge2/   IGNITION_EDITION=edge        spoke

Two things to know about the commissioning variables (`ACCEPT_IGNITION_EULA`,
`IGNITION_EDITION`, `GATEWAY_ADMIN_USERNAME`, `GATEWAY_ADMIN_PASSWORD`):

1. They are read by the **gateway**, not by `docker-entrypoint.sh`. Grepping the
   entrypoint for them finds nothing, which is why they are easy to write off as
   unsupported. They work on 8.3.8 -- a fresh volume commissions unattended in
   about ten seconds with no browser wizard.
2. They apply **only while `data/commissioning.json` is absent**, i.e. on an
   empty volume. Editing them on a commissioned gateway does nothing.

So changing a gateway's edition after the fact is not an edit, it is a rebuild:

    cd ~/docker/ignition-edge1
    docker compose down -v        # DESTROYS that gateway's data
    docker compose up -d

You can verify which edition a gateway actually commissioned as:

    docker logs ignition-edge1 2>&1 | grep -c "Starting up module"

Edge loads about 20 modules, Standard about 94. Edge also creates a project
called `Edge` and a tag provider called `edge`; Standard creates `GatewayPulse`.

> **[gateway behaviour]** On 8.3.8 the counts are **22 for Edge and 33 for
> Standard** -- 8.3 consolidated the module set, so an older release's figure
> would read higher. The relative difference between editions is the signal;
> the absolute count is not.

## Hub and spoke Gateway Network

`ignition` is the hub. `ignition-edge1` and `ignition-edge2` are the spokes and
dial **out** to it -- the hub never initiates. The GAN port (8060) is
deliberately not published to the host, because the spokes reach it over
`backbone` as `ignition:8060` and nothing outside Docker needs it.

On each Edge gateway (http://localhost:29188 and http://localhost:29288):

**Config -> Networking -> Gateway Network -> Outgoing Connections -> Create**

| Field     | Value      |
|-----------|------------|
| Host      | `ignition` |
| Port      | `8060`     |
| Use SSL   | on         |

Use the hostname **`ignition`**, not `localhost` and not `localhost:29088`.
Inside a container `localhost` is that container itself, and 8060 is the GAN
port, not the web port.

Then approve the incoming connections on the hub at
**Config -> Networking -> Gateway Network -> Incoming Connections**, where they
appear as `edge1` and `edge2`. Those names come from the `-n edge1` / `-n edge2`
arguments in each spoke's `command:` block -- without them the gateways would
identify themselves by container ID.

The hub's own name was set during its original browser commissioning and is left
alone on purpose: a gateway's system name is its identity on the Gateway
Network, so renaming an established hub would orphan the spokes' connections.

### Start order

`depends_on` cannot cross Compose projects, so bring the hub up before the
spokes. If a spoke starts first it just retries until the hub answers -- the
connection is not lost, it only looks broken for a minute.

    make up            # every stack, in dependency order

or by hand, hub before spokes:

    cd stacks/postgres        && docker compose up -d
    cd stacks/ignition        && docker compose up -d
    cd stacks/ignition-edge1  && docker compose up -d
    cd stacks/ignition-edge2  && docker compose up -d

## Nginx Proxy Manager

NPM is the single front door. It sits on `backbone`, so it reaches every other
container by container name on that container's **internal** port.

Admin UI: http://localhost:29081, credentials in `npm/.env`. Those are consumed on
first run only, while the database has no users -- change the password in the UI
afterwards, not in the file.

### Proxy hosts

Created (and reconciled -- a changed target is corrected, not skipped) by
`stacks/npm/create-proxy-hosts.sh`, derived from every manifest declaring a
`TEST_HOST`. The forward host is always the container name. As of the manifests
today:

| Domain name     | Forward hostname | Forward port |
|-----------------|------------------|--------------|
| `console.test`  | `ignition`       | 8088 -- the hub, always up |
| `ignition.test` | `ignition-ha`    | 8088 -- the redundant PAIR, via HAProxy; the hub while HAProxy is down (`TEST_FALLBACK`) |
| `edge1.test`    | `ignition-edge1` | 8088         |
| `edge2.test`    | `ignition-edge2` | 8088         |

**A stopped demo's name explains itself.** Since the demo console, every one of
these names except `console.test` belongs to an optional demo — so on a
core-only machine they are all dead, correctly, and a browser reports that as
"502 Bad Gateway": no cause, no action. Each proxy host now carries an
`error_page` pointing at `/data/wd/__wd-down.html`, generated from `demos.json`
by `stacks/npm/gen-down-page.py`, which names the stack, the demonstration it
belongs to, and the command that starts it. The status stays 502 — only what a
human sees changes.

`console.test` is the hub, and it is here because the console is the page you
start demos *from*: it was the one thing always running and the only one
reachable solely by a raw host port. It is deliberately not called `hub.test` —
see `stacks/ignition/stack.meta` for why a name that reads like a shortcut to
the pair is a trap during a failover demonstration.

They were created through NPM's REST API rather than by hand, so they can be
rebuilt after a `down -v` without clicking through the UI seven times:

    cd stacks/npm && ./create-proxy-hosts.sh

The script reads the credentials out of `npm/.env` and skips any domain that
already exists, so it is safe to re-run. If you change the admin password in the
UI, update `npm/.env` or the script will no longer authenticate.

Scheme is `http` for all of them: the leg from NPM to the container is inside
the Docker network, and terminating TLS twice buys nothing here.

### Logins behind the proxy

NPM cannot log you in to anything: it can only inject Basic-auth headers, and
every app here uses a form-plus-cookie flow. So the login story is per service,
tuned for a demonstration stack:

| Service | Login |
|---|---|
| Perspective clients (the demo itself) | none — sessions are unauthenticated |
| Ignition gateway config UIs | required; let the browser save it |
| MQTT broker (Distributor) | a page in the hub's gateway UI -- opens signed in through `/_wd/login` |
| NPM admin | required — it controls the proxy, keep it |

### Gateway public addresses

The gateways' Perspective Session Launcher throws
`Routes$MissingGatewayAddressException` until each gateway has an explicit
public address. Each gateway points at
its own `.test` name, so anything launched from a gateway page lands on the
proxied HTTPS address:

| Gateway | Public address |
|---|---|
| Ignition-Standard | `ignition.test`, HTTP 80 / HTTPS 443 |
| Ignition-Edge1 | `edge1.test`, HTTP 80 / HTTPS 443 |
| Ignition-Edge2 | `edge2.test`, HTTP 80 / HTTPS 443 |

This is **gateway state, not repo state** (rule 4) — set per machine, and the
work machine needs its own values. Scriptable via `GET`/`POST
/data/config/web-server` (`autoDetectPublicAddress: false`, `publicAddress`,
`publicHttpPort`, `publicHttpsPort`); the POST can drop the connection as the
change applies while still succeeding, so verify by re-reading.

**Websockets Support is on for every host.** Perspective runs its entire session
over a websocket -- without it the Ignition login page renders and then the
session hangs with no obvious error. It is a no-op for the hosts that do not use
websockets, so it is enabled uniformly rather than only on the gateways.

`.test` is reserved by RFC 6761 and will never resolve on the public internet,
which makes it safe for this. Avoid `.local` on macOS -- that is Bonjour's, and
`/etc/hosts` entries under it behave unpredictably.

### Making the names resolve, and trusting the CA

Two steps need root, so they live in one script — one per platform, because
neither the hosts file nor the trust store is reachable the same way:

    cd stacks/npm && sudo ./finish-setup.sh                              # macOS, Linux

    cd stacks\npm                                                       # Windows,
    powershell -ExecutionPolicy Bypass -File .\finish-setup.ps1          # as Administrator

**Neither runs in the toolbox, and neither can.** The toolbox is a container: it
has its own hosts file and its own trust store, and writing either would change
nothing on the machine whose browser you are using. This is the one part of the
stack that is genuinely a host step.

Until the PowerShell half existed the `.test` names simply did not work on
Windows — the browser answered `DNS_PROBE_FINISHED_NXDOMAIN`, and the only way
in was `http://localhost:<port>`, which is not what this document tells you to
use. That is worth knowing as a symptom: a name that does not resolve on one
machine and does on another is this script having been run on one of them.

It adds the `.test` names to `/etc/hosts` pointing at `127.0.0.1` (NPM routes by
hostname but does not do DNS), and trusts the local CA in the System keychain so
HTTPS shows a padlock rather than a warning. Both halves are idempotent, it
backs up `/etc/hosts` to `/etc/hosts.bak` first, and it prints the undo commands
when it finishes.

Until you run it, the names will not resolve at all and the browser will say the
site cannot be found. The proxy itself is fine -- you can prove that without
touching DNS:

    curl -sI --cacert npm/certs/rootCA.pem \
      --resolve ignition.test:443:127.0.0.1 https://ignition.test/

### HTTPS

All seven hosts are on HTTPS with `ssl_forced`, so plain http 301-redirects to
https. The certificate is a local one covering `*.test` plus each explicit name,
generated by:

    make certs        # the local CA and the *.test leaf

**Let's Encrypt cannot be used for these.** `.test` is reserved by RFC 6761 and
will never resolve on the public internet, so no public CA will ever issue for
it. That is not an NPM limitation and no amount of DNS configuration works
around it -- a local CA is the only way to get real TLS on these names.

The script creates `npm/certs/rootCA.pem` once and reuses it on subsequent runs,
so re-issuing the leaf certificate does not mean re-trusting the CA. The leaf is
valid 825 days, which is the maximum macOS accepts. The wildcard means a seventh
proxy host later needs no new certificate.

`npm/certs/` holds private keys. It is `chmod 700` and excluded by `.gitignore`.

HSTS is deliberately **off**. Browsers cache it hard, and turning it on would
make it painful to go back to plain http on these names later.

If you would rather have certificates that are trusted without a custom CA, the
alternative is a real domain you own with DNS-01 validation -- NPM supports that
under Hosts -> SSL Certificates -> Add. That means moving off `.test` entirely.

### Ignition behind the proxy

Each gateway's `command:` block passes `gateway.useProxyForwardedHeader=true`.
That makes the gateway honour the `X-Forwarded-Proto` / `X-Forwarded-Host`
headers NPM sends, so redirects and Perspective session URLs come back as
`https://ignition.test` rather than the unreachable `http://ignition:8088`.
Without it, logging in through the proxy bounces you somewhere that does not
exist.

Unlike the commissioning variables, arguments after `--` of the form
`gateway.<key>=<value>` are rewritten into `data/gateway.xml` on **every** start,
so they stay authoritative and can be changed with a plain `up -d`.

That authority cuts both ways, and it bit once. The hub's args carried
`gateway.publicAddress.autoDetect=true` from before the public addresses were
set. Because the address and ports are *not* args they survived the restart of
a restart untouched, so the page looked configured — but auto-detect had
quietly flipped back on and the Perspective launch page threw
`MissingGatewayAddressException` again. Dropping the arg fixes it for good: the
public address is gateway state (rule 4), so nothing in compose should
overwrite it.

**Anything you list after `--` wins over the UI on every start.** Put a setting
there only when compose genuinely is the source of truth for it; otherwise the
gateway silently reverts a change back to the compose arg on the next restart.

## Connecting the Ignition gateway to Postgres

In the gateway: **Config -> Databases -> Connections -> Create new Connection**,
choose **PostgreSQL**, then:

| Field          | Value                                          |
|----------------|------------------------------------------------|
| Connect URL    | `jdbc:postgresql://postgres:5432/ignition`     |
| Username       | `ignition`                                     |
| Password       | see `IGNITION_DB_PASSWORD` in `postgres/.env`  |

Use the hostname **`postgres`**, not `localhost`. Container-to-container traffic
goes over `backbone` directly and does not use the published 127.0.0.1:5432 port.

The `ignition` database and role are created by `postgres/initdb/01-ignition.sh`,
which runs **only** when the data volume is empty. Editing it later does nothing
unless you delete the volume.

Edge gateways use their own embedded historian and do not need this connection.

## The MQTT broker

The broker is **Cirrus Link MQTT Distributor on the hub's redundant pair**, not
a container of its own: `ssl://mqtt-master:8883` (the master half) with
`ssl://mqtt-backup:8883` as every client's failover. It listens only on the
active half, TLS only, on `wd-mqtt`, a network that carries MQTT and nothing
else -- which is also what makes a single edge's link cuttable
(`docker network disconnect wd-mqtt <edge>`, done by wd-control). Its
certificate is each half's own web certificate, from the repo CA.
`scripts/distributor-setup.sh` builds it and `scripts/ign-mqtt.sh setup` points
every MQTT module at it; the login is user `ignition`, `MQTT_PASSWORD` in
`.secrets.env`. Settings: `https://console.test/app/mqtt-distributor`. The
whole story, measured, is [MQTT-DISTRIBUTOR.md](MQTT-DISTRIBUTOR.md); the TLS
side is [MQTTS.md](MQTTS.md).

No separate broker container runs. A machine that ran the retired EMQX broker
may still hold its volumes (`emqx_emqx_data`, `emqx_emqx_log`, `wd-emqx-certs`);
nothing uses them and they are safe to delete.

## Everyday commands

Run these from inside a stack's folder:

    docker compose up -d       # start
    docker compose down        # stop and remove containers (data survives)
    docker compose logs -f     # follow logs
    docker compose pull        # fetch newer images
    docker compose down -v     # DESTROYS that stack's data volumes

## macOS notes

The work machine is macOS; the personal VM is Linux. Everything below is about
the work machine. On Linux the named-volume reasoning does not apply (there is
no VirtioFS boundary) but named volumes are kept anyway so both machines behave
identically.

- Data lives in **named volumes**, not bind mounts. On macOS, bind mounts cross
  the VirtioFS boundary into the Docker VM and are far slower -- badly so for
  Postgres. Named volumes stay inside the VM. NPM departs from the upstream
  docs for this reason: its `/data` and `/etc/letsencrypt` are named volumes
  rather than `./data` and `./letsencrypt`.
- Docker Desktop must be running before any `docker` command works. To start
  the stacks automatically, enable *Settings -> General -> Start Docker Desktop
  when you sign in*; `restart: unless-stopped` then brings the containers back.
- Every image here is native `arm64`, so nothing runs under emulation.
- Three Ignition gateways is a real memory commitment. The Edge spokes are
  capped at a 512 MB heap via `-m 512`; the hub is left at the image default.
  If Docker Desktop starts swapping, raise its memory allocation in
  *Settings -> Resources* before trimming the gateways.

## VS Code

`.vscode/settings.json` sets `python.envFile` to empty. The `.env` files here
are Compose variable files, not Python ones, and without that setting the Python
extension warns that "an environment file is configured but terminal environment
injection is disabled". Do not resolve that warning by enabling
`python.terminal.useEnvFile` -- it would inject the Postgres and gateway
passwords into every integrated terminal.

## Backups

Volumes are not in this folder, so copying `~/docker` does not back up data.
To dump the Postgres databases:

    docker exec postgres pg_dumpall -U postgres > ~/backup-$(date +%F).sql

Ignition gateways back up separately -- take a `.gwbk` from each gateway's
**Config -> System -> Backup/Restore** page, or accept that the volumes are the
only copy.

## Secrets

`.env` files hold generated passwords and are `chmod 600`. They are excluded by
`.gitignore`.

> **[gateway behaviour]** This folder *is* in git. The real `.env` files
> and both `certs/` directories stay out; what is committed is a `.env.example`
> per stack carrying the variable names and comments, which
> `scripts/make-env.sh` renders into a real `.env` with generated passwords.
> `make check-secrets` enforces it and runs in CI.

All timezones are `Australia/Adelaide`. Note that Postgres is the one stack that
does not take a `TZ` variable, so its logs are in UTC.

## Small machines: the two timeouts that decide whether it starts

Fitting in 8 GB is one question; *starting* on two cores is a different one, and the
second is the one that bites. An Ignition gateway's start is dominated by classloading
and the project scan, both CPU-bound, so on two cores it takes roughly twice as long as
on the sixteen-core host these numbers were first chosen on.

That matters more than a slow start, because both timeouts feed the same loop:

* **`READY_TIMEOUT` in `control/service.py`** is how long the console waits for a stack
  to become healthy *before starting the next one*. Giving up early does not merely
  report a failure — the loop moves on and starts the next stack, so a timeout that
  fired because the box was slow is precisely what then puts two JVMs on it at once.
  It now scales: `min(900, max(300, 300 * 4 / cores))` — 300s at four cores or more
  (the measured value), 600s at two, 900s at one. `WD_READY_TIMEOUT` overrides it.
* **The gateway healthchecks** were `start_period: 120s, retries: 10`. A gateway that
  needs three minutes on a small machine is marked unhealthy while it is working
  perfectly, and the console then waits on health it will never see. Now `180s` and
  `20`. The cost is that a genuinely dead gateway takes longer to be called dead,
  which is the right way round for a stack whose containers are `restart: "no"`.

Neither is a guess about the hardware: both are about not punishing a machine for being
slow by giving it more to do.

### Measured on two cores

Emulated faithfully rather than estimated: every container of the stack pinned to the
same two CPUs with `cpuset`, then **all four gateways restarted simultaneously** — the
worst case, and worse than a real bootstrap, which starts them in order.

| gateway | RUNNING | docker-healthy | failing checks |
|---|---|---|---|
| `ignition` | 140s | 140s | 0 |
| `ignition-backup` | 141s | 141s | 0 |
| `ignition-edge1` | 118s | 118s | 0 |
| `ignition-edge2` | 130s | 141s | 0 |

Load average reached **27**, and the whole stack sat at **3.8 GB** resident (hub 931 MB,
backup 797 MB, edges 689/734 MB, EMQX -- since retired -- 376 MB, the rest under 250 MB each) — so 8 GB has
room for the stack and the machine around it.

**Read this honestly: it validates that the stack runs on two cores, and it does NOT show
the old healthcheck window failing.** The old budget was 120s start_period plus ten
15-second retries, so a gateway ready at 141s would have gone healthy without ever being
marked unhealthy. What the new 180s/20 buys is *headroom* — this run had warm page cache
and containers that already existed, and a genuinely small machine on first boot, slower
storage, or weaker cores can be several times slower. The change is insurance against
that, not the repair of an observed failure, and it should not be described as one.
