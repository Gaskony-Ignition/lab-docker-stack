"""The demonstration script, in the demonstration.

WHY THIS IS A SCRIPT LIBRARY AND NOT A DOCUMENT

docs/ explains how the stack WORKS. This explains how to SHOW it, and the two
audiences never overlap: nobody reads a repository during a customer meeting,
and a customer cannot read it at all. Keeping the click-path here means the
guide travels with the thing it describes -- it is a project resource, so EAM
carries it to the edges along with everything else, and a customer left alone
with the stack can drive all three demonstrations without anyone sitting beside
them.

WHY THE CONTENT IS DATA

Each topic is a plain dict rendered by Guide/Panel. Adding a step is editing a
list, not a view, so nobody has to lay out Perspective JSON to fix a sentence --
which is the difference between a guide that stays accurate and one that rots.

WRITE THE 'watch' LINE FOR SOMEONE WHO HAS NEVER SEEN THIS.
The demonstrations are all about a thing that takes a few seconds to happen
somewhere other than where you clicked. If nobody says where to look, the whole
effect lands after the audience has stopped looking.
"""

# Jython 2.7 -- no f-strings, and `except Exception, e` rather than `as e`.

TOPICS = {

    "styling": {
        "height": 560,
        "title": "One theme, everywhere at once",
        "lead": "Nineteen style packs ship with the stack. Choosing one rewrites "
                "a single project resource on the gateway, and every screen that "
                "inherits it re-themes itself -- including sessions that are "
                "already open, which is the part people do not expect.",
        "where": "Site 1 or Site 2, the /admin page",
        "steps": [
            {"do": "Open /admin on Site 1, and leave Site 2's dashboard open on "
                   "another screen or tab.",
             "then": "Two sites, same theme, different line data. That contrast "
                     "is the point -- the theme is shared, the data is not."},
            {"do": "Click any swatch.",
             "then": "Site 1 re-themes immediately. Watch the OTHER tab: it "
                     "follows within about five seconds without being reloaded."},
            {"do": "Click 'Push both edges'.",
             "then": "The two edge gateways receive the same theme as a project "
                     "push. They are separate gateways on separate hardware in a "
                     "real deployment; nothing was copied by hand."},
            {"do": "Try the Theme button on any page.",
             "then": "That one changes only YOUR session and leaves everyone "
                     "else's alone -- useful for showing a pack without "
                     "committing to it mid-meeting."},
        ],
        "watch": "Keep a second screen on a page you did NOT click. The demo is "
                 "that it changes there too.",
        "note": "The theme is a project resource rather than a database row, "
                "because project resources are what travel to an edge.",
    },

    "eam": {
        "height": 440,
        "title": "Distributing projects to remote gateways",
        "lead": "Enterprise Administration pushes a project from this hub to the "
                "edge gateways. In a real site those are in a plant room, on a "
                "vessel, or on the other side of the country -- nobody opens a "
                "Designer against them.",
        "where": "This page, the EAM tab",
        "steps": [
            {"do": "Look at the agent cards. Each is a real, separate gateway.",
             "then": "Connected means the hub can manage it. The TRIAL pill is "
                     "this demo stack's licensing, not part of the product story."},
            {"do": "Change the theme on /admin first, then come back and push.",
             "then": "Gives the push something visible to carry, so the edges "
                     "obviously changed rather than obviously did nothing."},
            {"do": "Push a project to one edge, then open that edge's own screen.",
             "then": "The edge is now running the new version. It was never "
                     "restarted and nobody logged into it."},
        ],
        "watch": "A push reports Success even when it had nothing to send. Prove "
                 "it by looking at the edge, not at the task result -- which is "
                 "true of the real product too, and worth saying out loud.",
        "note": "An edge runs exactly one project, so the hub's project and its "
                "parents are flattened into it on the way.",
    },

    "storeforward": {
        "height": 555,
        "title": "What happens when the link drops",
        "lead": "Two edges send data to this hub by two different roads: one over "
                "the Gateway Network, one as MQTT Sparkplug over TLS through a "
                "broker. Cut either one and the edge keeps collecting. Restore it "
                "and the missing minutes arrive with their ORIGINAL timestamps -- "
                "so the history has a gap while it is down and no gap afterwards.",
        "where": "This page, the Store & Forward tab",
        "steps": [
            {"do": "Watch the trend for a moment first, so the shape of normal is "
                   "familiar.",
             "then": "Two lines, one per site, both live."},
            {"do": "Choose a duration and click 'Cut Site 2'.",
             "then": "Its box goes Offline within seconds and its line stops. "
                     "The edge itself is fine -- it is still reading and still "
                     "recording, it just cannot reach us."},
            {"do": "Wait for it to come back on its own.",
             "then": "This is the moment. The gap in the trend FILLS IN "
                     "backwards, because what arrives is the buffered history, "
                     "stamped when it was measured rather than when it arrived."},
            {"do": "If there is time, click 'Cut both' and let them recover together.",
             "then": "Two different transports, two different buffers, same "
                     "outcome -- which is the argument that this is a property of "
                     "the platform rather than a trick of one protocol."},
        ],
        "watch": "The recovery, not the outage. Anyone can show a red card; the "
                 "demonstration is the hole closing up afterwards.",
        "note": "Site 2 is cut by banning it at the broker. Site 1 cuts itself on "
                "a timer, because the hub cannot cut the road it would need to "
                "give the connection back.",
    },

    "redundancy": {
        "height": 500,
        "title": "Losing a gateway without losing the session",
        "lead": "Two gateways run as a redundant pair -- one active, one warm "
                "standby holding a synchronised copy of everything. A single "
                "address in front of them always points at whichever one is "
                "active, so a browser session survives the changeover.",
        "where": "This page, the Redundancy tab",
        "steps": [
            {"do": "Note which half is Active and which is Warm, and check the "
                   "sync table is current.",
             "then": "The standby is not idle -- it is being kept identical, "
                     "continuously."},
            {"do": "Open the dashboard in another tab and leave it running.",
             "then": "This is what you are about to NOT lose. Do this before the "
                     "failover, not after."},
            {"do": "Hand responsibility over.",
             "then": "The roles swap within a few seconds. Go back to the other "
                     "tab: it is still live, and it is now being served by the "
                     "gateway that was standing by a moment ago."},
            {"do": "Hand it back.",
             "then": "Same again in the other direction. Nothing was restarted "
                     "and nothing was reconfigured."},
        ],
        "watch": "The dashboard tab you left open, not this page. A page that "
                 "keeps working is a much better proof than a status field that "
                 "changes.",
        "note": "The single address is a proxy choosing the half that reports "
                "itself active. Ignition does not ship one, and does not claim "
                "to -- load balancing is left to the thing in front.",
    },
}

