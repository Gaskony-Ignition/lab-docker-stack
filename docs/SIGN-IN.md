# Opening a gateway without typing a password

Every demonstration eventually needs a gateway's own config UI on screen — the
Gateway Network page, EAM's task list, the MQTT module, the redundancy tab —
and every one of those detours costs a password lookup in front of the room
unless the console's cards carry the buttons instead.

```text
GatewayAdmin -> Demos      a button per stack that has a UI
   Hub gateway             opens ALREADY SIGNED IN
   Edge 1 gateway          opens ALREADY SIGNED IN
   Pair front door         opens ALREADY SIGNED IN (whichever half is active)
   Proxy manager    [key]  opens, and `key` shows the stored login
```

```text
make signin           the whole thing: proxy door + secrets + Designer setting
make secrets          just the stored UI logins
make secrets-list     what is stored -- NAMES only, never a value
make designer-idp     just the Designer setting
```

---

## What actually happens on a click

The click does not send a password to the browser. It opens

```text
https://console.test/_wd/login?next=/web/home
```

which nginx proxies to the control plane. That performs the login **server
side** and answers `302` with the gateway's own session cookies. The browser
lands on `/web/home` already authenticated; the credential never leaves the
machine's own processes.

```text
browser ──► https://<gateway>.test/_wd/login
                     │  (nginx location, gateway hosts only)
                     ▼
              wd-control  ── logs in over https://<gateway>.test
                     │      using .gateways.env
                     ▼
            302 + Set-Cookie ──► browser is signed in
```

### Four things that make it work, none of them obvious

**The session is bound to the ORIGIN it was minted on.** Logging in over
`http://ignition:8088` from inside the Docker network and handing those cookies
to a browser using `https://console.test` gives a clean `401`, with the cookies
present and correct. Measured. So the control plane logs in through the front
door — every `.test` name is a network alias of the `npm` container on
`backbone`, which is what lets it resolve the name from in there.

**The cookie names differ between HTTP and HTTPS.** Over TLS the gateway
applies the cookie-prefix conventions:

```text
http://    webui-sid-<n>          idp-sid-default-<n>       idp-relay-<n>
https://   __Host-webui-sid-<n>   __Secure-idp-sid-...      __Host-idp-relay-...
```

A browser silently **drops** a `__Host-` cookie that breaks the prefix's rules,
so the re-issued cookie is always `Secure`, never carries a `Domain`, and is
forced to `Path=/` when the name demands it. Anything looking for a bare
`webui-sid` finds nothing on the path everybody actually uses.

**All three cookies matter, and the IdP one is the Designer.** `webui-sid` is
the config UI's session; `idp-sid` is the *identity provider's*. Drop the
second and the config UI still opens while the Designer still asks for a
password — which reads as the Designer setting not having worked.

**Only `/_wd/login` is published, never `/_wd/`.** The control plane can start
and stop every stack on the machine and is deliberately unauthenticated on
`backbone`; putting all of it on a gateway's public name would hand that to
anything that can reach the proxy.

---

## The Designer

`make designer-idp` sets two fields in each gateway's `ignition/security-properties`:

| field | set to | why |
|---|---|---|
| `designerAuthStrategy` | `IDENTITY_PROVIDER` | the Designer defers its login to the browser rather than showing its own box |
| `forceIdpAuth` | `false` | **without this the rest buys nothing** |

`forceIdpAuth` is the *"always ask the IdP to re-authenticate users by default"*
checkbox, and it is not advisory. With it on, every authorization request
carries `prompt=login&max_age=1`, which obliges the IdP to challenge however
fresh the session is:

```text
forceIdpAuth=true    ...&prompt=login&max_age=1     -> always challenged
forceIdpAuth=false   (neither parameter)            -> existing session passes
```

Verified by reading the redirect, then by confirming that a browser holding a
live session is sent straight to `/app` with no challenge page in between.

**`allowDesignerSSO` is not the setting it looks like.** Nothing in
`DesignerRoutes` reads it; it is published to the Designer *launcher* through
`/system/gwinfo` and is a legacy 8.1 carry-over. Decompiled to check — the only
readers are `GatewayInfoServlet` and `SystemPropertiesMigrator`. It is left
alone.

