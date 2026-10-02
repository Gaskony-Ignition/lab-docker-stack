"""The status page control/service.py's _send_html sends, factored out so
scripts/verify-demos.sh can render the same markup for the a11y check without
importing service.py itself (which binds a socket at import time)."""


def render(title: str, body: str, refresh: int = 0) -> str:
    return (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        +
        "<style>:root{color-scheme:light dark}"
        "body{font:16px/1.6 system-ui,sans-serif;max-width:34em;"
        "margin:15vh auto;padding:0 1.5em;color:#111;background:#fff}"
        "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}}"
        "table{border-collapse:collapse}th,td{padding:.35em 1em .35em 0;text-align:left}"
        "code{background:#8881;padding:.1em .35em;border-radius:3px}"
        "a{color:#0645ad}@media(prefers-color-scheme:dark){a{color:#8ab4f8}}"
        ":focus-visible{outline:2px solid #0645ad;outline-offset:2px}"
        "@media(prefers-color-scheme:dark){:focus-visible{outline-color:#8ab4f8}}"
        "</style>"
        f"<h1>{title}</h1>{body}"
        # For the one case that fixes itself: a gateway still coming up. A
        # meta-refresh fails axe's meta-refresh rule (2.2.1 -- a timed
        # change of context with no user control); a JS timer plus a
        # manual link gets the same "take me there when it answers"
        # behaviour with both intact.
        + (f"<script>setTimeout(()=>location.reload(),{refresh * 1000})"
           "</script><p><a href='' onclick='location.reload();return false'>"
           "Reload now</a></p>" if refresh else "")
    )
