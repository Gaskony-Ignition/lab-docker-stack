"""THE WIRE's feed: every broker message, copied to the hub's witness route.

The Sparkplug tab's WIRE panel decodes whatever arrives at the hub's
/system/webdev/GatewayAdmin/sparkplug/wire (sparkplug_demo.handle_wire_post).
The broker is MQTT Distributor on the hub pair, which has no rule engine,
and an Engine custom namespace can't stand in: `spBv1.0` isn't a legal tag name, and the attempt re-birthed every
edge every ~10 s (docs/MQTT-DISTRIBUTOR.md, T-D13). So this subscribes as an
ordinary MQTT client and POSTs {"topic", "payload_b64", "qos"}, the body the
route has always taken.

It runs inside wd-control, in two daemon threads, and shares nothing with
the HTTP service but snapshot(): a slow or dead hub fills a bounded queue
that drops its OLDEST message (counted), never the service.

Failover: the hub pair's standby runs no broker (T-D1), so the client walks
WD_WIRE_BROKERS in order until one accepts, and POSTs to the gateway whose
broker it is on -- the active half, which is the one the console reads.
"""
from __future__ import annotations

import base64
import collections
import http.client
import json
import os
import ssl
import threading
import time
import urllib.parse

try:
    import paho.mqtt.client as mqtt
except ImportError:  # an image built before the dependency was added
    mqtt = None


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


ENABLED = _env("WD_WIRE", "on").lower() not in ("off", "0", "no", "false")
BROKERS = [b.strip() for b in _env("WD_WIRE_BROKERS", "ignition:8883,ignition-backup:8883").split(",") if b.strip()]
TOPICS = [t.strip() for t in _env("WD_WIRE_TOPICS", "spBv1.0/AlarmDemo/#,spBv1.0/STATE/#,notify/#").split(",") if t.strip()]
CA_FILE = _env("WD_WIRE_CA", "/work/stacks/npm/certs/rootCA.pem")
# The broker login: MQTT_PASSWORD in .secrets.env, the user distributor-setup.sh
# creates (step 3). The file has no MQTT_USER unless someone added one, so the
# user defaults to `ignition`, as scripts/lib.sh:mqtt_user does.
CREDS_FILE = _env("WD_WIRE_CREDS", "/work/.secrets.env")
DEFAULT_USER = "ignition"
# Empty = the gateway whose broker we're on, at HUB_PORT + ROUTE.
POST_URL = _env("WD_WIRE_POST_URL", "")
HUB_PORT = int(_env("WD_WIRE_HUB_PORT", "8088"))
ROUTE = "/system/webdev/GatewayAdmin/sparkplug/wire"
QUEUE_MAX = int(_env("WD_WIRE_QUEUE", "1000"))
MAX_AGE = float(_env("WD_WIRE_MAX_AGE", "30"))      # s; older than this isn't news
KEEPALIVE = 10                                     # s; a dead half is noticed in ~15 s
CLIENT_ID = _env("WD_WIRE_CLIENT_ID", "wd-control-wire")

_lock = threading.Lock()
_wake = threading.Condition(_lock)
_queue: collections.deque = collections.deque()
_status = {
    "enabled": ENABLED, "state": "starting", "connected": False, "broker": "",
    "since": int(time.time()), "brokers": BROKERS, "postTo": "",
    "received": 0, "forwarded": 0, "dropped": 0, "rejected": 0, "queued": 0,
    "lastError": "", "hubReachable": None,
}
_active_host = ""


def _log(msg: str) -> None:
    print(f"wire {time.strftime('%H:%M:%S')}: {msg}", flush=True)


def snapshot() -> dict:
    with _lock:
        s = dict(_status)
        s["queued"] = len(_queue)
    return s


def _set(**kw) -> None:
    with _lock:
        _status.update(kw)


def _error(text: str) -> None:
    # One log line per DIFFERENT error, not one per retry.
    with _lock:
        same = _status["lastError"] == text
        _status["lastError"] = text
    if not same:
        _log(text)


def _creds() -> tuple:
    """MQTT_USER (default `ignition`) / MQTT_PASSWORD from .secrets.env. Read on
    every connect so `make env` takes effect without a restart. Never logged."""
    user, pw = DEFAULT_USER, ""
    with open(CREDS_FILE) as f:
        for line in f:
            k, sep, v = line.strip().partition("=")
            if not sep or k.startswith("#"):
                continue
            v = v.strip().strip('"').strip("'")
            if k == "MQTT_USER":
                user = v
            elif k == "MQTT_PASSWORD":
                pw = v
    if not user or not pw:
        raise RuntimeError(f"no MQTT_PASSWORD in {CREDS_FILE} -- run: make env")
    return user, pw


# --- MQTT side ------------------------------------------------------------------

def _on_message(client, userdata, msg) -> None:
    # A retained message delivered on SUBSCRIBE is old news (the STATE the host
    # set minutes ago). Skip it.
    if msg.retain:
        return
    item = (msg.topic, bytes(msg.payload), msg.qos, time.time())
    with _lock:
        _status["received"] += 1
        if len(_queue) >= QUEUE_MAX:
            _queue.popleft()
            _status["dropped"] += 1
        _queue.append(item)
        _wake.notify()


