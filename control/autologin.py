#!/usr/bin/env python3
"""Log in to an Ignition gateway server-side, and hand the SESSION to a browser.

    python3 autologin.py --selftest --base https://console.test --gateway local

WHY THIS EXISTS

During a demonstration you regularly need to look at a gateway's own config UI
-- Gateway Network, EAM, the MQTT module, redundancy -- and every one of those
detours costs a password lookup in front of an audience. The console's cards
carry an "Open" button instead: it establishes the session here, on the server,
and the browser is handed the cookies already authenticated.

The credential therefore never reaches the browser, never lands in a project
resource, and never appears in a URL. What crosses to the browser is a session
cookie, which is what a normal login would have given it anyway.

HOW IGNITION 8.3 ACTUALLY LOGS SOMEBODY IN

Traced against 8.3.8 rather than guessed. The gateway is its OWN OIDC provider
and the config UI is an OIDC client of it, so a login is a full authorization
code round trip that happens entirely inside the one gateway:

    GET  /data/app/login
      -> 302 /idp/<idp>/oidc/auth?...&state=<jwt carrying the landing uri>
      -> 302 /idp/<idp>/authn/login?...&token=T0        (the login PAGE)
    POST /idp/<idp>/authn/next-challenge      {"token": T0}
      -> {"nextChallenge":[{"type":"basic"}], "token": T1}
    POST /idp/<idp>/authn/submit-challenge/basic
                                    {"token": T1, "challenge": {username, password}}
      -> {"success": true, "token": T2}
    GET  /idp/<idp>/oidc/auth?...&token=T2
      -> 302 /data/federate/callback/internal?code=...&state=...
      -> 302 /app                                       (now authenticated)

Three cookies come out of that, and ALL THREE are needed:

    idp-sid-<idp>-<n>   path=/idp/<idp>   the IdP's own session
    idp-relay-<n>       path=/
    webui-sid-<n>       path=/            the config UI's session

The `<n>` suffix is the gateway's own discriminator -- do not synthesise it,
copy the names the gateway actually returned. And the IdP cookie is not
optional bookkeeping: it is the one that makes the DESIGNER work. With
`designerAuthStrategy = IDENTITY_PROVIDER` the Designer sends you to the
browser at /login/idp/start, and that completes without a prompt only if the
browser already holds a live session with the system IdP. Drop `idp-sid` and
the config UI still opens, while the Designer still asks for a password --
which reads as the Designer setting not having worked.

WHY THE LOGIN GOES THROUGH THE FRONT DOOR, NOT THE CONTAINER ADDRESS

The obvious shortcut is to log in over `http://ignition:8088` because we are on
the same Docker network. Don't. The browser will use `https://console.test`, so
the session has to be minted through the same origin the browser will present
it on: same host in the OIDC redirect chain, same proxy, same source address as
far as the gateway is concerned. Minting it on one origin and spending it on
another is the kind of thing that works on the machine it was written on.

Standard library only -- this runs in the toolbox image beside control/service.py.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import ssl
import urllib.parse
import urllib.request

# The gateway is slow to answer while it is still starting, and a hung click is
# worse than a failed one: the operator gets a spinner with no way to tell
# whether it is working.
TIMEOUT = 20

# The gateway's login page is served over the stack's own CA, which the browser
# trusts and this process has no reason to. Verification here would only be
# checking our own proxy's certificate against a store we would have to seed;
# the connection never leaves the machine. Deliberate, and narrow -- it applies
# to this opener and nothing else.
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


class LoginError(RuntimeError):
    """Raised with a message safe to show a user -- never carries a credential."""


def _opener(jar: http.cookiejar.CookieJar, agent: str) -> urllib.request.OpenerDirector:
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=_CTX),
    )
    op.addheaders = [("User-Agent", agent)]
    return op


def login(base: str, username: str, password: str, idp: str = "default",
          agent: str = "Mozilla/5.0 (wd-console autologin)") -> list[dict]:
    """Authenticate against `base` and return the resulting cookies.

    Returns a list of {name, value, path} -- the caller re-issues them to the
    browser. Raises LoginError with a message that names no secret.
    """
    base = base.rstrip("/")
    jar = http.cookiejar.CookieJar()
    op = _opener(jar, agent)

    def post(path: str, obj: dict) -> dict:
        req = urllib.request.Request(
            base + path, data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"})
        with op.open(req, timeout=TIMEOUT) as r:
            body = r.read().decode()
        try:
            return json.loads(body)
        except ValueError:
            raise LoginError(f"{path} did not return JSON -- is this an Ignition gateway?")

    # 1. Start the login. The opener follows the redirects; where we land is the
    #    login page, and its `token` query parameter seeds the challenge loop.
    try:
        with op.open(base + "/data/app/login", timeout=TIMEOUT) as r:
            landed = r.geturl()
    except Exception as e:  # noqa: BLE001 -- the message must stay generic
        raise LoginError(f"could not reach the gateway at {base} ({type(e).__name__})")

    q = urllib.parse.parse_qs(urllib.parse.urlparse(landed).query)
    if "token" not in q:
        # Already authenticated is the common benign case: /data/app/login then
        # bounces straight to /app without ever showing a challenge.
        if "/idp/" not in landed:
            return _cookies(jar)
        raise LoginError("the login page carried no token -- unexpected login flow")
    token = q["token"][0]

    # 2. Ask what it wants. On a stock gateway that is one `basic` challenge;
    #    anything else (MFA, a password reset) is a real answer, not an error we
    #    should paper over, so say which one arrived.
    r = post(f"/idp/{idp}/authn/next-challenge", {"token": token})
    challenges = [c.get("type") for c in r.get("nextChallenge") or []]
    if "basic" not in challenges:
        raise LoginError(
            "this gateway does not want a username and password -- it asked for "
            f"{challenges or 'nothing'}. Open it and log in by hand.")

    # 3. Answer it. This is the only place the credential is used.
    r = post(f"/idp/{idp}/authn/submit-challenge/basic",
             {"token": r["token"],
              "challenge": {"username": username, "password": password}})
    if not r.get("success"):
        raise LoginError("the gateway rejected the stored credential")
    token = r["token"]

    # 4. Back to the OIDC endpoint carrying the now-authenticated token. The
    #    login page's own URL already has every parameter the authorize call
    #    needs, so reuse it rather than rebuilding the query by hand -- `state`
    #    is a signed JWT and `nonce` has to survive untouched.
    oidc = landed.replace(f"/idp/{idp}/authn/login", f"/idp/{idp}/oidc/auth")
    oidc = re.sub(r"([?&])token=[^&]*", r"\1token=" + urllib.parse.quote(token), oidc)
    with op.open(oidc, timeout=TIMEOUT) as r2:
        final = urllib.parse.urlparse(r2.geturl()).path

    cookies = _cookies(jar)
    # Match on a SUBSTRING, never a prefix. Over HTTPS the gateway applies the
    # cookie-prefix conventions and the same three cookies come back renamed:
    #
    #   http://   webui-sid-<n>            idp-sid-default-<n>   idp-relay-<n>
    #   https://  __Host-webui-sid-<n>     __Secure-idp-sid-...  __Host-idp-relay-...
    #
    # That cost an afternoon: the flow completed, the browser was redirected to
    # /app, and this raised "login did not produce a session" -- because the
    # name it was looking for only exists on the plain-HTTP path nobody uses.
    if not any("webui-sid" in c["name"] for c in cookies):
        raise LoginError(f"login did not produce a session (ended at {final})")
    return cookies


def _cookies(jar: http.cookiejar.CookieJar) -> list[dict]:
    return [{"name": c.name, "value": c.value, "path": c.path or "/"} for c in jar]


def verify(base: str, cookies: list[dict], agent: str) -> int:
    """Is this session good, asked as a DIFFERENT client would ask it?

    /data/api/v1/gateway-info is 401 anonymous and 200 authenticated, which is
    the same positive signal ign-gw.js uses. Deliberately a fresh opener with no
    cookie jar and a different User-Agent: the whole design rests on a session
    minted by one client being spendable by another, so the check has to be made
    by another client or it proves nothing.
    """
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_CTX))
    op.addheaders = [
        ("User-Agent", agent),
        ("Cookie", "; ".join(f"{c['name']}={c['value']}" for c in cookies)),
    ]
    try:
        with op.open(base.rstrip("/") + "/data/api/v1/gateway-info", timeout=TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


# --- which gateway is being asked for, and what does it cost to log in -------
#
# The browser arrives at https://console.test/_wd/login, so the ONLY thing
# identifying the gateway is the Host header. Resolving it through the stack
# manifests keeps that derived rather than listed: a gateway added by dropping a
# folder in stacks/ gets an Open button with no edit here, which is the rule the
# rest of this repo already follows.

def gateway_stacks(repo: str = "/work") -> dict:
    """{test host -> {stack, stanza, role}} for every host we can sign in to.

    Deliberately not every TEST_HOST: the login flow below is Ignition's, and
    pointing it at a non-Ignition UI would produce a confusing failure rather
    than a clear refusal. A host qualifies by being a gateway, or by naming its credentials
    with SIGNIN_STANZA.
    """
    import glob
    import os

    out = {}
    for path in sorted(glob.glob(os.path.join(repo, "stacks", "*", "stack.meta"))):
        text = open(path).read().replace("\r", "")

        def field(k, t=text):
            m = re.search(rf"^{k}=(.*)$", t, re.M)
            return m.group(1).strip().strip('"') if m else ""

        host = field("TEST_HOST")
        if not host:
            continue
        # A host is signable if it IS a gateway, or if the manifest says which
        # credentials to use for it. The second case is the redundant pair's
        # front door: ignition.test is served by the HAProxy stack, not by a
        # gateway, yet what you reach through it is a gateway config UI.
        stanza = field("SIGNIN_STANZA") or (
            field("STANZA") if field("KIND") == "gateway" else "")
        if not stanza:
            continue
        out[host] = {
            "stack": os.path.basename(os.path.dirname(path)),
            "stanza": stanza,
            "role": field("ROLE"),
        }
    return out


def credential(stanza: str, creds: str = "/work/.gateways.env") -> tuple:
    """The gateway's admin credential, from the file the repo already generates.

    .gateways.env is `make env`'s output: 0600, gitignored, and already the
    store every other script here reads. It is NOT in the Ignition secret
    provider on purpose -- this login happens on the SERVER, so the secret
    should stay where the server already keeps it. The gateway's own secret
    store exists for the credentials the GATEWAY has to read, which is a
    different problem and is solved in scripts/ign-secrets.sh.
    """
    try:
        env = open(creds).read()
    except OSError:
        raise LoginError("no .gateways.env -- run: make env")
    out = []
    for k in ("user", "password"):
        m = re.search(rf"^{re.escape(stanza)}\.{k}=(.*)$", env, re.M)
        if not m:
            raise LoginError(f"no {stanza}.{k} in .gateways.env -- run: make env")
        out.append(m.group(1))
    return tuple(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="the gateway's front-door URL")
    ap.add_argument("--gateway", default="local", help="stanza name in .gateways.env")
    ap.add_argument("--creds", default="/work/.gateways.env")
    ap.add_argument("--idp", default="default")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--verify-base", help="check the session against a DIFFERENT "
                                          "URL than it was minted on -- which is "
                                          "exactly what the browser does")
    a = ap.parse_args()

    user, password = credential(a.gateway, a.creds)
    cookies = login(a.base, user, password, a.idp)

    # Names and lengths only. A session cookie is a bearer token: printing one
    # is printing a credential, and this output goes to a terminal and a log.
    print("session established; cookies:")
    for c in cookies:
        print(f"   {c['name']}  path={c['path']}  len={len(c['value'])}")

    if a.selftest:
        where = a.verify_base or a.base
        code = verify(where, cookies, "Mozilla/5.0 (a completely different client)")
        print(f"re-checked from a different client at {where}: gateway-info -> {code}")
        if code != 200:
            raise SystemExit("SELFTEST FAILED: the session is not portable between clients")
        print("SELFTEST PASSED: the session is portable, so it can be handed to a browser")


if __name__ == "__main__":
    main()
