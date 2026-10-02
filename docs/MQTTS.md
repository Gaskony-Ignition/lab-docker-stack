# MQTTS — edge gateways to the hub's broker over TLS

Every MQTT client in the stack talks TLS to one broker: **MQTT Distributor on
the hub's redundant pair**. How it was chosen and measured is
[MQTT-DISTRIBUTOR.md](MQTT-DISTRIBUTOR.md); this page is the TLS and
configuration side.

| Piece | Where |
|-------|-------|
| broker | MQTT Distributor 5.0.4 on `ignition` and `ignition-backup`; listens only on the active half |
| address | `ssl://mqtt-master:8883`, then `ssl://mqtt-backup:8883` -- aliases on `wd-mqtt`, a network that carries MQTT only |
| certificate | each half's own gateway **web** certificate, signed by the repo CA (`stacks/npm/certs/rootCA.pem`), SANs `ignition localhost mqtt-master` / `ignition-backup localhost mqtt-backup` |
| listeners | TLS 8883 only; plain TCP, WebSocket and anonymous off |
| login | user `ignition`, password `MQTT_PASSWORD` in `.secrets.env`; the shipped `admin/changeme` deleted |
| clients | hub Engine (`ssl://localhost:8883`, its own set), Edge2/3/4 Transmission (both halves, keepalive 10, Rolling History Buffer), wd-control's wire listener |
| settings page | `https://console.test/app/mqtt-distributor` (opens signed in via `/_wd/login?next=`) |

```bash
scripts/distributor-setup.sh ignition          # certificate, TLS, user -- the hub
scripts/distributor-setup.sh ignition-backup   # the backup's own certificate
scripts/ign-mqtt.sh setup                      # hub Engine + the MQTT edges -> the broker
scripts/ign-mqtt.sh status                     # what each module is pointed at
```

All three are idempotent: each reads first and writes only what differs,
because every Distributor user or settings write restarts the whole broker.

`ignition-edge1` isn't on `wd-mqtt`: its road to the hub is the Gateway
Network, and its transmitter stays disabled.

## Certificates are config resources, not uploads

The single biggest time sink when the TLS road was first built (on the earlier
EMQX broker, and still true for the Cirrus Link modules). `cert-file` is an
ordinary resource under the module (`com.cirruslink.mqtt.engine.gateway/cert-file`,
and the Transmission equivalent), and the PEM lives in `config.fileContents`.
Three traps, in the order they bite:

1. **`fileContents` is an encrypted value**, not a string:
   `{"type":"Embedded","data":{<jwe>}}`, the same shape as a password. Sending the PEM as
   a plain string fails with **422 `Unable to read required property 'type'`** — which
   reads like a missing *resource* type and is actually the missing secret-value
   discriminator.
2. **Encrypt it by POSTing the PEM as a RAW body** to `/data/api/v1/encryption/encrypt` —
   not `{"plaintext": …}`, and not JSON-encoded. All three variants return **200 with a
   valid-looking JWE**. Only the raw one is correct: RTK Query treats a string body as
   not-jsonifiable and passes it through untouched, whereas JSON-encoding wraps the PEM in
   quotes and turns every newline into a literal `\n` — and a PEM without real line breaks
   is not a PEM. The mistake surfaces much later, at connect time, as
   `java.security.cert.CertificateException: No certificate data found`.
3. **`caCertFile` takes the resource NAME** (`rootCA`), not the file name
   (`rootCA.pem`) — the latter is rejected with a 422 carrying an empty `messages` array.

`/data/api/v1/resources/datafile/…` does **not** serve this type; it 404s for every path
shape, so do not go looking for an upload endpoint.

Transmission's **RPC client** (the raw-MQTT `publish()` the MQTT demo's
notifications use) needs the CA and credentials too, separately from the
server entry; `ign-mqtt.sh setup` sets both. Without them `publish()` reports
`ok` and delivers nothing.

**MQTT Transmission on Edge is fine.** Third-party modules generally do not run on Edge
gateways, but Cirrus Link's MQTT modules are one of the explicitly allowlisted
exceptions (the Edge IIoT product). Embr Charts is not — see [EAM.md](EAM.md).

## The CA

`make certs` makes the local CA and the `*.test` leaf (`stacks/npm/create-certs.sh`);
`distributor-setup.sh` signs each hub half's web certificate with the same CA. One
CA for HTTPS and MQTT means a gateway that trusts it trusts both. The CA's private
key is gitignored and machine-local: it is the authority to mint certificates every
machine trusting it will accept.

## Verify

`scripts/ign-mqtt.sh status`, and `./wd verify-demos`, which fails on an
Engine server set with no namespace bound (it subscribes to nothing while every
status reads Good, T-D10) and on an Engine server pointing anywhere but the
hub pair. Independently of Ignition, wd-control's MQTT listener is itself a
TLS client of the broker: `curl -s localhost:29485/state` shows its `wire`
block (which half it is connected to, messages received and forwarded).
From a machine with `mosquitto_sub` and the repo CA, on `backbone`:

```bash
mosquitto_sub -h ignition -p 8883 --cafile stacks/npm/certs/rootCA.pem \
  -u ignition -P "$(grep ^MQTT_PASSWORD= .secrets.env | cut -d= -f2-)" -t 'spBv1.0/#' -v
```

## Troubleshooting

- **Connection refused** — the half you dialled is the standby, which runs no broker
  (T-D1). Every client lists both halves for this reason.
- **TLS handshake failure** — the gateway is addressing the broker by a name not in
  that half's SANs, or its trust store lacks the repo CA.
- **Connected but no tags** — the Engine server set has no Sparkplug namespace bound
  (T-D10), or Transmission has nothing to publish: it publishes tags matched by its
  Tag Agent's path filter, and an edge with none publishes only births.
- **Every client dropped at once** — a Distributor user or settings change restarts
  the broker; plan those for a quiet moment.