def _session(host: str, port: int) -> bool:
    """One connection to one broker, until it ends. True if it got as far as
    subscribing (so the caller resets its backoff)."""
    global _active_host
    user, pw = _creds()
    ctx = {"acked": None}

    def on_connect(client, userdata, flags, reason_code, properties):
        ctx["acked"] = not reason_code.is_failure
        if ctx["acked"]:
            client.subscribe([(t, 1) for t in TOPICS])
        else:
            ctx["why"] = str(reason_code)

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID,
                    protocol=mqtt.MQTTv311, clean_session=True)
    c.username_pw_set(user, pw)
    c.tls_set(ca_certs=CA_FILE, cert_reqs=ssl.CERT_REQUIRED)
    c.connect_timeout = 5
    c.on_connect = on_connect
    c.on_message = _on_message
    try:
        c.connect(host, port, keepalive=KEEPALIVE)
        deadline = time.time() + 10
        while True:
            rc = c.loop(timeout=1.0)
            if rc != mqtt.MQTT_ERR_SUCCESS:
                break
            if ctx["acked"] is None:
                if time.time() > deadline:
                    ctx["why"] = "no CONNACK in 10 s"
                    break
                continue
            if ctx["acked"] is False:
                break
            if not _status["connected"]:
                _active_host = host
                _set(connected=True, broker=f"{host}:{port}", since=int(time.time()),
                     state=f"connected to {host}:{port}", lastError="",
                     postTo=_post_url())
                _log(f"connected to {host}:{port}, subscribed to {', '.join(TOPICS)}")
    finally:
        try:
            c.disconnect()
        except Exception:
            pass
        try:
            c.loop(timeout=0.1)
        except Exception:
            pass
    if ctx["acked"]:
        _set(connected=False, since=int(time.time()), state=f"lost {host}:{port}, reconnecting")
        _error(f"lost {host}:{port} ({mqtt.error_string(rc)}), reconnecting")
        return True
    raise ConnectionError(f"refused: {ctx.get('why') or 'connection closed'}")


def _mqtt_loop() -> None:
    """Walk the list until a broker accepts; after a session ends, start again
    from the top (the master is first). A round where none answers is ONE
    error, logged only when it reads differently from the last one."""
    backoff = 1
    while True:
        worked, why = False, []
        for b in BROKERS:
            host, _, port = b.rpartition(":")
            if not host:
                host, port = port, "8883"
            try:
                worked = _session(host, int(port))
            except Exception as e:
                why.append(f"{host}:{port} {type(e).__name__}: {e}")
            if worked:
                break
        if worked:
            backoff = 1
            continue
        _set(connected=False, state=f"no broker answering, retrying in {backoff} s")
        _error("no broker answering: " + "; ".join(why))
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)


# --- HTTP side ------------------------------------------------------------------

def _post_url() -> str:
    if POST_URL:
        return POST_URL
    return f"http://{_active_host or 'ignition'}:{HUB_PORT}{ROUTE}"


def _post_loop() -> None:
    conn, conn_for = None, ""
    backoff = 0.5
    while True:
        with _lock:
            while not _queue:
                _wake.wait()
            topic, payload, qos, at = _queue.popleft()
        body = json.dumps({"topic": topic,
                           "payload_b64": base64.b64encode(payload).decode("ascii"),
                           "qos": qos}).encode("utf-8")
        while True:
            if time.time() - at > MAX_AGE:
                with _lock:
                    _status["dropped"] += 1
                break
            url = _post_url()
            u = urllib.parse.urlsplit(url)
            reused = conn is not None and conn_for == u.netloc
            try:
                if conn is None or conn_for != u.netloc:
                    if conn is not None:
                        conn.close()
                    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=5)
                    conn_for = u.netloc
                conn.request("POST", u.path or "/", body,
                             {"Content-Type": "application/json", "Connection": "keep-alive"})
                resp = conn.getresponse()
                text = resp.read()
            except Exception as e:
                if conn is not None:
                    conn.close()
                conn = None
                if reused:
                    # The hub closed an idle keep-alive connection; that's
                    # not the hub being down. Retry once on a fresh one.
                    continue
                if _status["hubReachable"] is not False:
                    _set(hubReachable=False)
                    _log(f"hub witness {url} unreachable ({type(e).__name__}: {e}), queueing")
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
                continue
            backoff = 0.5
            if _status["hubReachable"] is not True:
                _set(hubReachable=True, postTo=url)
                _log(f"posting to {url}")
            ok = resp.status == 200
            try:
                ok = ok and bool(json.loads(text or b"{}").get("ok"))
            except ValueError:
                ok = False
            with _lock:
                if ok:
                    _status["forwarded"] += 1
                else:
                    _status["rejected"] += 1
            if not ok:
                _error(f"hub rejected a message on {topic}: HTTP {resp.status} {text[:120]!r}")
            break


def start() -> None:
    if not ENABLED:
        _set(state="off (WD_WIRE=off)")
        _log("off (WD_WIRE=off)")
        return
    if mqtt is None:
        _set(state="off: no paho-mqtt in this image -- rebuild it (./wd, TAG in the launcher)")
        _log(_status["state"])
        return
    _log(f"brokers {', '.join(BROKERS)}; queue {QUEUE_MAX}")
    threading.Thread(target=_mqtt_loop, name="wire-mqtt", daemon=True).start()
    threading.Thread(target=_post_loop, name="wire-post", daemon=True).start()