# The console's tabs, in the order they are laid out, so the Guide button can
# ask for "whatever is on screen" without the view knowing any topic names.
# Indexed by the console's tab index, and it must cover EVERY tab: a short list
# does not fail, it falls off the end and returns None, so a tab silently has no
# Guide button and nothing says why. Demos is index 4 and is the tab the console
# now OPENS on -- it has no guide yet, and that None is a deliberate gap rather
# than a missing entry. The Architecture tab is a drawing tool, not a
# demonstration, so it genuinely has none. The MQTT tab (5) has none either,
# and that is deliberate: every scenario on that tab carries its own one-line
# instruction on the panel, so a guide would be a second, drifting copy of it.
# `has_guide` is bound to the Guide button's meta.visible, so None hides the
# button rather than opening an empty popup.
#              0=EAM        1=Store&Fwd     2=Architecture  3=Redundancy  4=Demos  5=MQTT
TAB_TOPICS = ["eam",        "storeforward", None,           "redundancy", None,    None]


def topic_for_tab(index):
    """Which guide belongs to the tab at `index`. None where there is no guide
    (the Architecture tab is a drawing tool, not a demonstration)."""
    try:
        i = int(index)
    except Exception:
        return None
    if 0 <= i < len(TAB_TOPICS):
        return TAB_TOPICS[i]
    return None


def has_guide(index):
    return topic_for_tab(index) is not None


def get(topic):
    """The whole topic, with empty defaults, so the view never has to test for
    a missing key -- a KeyError in a Perspective binding renders the component
    as a red ERROR box and logs nothing anywhere a script can see it."""
    t = TOPICS.get(topic) or {}
    return {
        "height": t.get("height", 560),
        "title": t.get("title", "Guide"),
        "lead": t.get("lead", ""),
        "where": t.get("where", ""),
        "watch": t.get("watch", ""),
        "note": t.get("note", ""),
    }


def popup_size(topic):
    """Width and height for the popup, so a short guide does not open with a
    third of it empty.

    The heights are MEASURED (content bottom minus shell top, plus padding, at
    this width), not guessed, and they are a HINT rather than a contract: the
    panel scrolls, so text edited later can overflow harmlessly. Re-measure by
    comparing scrollHeight to clientHeight on the shell if a guide grows a lot.
    """
    return {"width": 660, "height": get(topic)["height"]}


def steps(topic):
    """Rows for the repeater: one instance per step, numbered from 1."""
    out = []
    t = TOPICS.get(topic) or {}
    n = 0
    for s in t.get("steps", []):
        n += 1
        out.append({"n": str(n), "do": s.get("do", ""), "then": s.get("then", "")})
    return out