**The system identity provider is deliberately NOT changed.** It stays
`default`, the gateway's own. Pointing it at any external provider would make
every gateway login depend on something outside this stack — and an unreachable
IdP means no way into the config UI at all. The Designer setting above needs
only the gateway's own IdP, which is why it costs nothing to leave this alone.

---

## The UIs that cannot be signed in

Nginx Proxy Manager has its own local login and no way in from outside. Its
button opens the page, and a second `key` button shows the stored login. (The
MQTT broker needs none: it is MQTT Distributor, a page in the hub's own
gateway UI, so it opens signed in like any other.)

The values live in the **hub gateway's own secret store** — Ignition 8.3's
`system.secrets`, provider `wd`, type `file`, installed by
`scripts/ign-secrets.sh` from the same `stacks/*/.env` every container is built
from. They are not in a view, not in a binding and not in git, which matters
more than tidiness here: views are committed and EAM pushes them to both edges,
so a password written into one would be published twice over.

The value is read **on the click** and dropped when the popup closes. A binding
would poll, re-reading the secret every few seconds for as long as the page is
open and leaving it in the session's property tree where anything can read it.

> **It does put a password on screen when you press Reveal.** That is the
> trade: the alternative is looking it up somewhere else, in front of the same
> room. It is two deliberate clicks, and it is masked until the second one.

Usernames are stored beside the passwords even though they are not secret —
otherwise the console needs its own table of usernames, and that is the copy
that goes stale the day somebody changes an admin email. It goes stale
silently, because a wrong username and a wrong password fail identically.

### Three traps this cost

- **`system.secrets` raises JAVA exceptions.** Jython's `except Exception` does
  not catch a `java.lang.Throwable`, so a missing secret escaped the function,
  took the whole binding with it, and the popup rendered every field as a red
  ERROR box reading `null` — with nothing in the gateway log, because a failed
  transform reports to the browser and nowhere else. Catch
  `(Exception, JThrowable)`.
- **`PyPlaintext` has no `getValue()`.** It is `getSecretAsString()` (or
  `getSecretAsBytes`). The miss is reported as
  `'com...secrets.Py' object has no attribute 'getValue'`, which reads like the
  wrong object rather than the wrong method. It owns its buffer, so read it
  inside a `with` block.
- **There is no `script` BINDING type in Perspective.** Script is a
  *transform*. A binding declared `"type": "script"` produces nothing at all,
  and every field bound through it renders as a red ERROR box reading `null` —
  identical to the symptom above and with an entirely different cause. Use an
  `expr` binding with a script transform.

---

## Adding a gateway or a UI

Nothing here lists a hostname. A stack that declares `TEST_HOST` gets a button;
a stack that is `KIND=gateway` gets the sign-in door as well.

```text
TEST_HOST=edge3.test          the button
TEST_FORWARD_PORT=8088
KIND=gateway                  ... and the door, signed in with STANZA
STANZA=local-edge3
```

`SIGNIN_STANZA` is the exception, and exists for one case: the redundant
pair's front door is served by the **HAProxy** stack, not by a gateway, yet
what you reach through it is a gateway config UI. `stacks/ignition-ha` names
`local` there — redundancy synchronises the user source from the master, so the
master's admin is the admin on whichever half is currently active.

`make validate` checks that a `SIGNIN_STANZA` names a real gateway's `STANZA`,
because the alternative is a button that 502s at the moment somebody clicks it
in front of an audience while the manifest looks perfectly fine.

After adding one: `make hosts` (this machine's hosts file), then `make signin`.

---

## When a button does not work

| what you see | what it is |
|---|---|
| a page naming a demo to start | that stack is stopped; the button is fine |
| *"Could not sign in"* | the control plane could not log in — check `.gateways.env` exists (`make env`) |
| *"Not a gateway"* | the host has no `KIND=gateway` and no `SIGNIN_STANZA` |
| the gateway's own login page | the door did not run: `make proxy-hosts` |
| the Designer still asks | `forceIdpAuth` is back on, or the browser has no session yet — open the gateway first |

The control plane's own check, from a terminal:

```bash
./wd -- python3 /work/control/autologin.py --base https://console.test \
     --gateway local --selftest
```

It prints cookie **names and lengths** and whether a second, unrelated client
can spend the session. It never prints a value — a session cookie is a bearer
token, and printing one is printing a credential.
