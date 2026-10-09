#!/usr/bin/env python3
"""Generate the GatewayAdmin MQTT tab's views -- the guided-scenarios page.

GENERATED FILES: views/Sparkplug and every views/Sparkplug* folder this script
writes, the tab's MQTT traffic dock in page-config, and the Sparkplug block of the
project stylesheet all come from HERE. Edit this script and re-run it; hand edits
to those files are overwritten on the next run.

    python3 scripts/gen-sparkplug-views.py && ./wd validate && ./wd deploy PROJECT=GatewayAdmin

The data each panel shows comes from sparkplug_demo.page_*() in the project's
script library, and so do most of the words a first-time viewer reads.
"""
import json
import os
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSP = REPO + '/ignition/projects/GatewayAdmin/com.inductiveautomation.perspective'
VIEWS = PERSP + '/views'
S = '{session.custom.style}'
DOCK_ID = 'sp-traffic'
VH = '{page.props.dimensions.viewport.height}'
# The drawer's top edge: the scenario's one line ends 232px down the page
# (measured, 1366x640, 21/09/2026) and stays visible above the drawer, with
# half the 8px gap under it.
DRAWER_TOP = 232 + 4

RESOURCE = {
    "scope": "G", "version": 1, "restricted": False, "overridable": True,
    "files": ["view.json"],
    "attributes": {"lastModification": {"actor": "external", "timestamp": "1970-01-01T00:00:00Z"}},
}

# The two-edge Level chart, as first built for this tab; trend_component()
# adjusts it (5 s base interval, legend, fonts, binding).
TREND = json.loads(r"""{
 "meta": {
  "name": "trend"
 },
 "position": {
  "basis": "0px",
  "grow": 1,
  "shrink": 1
 },
 "propConfig": {
  "props.dataSources": {
   "binding": {
    "config": {
     "expression": "now(5000)"
    },
    "transforms": [
     {
      "code": "\timport sparkplug_demo\n\treturn sparkplug_demo.trend_series()\n",
      "type": "script"
     }
    ],
    "type": "expr"
   }
  }
 },
 "props": {
  "background": {
   "color": "transparent",
   "gradient": {
    "colors": [],
    "direction": "linear",
    "rotation": 0
   },
   "opacity": 0,
   "render": "color"
  },
  "cursor": {
   "enabled": true,
   "lineY": {
    "disabled": true
   }
  },
  "dataSources": {
   "edge1": [],
   "edge2": []
  },
  "legend": {
   "enabled": true,
   "labels": {
    "font": {
     "color": "#9ca3af",
     "size": 11,
     "weight": 400
    },
    "text": "{name}"
   },
   "markers": {
    "enabled": true,
    "height": 8,
    "mirrorLookOfSeries": true,
    "width": 8
   },
   "position": "right"
  },
  "series": [
   {
    "data": {
     "source": "edge1",
     "x": "t",
     "y": "v"
    },
    "hiddenInLegend": false,
    "label": {
     "text": ""
    },
    "line": {
     "appearance": {
      "connect": false,
      "fill": {
       "color": "#60a5fa",
       "opacity": 0
      },
      "stroke": {
       "color": "#60a5fa",
       "dashArray": "",
       "opacity": 1,
       "width": 2
      },
      "tensionX": 1,
      "tensionY": 1
     }
    },
    "name": "Edge 3 Level",
    "render": "line",
    "tooltip": {
     "cornerRadius": 3,
     "enabled": true,
     "pointerLength": 4,
     "text": "{name}: [bold]{valueY}[/]%"
    },
    "visible": true,
    "xAxis": "t",
    "yAxis": "level",
    "zIndex": 0
   },
   {
    "data": {
     "source": "edge2",
     "x": "t",
     "y": "v"
    },
    "hiddenInLegend": false,
    "label": {
     "text": ""
    },
    "line": {
     "appearance": {
      "connect": false,
      "fill": {
       "color": "#fb923c",
       "opacity": 0
      },
      "stroke": {
       "color": "#fb923c",
       "dashArray": "",
       "opacity": 1,
       "width": 2
      },
      "tensionX": 1,
      "tensionY": 1
     }
    },
    "name": "Edge 4 Level",
    "render": "line",
    "tooltip": {
     "cornerRadius": 3,
     "enabled": true,
     "pointerLength": 4,
     "text": "{name}: [bold]{valueY}[/]%"
    },
    "visible": true,
    "xAxis": "t",
    "yAxis": "level",
    "zIndex": 0
   }
  ],
  "style": {
   "minHeight": "150px",
   "width": "100%"
  },
  "title": {
   "text": ""
  },
  "xAxes": [
   {
    "appearance": {
     "font": {
      "size": "10px",
      "weight": 500
     },
     "grid": {
      "color": "#374151",
      "dashArray": "3 3",
      "minDistance": 40,
      "opacity": 1
     },
     "inside": false,
     "labels": {
      "color": "#9ca3af",
      "opacity": 1,
      "rotation": 0
     },
     "opposite": false
    },
    "date": {
     "baseInterval": {
      "count": 1,
      "enabled": false,
      "skipEmptyPeriods": false,
      "timeUnit": "second"
     },
     "break": {
      "enabled": false,
      "endDate": "",
      "size": 0.05,
      "startDate": ""
     },
     "format": "HH:mm:ss",
     "inputFormat": "yyyy-MM-dd HH:mm:ss",
     "range": {
      "max": "",
      "min": "",
      "useStrict": false
     }
    },
    "label": {
     "color": "",
     "enabled": false,
     "text": ""
    },
    "name": "t",
    "render": "date",
    "visible": true
   }
  ],
  "yAxes": [
   {
    "appearance": {
     "font": {
      "size": "10px",
      "weight": 500
     },
     "grid": {
      "color": "#374151",
      "dashArray": "3 3",
      "minDistance": 40,
      "opacity": 1
     },
     "inside": false,
     "labels": {
      "color": "#9ca3af",
      "opacity": 1,
      "rotation": 0
     },
     "opposite": false
    },
    "label": {
     "color": "",
     "enabled": false,
     "text": ""
    },
    "name": "level",
    "render": "value",
    "value": {
     "break": {
      "enabled": false,
      "endValue": 0,
      "size": 0.05,
      "startValue": 0
     },
     "format": "#,###",
     "logarithmic": false,
     "range": {
      "max": "",
      "min": "",
      "useStrict": false
     }
    },
    "visible": true
   }
  ]
 },
 "type": "ia.chart.xy"
}""")

# ------------------------------------------------------------------ builders

def expr(e):
    return {"binding": {"type": "expr", "config": {"expression": e}}}


def data_binding(fn, poll=2000):
    return {"binding": {"type": "expr", "config": {"expression": "now(%d)" % poll},
                        "transforms": [{"type": "script",
                                        "code": "\timport sparkplug_demo\n\treturn sparkplug_demo.%s\n" % fn}]}}


def comp(type_, name, props=None, pos=None, pc=None, children=None, events=None, meta=None):
    c = {"type": type_, "meta": {"name": name}, "props": props or {}}
    if meta:
        c["meta"].update(meta)
    if pos is not None:
        c["position"] = pos
    if pc:
        c["propConfig"] = pc
    if children is not None:
        c["children"] = children
    if events:
        c["events"] = {"component": events}
    return c


def show_if(c, expression):
    """Remove a flex child from the layout (position.display) unless expression is true."""
    c.setdefault("propConfig", {})["position.display"] = expr(expression)
    return c


def class_expr(theme=None, extra=None, extra_expr=None):
    parts = []
    if theme:
        parts.append("%s + '/%s'" % (S, theme))
    if extra:
        parts.append("' %s'" % extra if parts else "'%s'" % extra)
    if extra_expr:
        parts.append(extra_expr)
    return expr(" + ".join(parts))


def label(name, text='', theme=None, extra=None, style=None, pos=None, text_expr=None,
          tip_expr=None, extra_expr=None, wrap=False):
    st = {"lineHeight": "1.35", "whiteSpace": "normal" if wrap else "nowrap"}
    if wrap:
        st["minWidth"] = "0"
    st.update(style or {})
    props = {"text": text, "style": st}
    pc = {}
    if theme or extra_expr:
        pc["props.style.classes"] = class_expr(theme, extra, extra_expr)
    elif extra:
        props["style"]["classes"] = extra
    if text_expr:
        pc["props.text"] = expr(text_expr)
    meta = None
    if tip_expr:
        meta = {"tooltip": {"enabled": True}}
        pc["meta.tooltip.text"] = expr(tip_expr)
    return comp("ia.display.label", name, props, pos if pos is not None else {"shrink": 0}, pc or None, meta=meta)


def flex(name, direction, children, pos=None, style=None, align=None, justify=None, wrap=None,
         classes=None, classes_expr=None):
    props = {"direction": direction, "style": dict(style or {})}
    if align:
        props["alignItems"] = align
    if justify:
        props["justify"] = justify
    if wrap:
        props["wrap"] = wrap
    pc = None
    if classes:
        props["style"]["classes"] = classes
    if classes_expr:
        pc = {"props.style.classes": expr(classes_expr)}
    return comp("ia.container.flex", name, props, pos if pos is not None else {"shrink": 0}, pc, children)


def spacer(name):
    return comp("ia.container.flex", name, {"direction": "row"}, {"basis": "0px", "grow": 1, "shrink": 1}, None, [])


def button(name, text, code, theme='buttons/chip', class_e=None, pos=None, style=None):
    st = {"height": "30px", "minHeight": "30px", "padding": "0 14px", "whiteSpace": "nowrap", "lineHeight": "1"}
    st.update(style or {})
    pc = {"props.style.classes": expr(class_e) if class_e else class_expr(theme)}
    ev = {"onActionPerformed": {"type": "script", "scope": "G", "config": {"script": code}}}
    return comp("ia.input.button", name, {"text": text, "style": st}, pos if pos is not None else {"shrink": 0}, pc, events=ev)


# The drawer sizes itself to the window it opens in, not to a fixed box.
VIEWPORT = ("\ttry:\n"
            "\t\tvp = self.page.props.dimensions.viewport\n"
            "\t\tw, h = int(vp.width), int(vp.height)\n"
            "\texcept:\n"
            "\t\tw, h = 1366, 640\n")


def view(root, custom=None, params=None, size=(1200, 400), pc=None):
    v = {"custom": custom or {}, "params": params or {},
         "props": {"defaultSize": {"width": size[0], "height": size[1]}}, "root": root}
    if pc:
        v["propConfig"] = pc
    return v


def card(name, children, pos=None, style=None, gap='6px'):
    st = {"gap": gap, "minWidth": "0", "overflow": "hidden"}
    st.update(style or {})
    return flex(name, "column", children, pos=pos, style=st, classes="sp-card")


def scenario_root(children):
    # Top-aligned (never centred); 4px here + the tab root's 10px = the bottom gutter.
    return flex("root", "column", children, pos={"basis": "auto", "shrink": 0},
                style={"height": "100%", "width": "100%", "gap": "8px", "overflow": "hidden",
                       "paddingBottom": "4px", "boxSizing": "border-box"})


def try_line(text):
    """The ONE line of a scenario: what to do and what to watch, together.

    It was two -- a "Do" line and a "Watch" line -- and on a 640px laptop the
    pair wrapped to four. One sentence, at most ~110 characters, is the budget
    (21/09/2026: "succinct but clear"); the buttons it names are in the
    rail, directly under the scenario list."""
    assert len(text) <= 112, (len(text), text)
    return flex("tryWatch", "row", [
        label("tryText", text, pos={"basis": "0px", "grow": 1, "shrink": 1},
              extra="sp-ellipsis", tip_expr="'%s'" % text.replace("'", "\\'"),
              style={"minWidth": "0"}),
    ], align="center", classes="sp-try", style={"gap": "8px"})


# ------------------------------------------------------------------ the rail
#
# The left rail is the console's SHARED one (scripts/gen-rail-views.py): these
# embed the same RailHead / NotRunning / RailGroup / RailFact / RailButton /
# RailLink views the three hand-built tabs embed, and demo_rail.rail('sparkplug')
# feeds them. Nothing about the rail's look is decided here.

RAIL = "view.custom.rail"
RAIL_WIDTH = 260


def embed(name, path, params=None, pc=None, pos=None, fixed=False, style=None):
    """A shared view placed in a rail. Content-sized unless `fixed`, so a
    two-line refusal grows its box and an empty banner collapses to nothing."""
    st = {"width": "100%"}
    st.update(style or {})
    return comp("ia.display.view", name,
                {"path": path, "params": params or {}, "useDefaultViewHeight": bool(fixed),
                 "useDefaultViewWidth": False, "style": st},
                pos if pos is not None else {"shrink": 0}, pc)


def rail_group(name, text):
    return embed(name, "RailGroup", params={"text": text}, fixed=True)


def rail_button(name, key, can, icon="", kind="chip", quiet=False, pos=None):
    """A RailButton. `key` is both its latch key and its demo_rail.ACTIONS key;
    `can` names its guard in the control view's own `c.can` dict."""
    return embed(name, "RailButton",
                 params={"key": key, "action": key, "icon": icon, "kind": kind,
                         "quiet": quiet},
                 pc={"props.params.can": expr(can)}, pos=pos)


def rail_repeater(name, path, key, direction, basis, wrap=None):
    props = {"path": path, "direction": direction, "instances": [],
             "useDefaultViewHeight": True, "useDefaultViewWidth": False,
             "elementPosition": {"basis": basis, "grow": 1 if wrap else 0, "shrink": 1 if wrap else 0},
             "style": {"gap": "4px" if wrap else "0px", "width": "100%"}}
    if wrap:
        props["wrap"] = wrap
    return comp("ia.display.flex-repeater", name, props, {"shrink": 0},
                {"props.instances": expr("{%s.%s}" % (RAIL, key))})


# Scenario rows in the rail: shorter than a button, because six of them sit
# above the selected scenario's own buttons in 534px of laptop rail. Measured
# rather than guessed -- see the budget in build_main().
SCENARIO_ROW = 22


def table(name, data_expr, columns, pos=None, row_h=28, empty="Nothing yet", header=True, wrap=False):
    cols = []
    for col in columns:
        field, title, width, strict = col[:4]
        c = {"field": field, "header": {"title": title, "justify": "left", "align": "center", "style": {}},
             "width": width, "strictWidth": strict, "justify": "left", "align": "center",
             "render": "string", "sortable": False, "resizable": False, "editable": False, "visible": True,
             "style": ({"whiteSpace": "normal", "padding": "4px 8px", "lineHeight": "1.35"} if wrap else
                       {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis",
                        "padding": "0 8px"})}
        if len(col) > 4:
            c.update(col[4])
        cols.append(c)
    props = {
        # Virtualised when rows are a fixed height: its scroller is ReactVirtualized__Grid,
        # where a half-visible last row is the scroll affordance. Wrapping rows are not.
        "data": [], "columns": cols, "virtualized": not wrap,
        "pager": {"top": False, "bottom": False},
        "selection": {"enableRowSelection": False, "enableColumnSelection": False},
        "rows": {"height": "auto" if wrap else row_h, "striped": {"enabled": False}, "highlight": {"enabled": False}},
        "emptyMessage": {"noData": {"text": empty}},
        "enableHeader": header,
        "headerStyle": {"paddingLeft": "8px"},
        "style": {"classes": "sp-table"},
    }
    return comp("ia.display.table", name, props,
                pos if pos is not None else {"basis": "0px", "grow": 1, "shrink": 1},
                {"props.data": expr(data_expr)})


def write_view(name, v):
    d = os.path.join(VIEWS, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'view.json'), 'w') as fh:
        json.dump(v, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    with open(os.path.join(d, 'resource.json'), 'w') as fh:
        json.dump(RESOURCE, fh, indent=2)
        fh.write('\n')


def inputs(names):
    return {"params.%s" % n: {"paramDirection": "input"} for n in names}


EDGE_TITLES = ["Edge 3", "Edge 4"]

BLURB = ("Two edges with no Gateway Network send tags and alarms to the cloud over MQTT; the cloud sends commands back.")

# The alarm repeater's row height, and the row view's own default height.
ALARM_ROW_H = 26


# The cloud notification log starts this far down the page (the one-line
# instruction plus the two "way" cards) and gets whole LOG_RH rows of whatever
# is left. Measured with the others below.
LOG_OFF = 448


LOG_RH = 28


def stat(name, caption, key):
    return flex(name, "column", [
        label("caption", caption, extra="sp-th-plain"),
        label("value", text_expr="{view.custom.d.stats.%s}" % key, extra="sp-value"),
    ], pos={"basis": "auto", "shrink": 0}, style={"gap": "0px", "paddingRight": "18px"})


DRAWER_FIXED = 24 + 30 + 40 + 16


def side_by_side(left, chart, left_basis, left_max):
    """Height is the scarce axis at 1366x640: the chart sits BESIDE its content and takes
    the full panel height. The left column is top-aligned and capped, so a wide window
    gives the chart the width instead of stretching the content."""
    column = flex("left", "column", left, pos={"basis": left_basis, "shrink": 0},
                  style={"gap": "8px", "minWidth": "0", "maxWidth": left_max})
    return flex("body", "row", [column, chart], pos={"basis": "0px", "grow": 1, "shrink": 1},
                align="stretch", style={"gap": "12px", "minHeight": "0"})


def chart_card(title, trend):
    return card("trendCard", [label("title", title, theme="text/muted"),
                              trend],
                pos={"basis": "0px", "grow": 1, "shrink": 1}, gap="0px",
                style={"minHeight": "0", "minWidth": "0", "padding": "6px 14px 4px"})


LEFT_BASIS = "48%"


LEFT_MAX = "760px"


def grow_list(t, row_h, off, count_expr, header=True):
    """A list at the bottom of a left column: fixed-height rows, as many whole rows as the
    window has room for, never more than it holds (at least 3). Bigger windows show more
    rows; the box never holds empty space."""
    head = 32 if header else 0
    t["props"]["rows"]["height"] = row_h
    t["position"] = {"basis": "%dpx" % (head + 3 * row_h + 2), "grow": 0, "shrink": 0}
    t["propConfig"]["position.basis"] = expr(
        "toStr(%d + %d * max(3, min(max(3, %s), floor((%s - %d) / %d)))) + 'px'" % (head + 2, row_h, count_expr, VH, off, row_h))
    return t


# Where a whole-row list's own top edge sits (the layout above it does not
# change with the window), plus its header and everything below it: card
# padding and border, the scenario's bottom gutter and the tab root's 16px.
# MEASURED off the rendered page (21/09/2026, the shared-rail layout: list tops
# 428 / 449 / 382 at 1366x640), not guessed -- re-measure if the layout above
# a list changes. At 1366x640 the alarms list holds 5 rows, 7 at 1844x690, 22
# at 1920x1080, and never half of one.
OUTAGE_LIST_OFF = 481


# 492 until the cloud's own link alarm became a fifth row in each edge's block
# (ALARM_ROW_H taller), which pushed the recent list down by exactly that much.
ALARM_LIST_OFF = 492 + ALARM_ROW_H


# Each edge's three columns, and the widths the header row above them repeats.
LIVE_COLS = ("e3edge", "e3cloud", "e3delay", "e4edge", "e4cloud", "e4delay")
LIVE_DELAY_BASIS = "78px"


def build_live_row():
    cells = [label("name", text_expr="{view.params.name}", pos={"basis": "160px", "shrink": 0},
                   extra="sp-cell sp-strong", tip_expr="{view.params.tip}")]
    for key in LIVE_COLS:
        pos = ({"basis": LIVE_DELAY_BASIS, "shrink": 0} if key.endswith("delay")
               else {"basis": "0px", "grow": 1, "shrink": 1})
        cells.append(label(key, text_expr="{view.params.%s}" % key, extra="sp-cell sp-ellipsis",
                           tip_expr="{view.params.tip}", pos=pos, style={"minWidth": "0"}))
    root = flex("root", "row", cells, align="center", classes="sp-tr",
                style={"gap": "10px", "height": "100%", "width": "100%", "padding": "0 4px"})
    params = dict((k, "") for k in LIVE_COLS)
    params.update({"name": "", "tip": ""})
    return view(root, params=params, size=(700, 20), pc=inputs(params))


# ------------------------------------------------------------------ Sparkplug

def lane(name):
    """The two directions between one pair of boxes, as two plain badges.

    This was a pair of animated bars: decoration that said the same thing the
    badge text now says, and cost a reader a legend to understand. The words
    and the colour both come from page_strip(), so the strip never disagrees
    with itself."""
    def badge(n, key):
        return label(n, extra_expr="%s + '/status/' + {view.custom.strip.%s.badge}" % (S, key),
                     text_expr="{view.custom.strip.%s.text}" % key,
                     tip_expr="{view.custom.strip.%s.why}" % key)
    return flex(name, "column", [badge("data", "dataLane"), badge("cmd", "cmdLane")],
                pos={"basis": "0px", "grow": 1, "shrink": 1}, justify="center", align="center",
                style={"gap": "6px", "padding": "0 10px", "minWidth": "72px", "overflow": "hidden"})


SCENARIOS = ["1 Live data", "2 Alarms", "3 Cloud to edge", "4 Outages", "5 UDT changes", "6 Notifications"]
PANELS = ["SparkplugLive", "SparkplugAlarms", "SparkplugCommands", "SparkplugOutage", "SparkplugUdt", "SparkplugNotify"]
# Selected and unselected share every text metric; only the fill changes, so the row never shifts.
SCENARIO_TEXT = {"fontSize": "13px", "fontWeight": "600", "letterSpacing": "0", "boxShadow": "none",
                 "fontFamily": "inherit"}


def strip_node(name, key, pos):
    """One box on the strip -- the shared StripNode, the same one the Store &
    Forward tab draws its sites and hub with."""
    return comp("ia.display.view", name,
                {"path": "StripNode", "useDefaultViewHeight": False, "useDefaultViewWidth": False,
                 "style": {"minWidth": "0", "width": "100%"}},
                pos, {"props.params": expr("{view.custom.strip.%s}" % key)})


CTL_VIEWS = ["SparkplugCtlLive", "SparkplugCtlAlarms", "SparkplugCtlCommands", "SparkplugCtlOutage",
             "SparkplugCtlUdt", "SparkplugCtlNotify"]


def build_main():
    """The MQTT tab: the shared rail on the left, the picture and the scenario
    on the right.

    THE RAIL'S HEIGHT IS A BUDGET, not a layout. At 1366x640 it has 534px inside
    its padding and must not scroll (a rail whose bottom half nobody finds), and
    this is the only tab whose rail carries a list AND the list's buttons:

        name 22 + banner 42 (stopped) + STATUS 18 + two facts 44 + CONTROLS 18
        + six scenarios 142 + the busiest scenario's buttons 150 + GATEWAYS 18
        + one row of links 26 + 8 gaps x 6                        = 528

    which is why the scenarios are 22px rows, a scenario's buttons go two to a
    line, and the MQTT rail has two facts where the others have three or four.
    Measured by the layout gate in both states; re-measure if any row changes.
    """
    node_skel = {"name": "", "state": "", "badge": "badge-neutral", "line": "", "tip": ""}
    strip_skel = {
        "n0": dict(node_skel, name=EDGE_TITLES[0]), "n1": dict(node_skel, name=EDGE_TITLES[1]),
        "brokerNode": dict(node_skel, name="MQTT broker"),
        "cloudNode": dict(node_skel, name="Cloud gateway (hub)"),
        "dataLane": {"text": "", "badge": "badge-neutral", "why": ""},
        "cmdLane": {"text": "", "badge": "badge-neutral", "why": ""},
        "notReady": "",
    }
    rail_skel = {"head": {"name": "", "state": "", "badge": "badge-neutral", "tip": ""},
                 "banner": {"text": ""}, "notReady": "", "state": "stopped", "facts": [], "links": []}

    def scenario(i, text):
        b = button("s%d" % (i + 1), text, "\tself.view.custom.scenario = %d\n" % i,
                   class_e="%s + if({view.custom.scenario} = %d, '/buttons/primary', '/buttons/chip')" % (S, i),
                   style=dict(SCENARIO_TEXT, height="%dpx" % SCENARIO_ROW, minHeight="%dpx" % SCENARIO_ROW,
                              fontSize="12px", padding="0 10px", width="100%"))
        # `justify` is a PROP on ia.input.button; as a style key it is ignored.
        b["props"]["justify"] = "flex-start"
        return b
    scenarios = flex("scenarios", "column", [scenario(i, t) for i, t in enumerate(SCENARIOS)],
                     style={"gap": "2px", "width": "100%"})

    ctl_case = ("case({view.custom.scenario}, " + ", ".join("%d, '%s'" % (i, p) for i, p in enumerate(CTL_VIEWS))
                + ", 'SparkplugCtlLive')")
    ctl = comp("ia.display.view", "ctl",
               {"path": "SparkplugCtlLive", "useDefaultViewHeight": False, "useDefaultViewWidth": False,
                "style": {"width": "100%"}},
               {"shrink": 0}, {"props.path": expr(ctl_case)})

    rail = comp("ia.container.flex", "sidebar", {
        "direction": "column", "alignItems": "stretch",
        "style": {"gap": "6px", "minWidth": "0", "padding": "14px", "width": "100%"}},
        {"basis": "%dpx" % RAIL_WIDTH, "shrink": 0},
        {"props.style.classes": expr("%s + '/containers/card'" % S)}, [
            embed("head", "RailHead", fixed=True, pc={"props.params": expr("{%s.head}" % RAIL)}),
            embed("banner", "NotRunning", pc={"props.params": expr("{%s.banner}" % RAIL)}),
            rail_group("gStatus", "STATUS"),
            rail_repeater("facts", "RailFact", "facts", "column", "22px"),
            rail_group("gControls", "CONTROLS"),
            scenarios,
            ctl,
            rail_group("gGateways", "GATEWAYS"),
            rail_repeater("links", "RailLink", "links", "row", "31%", wrap="wrap"),
        ])

    header = flex("header", "row", [
        label("what", BLURB, theme="text/muted", extra="sp-ellipsis", tip_expr="'%s'" % BLURB,
              pos={"basis": "0px", "grow": 1, "shrink": 1}, style={"minWidth": "0", "fontSize": "13px"}),
        button("traffic", "MQTT traffic",
               VIEWPORT +
               "\t# Top of the drawer = just below the scenario's one line, which stays visible.\n"
               "\ttry:\n"
               "\t\tsystem.perspective.alterDock('%s', {'size': max(240, h - %d)})\n" % (DOCK_ID, DRAWER_TOP) +
               "\texcept:\n"
               "\t\tpass\n"
               "\tsystem.perspective.toggleDock('%s')\n" % DOCK_ID),
    ], align="center", style={"gap": "8px"})

    strip = flex("strip", "row", [
        flex("edges", "column", [strip_node("edge3", "n0", {"basis": "0px", "grow": 1, "shrink": 1}),
                                 strip_node("edge4", "n1", {"basis": "0px", "grow": 1, "shrink": 1})],
             pos={"basis": "29%", "shrink": 0}, style={"gap": "6px", "minWidth": "0"}),
        lane("laneIn"),
        flex("broker", "column", [strip_node("box", "brokerNode", {"basis": "52px", "shrink": 0})],
             pos={"basis": "15%", "shrink": 0}, justify="center", style={"minWidth": "0"}),
        lane("laneOut"),
        strip_node("cloud", "cloudNode", {"basis": "23%", "shrink": 0}),
    ], pos={"basis": "96px", "shrink": 0}, align="stretch", style={"gap": "0px", "minWidth": "0"})

    case = "case({view.custom.scenario}, " + ", ".join("%d, '%s'" % (i, p) for i, p in enumerate(PANELS)) + ", 'SparkplugLive')"
    panel = comp("ia.display.view", "panel", {"path": "SparkplugLive", "style": {"minHeight": "0", "width": "100%"}},
                 {"basis": "0px", "grow": 1, "shrink": 1}, {"props.path": expr(case)})

    # `sparkplug-scope` on the CONTENT column, not the root: the scope's rules
    # restyle every status badge inside it (13px, no capitals), and on the root
    # they reached the shared rail and made this tab's rail the one that looked
    # different from the other three.
    main = flex("main", "column", [header, strip, panel],
                pos={"basis": "0px", "grow": 1, "shrink": 1},
                style={"gap": "8px", "minHeight": "0", "minWidth": "0", "overflow": "hidden"},
                classes="sparkplug-scope")

    # A FIXED-HEIGHT page, unlike the other three: every whole-row list below is
    # sized from the viewport (grow_list), which only works if nothing scrolls.
    root = flex("root", "row", [rail, main], pos={"basis": "auto", "shrink": 0},
                style={"height": "100%", "width": "100%", "overflow": "hidden", "padding": "16px",
                       "gap": "16px", "boxSizing": "border-box"},
                classes_expr="%s + '/containers/content'" % S)
    root["props"]["alignItems"] = "stretch"
    return view(root, custom={"scenario": 0, "strip": strip_skel, "rail": rail_skel}, size=(1366, 594),
                pc={"custom.strip": data_binding("page_strip()", 2000),
                    "custom.rail": {"binding": {"type": "expr", "config": {"expression": "now(3000)"},
                                                "transforms": [{"type": "script", "code":
                                                                "\timport demo_rail\n"
                                                                "\treturn demo_rail.rail('sparkplug')\n"}]}}})


# ------------------------------------------------------------------ row views

def build_alarm_row():
    def end(key):
        # `sp-stale` rides the classes binding, never a props.style binding: a
        # style binding replaces the whole style object and drops the static
        # keys below (the Launchpad trap).
        stale = " + if({view.params.cloudStale}, ' sp-stale', '')" if key == "cloud" else ""
        return label(key, extra_expr="%s + '/status/' + {view.params.%sBadge}%s" % (S, key, stale),
                     text_expr="{view.params.%sText}" % key, tip_expr="{view.params.tip}",
                     pos={"basis": "0px", "grow": 1, "shrink": 1}, style={"minWidth": "0", "justifyContent": "flex-start"})
    root = flex("root", "row", [
        label("name", text_expr="{view.params.name}", tip_expr="{view.params.tip}", pos={"basis": "18%", "shrink": 0},
              extra="sp-cell sp-strong"),
        flex("edgeCell", "row", [end("edge")], pos={"basis": "0px", "grow": 1, "shrink": 1}, align="center",
             style={"minWidth": "0", "overflow": "hidden"}),
        flex("cloudCell", "row", [end("cloud")], pos={"basis": "0px", "grow": 1, "shrink": 1}, align="center",
             style={"minWidth": "0", "overflow": "hidden"}),
    ], align="center", classes="sp-tr", style={"gap": "10px", "height": "100%", "width": "100%", "padding": "0 4px"})
    params = {"name": "", "priority": "", "edgeText": "", "edgeBadge": "badge-neutral",
              "cloudText": "", "cloudBadge": "badge-neutral", "cloudStale": False, "tip": ""}
    return view(root, params=params, size=(600, ALARM_ROW_H), pc=inputs(params))


def thead(cols):
    kids = []
    for i, (text, pos) in enumerate(cols):
        kids.append(label("h%d" % i, text, extra="sp-th", pos=pos))
    return flex("thead", "row", kids, align="center", style={"gap": "10px", "padding": "0 4px"})


def repeater(name, path, data_expr, row_h):
    return comp("ia.display.flex-repeater", name, {
        "path": path, "direction": "column", "instances": [],
        "useDefaultViewHeight": True, "useDefaultViewWidth": False,
        "elementPosition": {"basis": "%dpx" % row_h, "grow": 0, "shrink": 0},
        "style": {"width": "100%", "gap": "0px"},
    }, {"shrink": 0}, {"props.instances": expr(data_expr)})


def trend_component(name="trend", fn="page_trend()", names=("Edge 3", "Edge 4")):
    t = json.loads(json.dumps(TREND))
    t['meta']['name'] = name
    t['position'] = {"basis": "0px", "grow": 1, "shrink": 1}
    t['props']['style'] = {"minHeight": "0", "width": "100%"}
    t['props']['legend']['position'] = 'right'
    t['props']['series'][0]['name'] = names[0]
    t['props']['series'][1]['name'] = names[1]
    # Two edges at the same value draw one line over the other: the second is dashed,
    # never offset, so both stay visible without moving either off its true value.
    t['props']['series'][1]['line']['appearance']['stroke']['dashArray'] = "7 5"
    t['props']['legend']['labels']['font']['size'] = 12
    for ax in t['props']['xAxes'] + t['props']['yAxes']:
        ax['appearance']['font']['size'] = '12px'
    t['props']['yAxes'][0]['value']['range'] = {"min": 0, "max": 100, "useStrict": True}
    # One tick rule for every chart: minutes only, labels at least 230px apart. Every
    # chart is the same width at a given window size, and at 653-1086px that is a
    # 5-minute tick at all three sizes.
    t['props']['xAxes'][0]['date']['format'] = 'HH:mm'
    t['props']['xAxes'][0]['appearance']['grid']['minDistance'] = 230
    t['props']['xAxes'][0]['date']['baseInterval'] = {"enabled": True, "count": 5, "timeUnit": "second",
                                                       "skipEmptyPeriods": False}
    t['propConfig']['props.dataSources'] = data_binding(fn, 5000)
    return t


# ------------------------------------------------------------------ 1 Live data

def build_live():
    """One table, both ends of both edges, and the delay in its own column.

    The delay used to ride in a list HEADER above a second list of the same
    updates; both are gone. The chart that was here is the same chart as
    scenario 4's, where the gap filling in is the point."""
    grow = {"basis": "0px", "grow": 1, "shrink": 1}
    delay_pos = {"basis": LIVE_DELAY_BASIS, "shrink": 0}

    def head(name, text):
        return label(name, text, extra="sp-th-plain sp-ellipsis", tip_expr="'%s'" % text, pos=dict(grow),
                     style={"minWidth": "0"})

    def delay_head(name):
        return label(name, "Delay", extra="sp-th-plain",
                     tip_expr="'How far behind the edge the cloud copy is, measured by MQTT Engine'",
                     pos=dict(delay_pos))
    rows = repeater("rows", "SparkplugLiveRow", "{view.custom.d.tags}", 20)
    # Each edge title sits exactly over its own three columns at every width:
    # the two rows share the 160px tag column and the 10px gap, and an edge's
    # span works out at (row width - 180) / 2 in both -- so basis 0, grow 1.
    table_card = card("values", [
        flex("edges", "row", [
            label("spacer", "", pos={"basis": "160px", "shrink": 0}),
            label("e3", "Edge 3", theme="text/card-title", pos=dict(grow),
                  style={"fontSize": "14px", "minWidth": "0"}),
            label("e4", "Edge 4", theme="text/card-title", pos=dict(grow),
                  style={"fontSize": "14px", "minWidth": "0"}),
        ], align="center", style={"gap": "10px", "padding": "0 4px"}),
        flex("thead", "row", [
            label("tag", "Tag", extra="sp-th-plain", pos={"basis": "160px", "shrink": 0}),
            head("h1", "At the edge"), head("h2", "In the cloud"), delay_head("h3"),
            head("h4", "At the edge"), head("h5", "In the cloud"), delay_head("h6"),
        ], align="center", style={"gap": "10px", "padding": "0 4px"}),
        rows,
    # Eight tags, fixed rows: the card hugs them. Growing it to the panel's
    # full height only draws a bigger empty box under the last row.
    ], pos={"basis": "auto", "grow": 0, "shrink": 0}, gap="0px",
        style={"padding": "6px 14px"})
    root = scenario_root([
        try_line("Compare each tag at the edge and in the cloud: the cloud copy follows within about a second."),
        table_card,
    ])
    return view(root, custom={"d": {"tags": [], "ready": False}},
                pc={"custom.d": data_binding("page_live()")})


# ------------------------------------------------------------------ 2 Alarms

def build_alarms():
    def block(i):
        # The buttons that were on this row are in the rail now (SparkplugCtlAlarms),
        # under the scenario list; the edge's name keeps the row.
        #
        # `asOf` shares that row rather than taking one of its own: it is empty
        # whenever the edge is live, and a row of its own would move every list
        # below it the moment an edge went quiet -- on the one scenario whose
        # bottom list is already sized to the window.
        return card("edge%d" % (i + 3), [
            flex("actions", "row", [
                label("title", EDGE_TITLES[i], theme="text/card-title", style={"marginRight": "4px"}),
                label("asOf", theme="text/muted", extra="sp-ellipsis",
                      text_expr="{view.custom.d.asOf%d.note}" % i,
                      tip_expr="{view.custom.d.asOf%d.note}" % i,
                      pos={"basis": "0px", "grow": 1, "shrink": 1},
                      style={"minWidth": "0", "fontSize": "12px"}),
            ], align="center", style={"gap": "8px", "height": "24px"}),
            thead([("Alarm", {"basis": "18%", "shrink": 0}), ("At the edge", {"basis": "0px", "grow": 1, "shrink": 1}),
                   ("In the cloud", {"basis": "0px", "grow": 1, "shrink": 1})]),
            repeater("rows", "SparkplugAlarmRow", "{view.custom.d.e%d}" % i, ALARM_ROW_H),
        ], pos={"basis": "0px", "grow": 1, "shrink": 1}, gap="6px", style={"padding": "8px 14px"})

    # One list, both edges, newest first: the evidence for "every firing is its
    # own event". Two lists said it twice and halved the rows each could show.
    recent = grow_list(table("recent", "{view.custom.d.recent}",
                             [("ts", "Time", 130, True), ("who", "Edge", 110, True),
                              ("what", "What happened", 1, False)],
                             row_h=ALARM_ROW_H, empty="Nothing yet"),
                       ALARM_ROW_H, ALARM_LIST_OFF, "{view.custom.d.recentCount}")
    root = scenario_root([
        try_line("Trip a fault on one edge: it goes active at both ends. Acknowledge it at either end."),
        flex("blocks", "row", [block(0), block(1)], align="flex-start", style={"gap": "12px"}),
        card("recentCard", [recent], pos={"basis": "auto", "grow": 0, "shrink": 0}, gap="4px",
             style={"padding": "8px 14px", "minHeight": "0"}),
    ])
    blank_as_of = {"current": True, "asOf": "", "tail": "", "note": ""}
    return view(root, custom={"d": {"e0": [], "e1": [], "recent": [], "recentCount": 0, "ready": False,
                                    "asOf0": dict(blank_as_of), "asOf1": dict(blank_as_of)}},
                pc={"custom.d": data_binding("page_alarms()")})


# ------------------------------------------------------------------ the rail's controls
#
# Each scenario's buttons, as one small view per scenario that the main view's
# rail embeds directly under the scenario list (by the same case() on
# view.custom.scenario the panel uses). Only the selected one is mounted, so
# only it polls -- the same reason the panels are separate views.
#
# Every button is the shared RailButton, fired through demo_rail.ACTIONS, and
# every one of them is guarded by sparkplug_demo.ctl(<scenario>)'s `can`.
# QUIET: a scenario's buttons go two to a line, and a refusal wrapping under a
# 114px button would grow the rail past a 640px laptop. The answer to the last
# press is ONE line at the foot of the block instead, filtered to this
# scenario's own keys so it never shows another scenario's answer.

def ctl_note(prefix):
    key = "{session.custom.actKey}"
    acting = "{session.custom.acting}"
    text = ("if(indexOf(%s, '%s') = 0, {session.custom.actingWhat}, "
            "if(indexOf(%s, '%s') = 0, {session.custom.actNote}, ''))" % (acting, prefix, key, prefix))
    shown = "{view.custom.c.ready} && %s != ''" % text
    return comp("ia.display.label", "note",
                {"text": "", "style": {"fontSize": "10px", "lineHeight": 1.35, "minWidth": "0",
                                       "overflow": "hidden", "textOverflow": "ellipsis", "whiteSpace": "nowrap"},
                 "tooltip": {"enabled": True}},
                {"shrink": 0},
                {"props.text": expr(text), "props.tooltip.text": expr(text),
                 "props.style.classes": expr("%s + '/text/muted'" % S),
                 "position.display": expr(shown)})


def ctl_pair(name, left, right):
    half = {"basis": "0px", "grow": 1, "shrink": 1}
    left["position"] = dict(half)
    right["position"] = dict(half)
    return flex(name, "row", [left, right], style={"gap": "4px", "width": "100%"})


def ctl_heads():
    """"Edge 3 | Edge 4" over a block of pairs, so each button's label can be a
    verb alone and still say which edge it acts on."""
    def h(n, t):
        return label(n, t, pos={"basis": "0px", "grow": 1, "shrink": 1},
                     style={"fontSize": "10px", "fontWeight": "600", "letterSpacing": "0.06em",
                            "opacity": "0.7", "textAlign": "center", "justifyContent": "center"})
    return flex("heads", "row", [h("h3", "EDGE 3"), h("h4", "EDGE 4")],
                style={"gap": "4px", "width": "100%", "height": "14px"})


def ctl_view(name, children, fn, prefix):
    root = flex("root", "column", children + [ctl_note(prefix)],
                style={"gap": "4px", "width": "100%", "paddingTop": "2px"})
    return view(root, custom={"c": {"ready": False, "can": {}}}, size=(232, 60),
                pc={"custom.c": data_binding("ctl('%s')" % fn, 2000)})


def can_of(key):
    return "{view.custom.c.can.%s}" % key


def build_ctl_live():
    # Nothing to press on this scenario: the block is empty and takes no room.
    return view(flex("root", "column", [], style={"gap": "0px", "width": "100%"}),
                custom={}, size=(232, 1))


def build_ctl_alarms():
    def pair(name, verb_key, icon):
        return ctl_pair(name,
                        rail_button(name + "0", "mqtt.alarm.%s0" % verb_key, can_of(verb_key + "0"), icon, quiet=True),
                        rail_button(name + "1", "mqtt.alarm.%s1" % verb_key, can_of(verb_key + "1"), icon, quiet=True))
    return ctl_view("SparkplugCtlAlarms", [
        ctl_heads(),
        pair("trip", "trip", "material/warning"),
        pair("reset", "reset", "material/replay"),
        pair("ackEdge", "ackEdge", "material/done"),
        pair("ackCloud", "ackCloud", "material/done_all"),
    ], "alarms", "mqtt.alarm.")




# ------------------------------------------------------------------ 3 Cloud to edge

def build_commands():
    def value(name, expression):
        return label(name, text_expr=expression, extra="sp-value sp-ellipsis", tip_expr=expression,
                     pos={"basis": "0px", "grow": 1, "shrink": 1}, style={"minWidth": "0"})

    def block(i):
        d = "{view.custom.d.e%d." % i
        # The setpoint entry, Send, AUTO and MANUAL are in the rail now
        # (SparkplugCtlCommands); this card is what they did.
        ctl = {"basis": "150px", "shrink": 0}

        def caption(name, text):
            return label(name, text, extra="sp-th sp-ellipsis", tip_expr="'%s'" % text,
                         pos={"basis": "0px", "grow": 1, "shrink": 1}, style={"minWidth": "0"})
        return card("edge%d" % (i + 3), [
            flex("head", "row", [
                label("title", EDGE_TITLES[i], theme="text/card-title", pos=ctl),
                caption("asked", "Last sent"), caption("cloud", "In the cloud"), caption("edge", "At the edge"),
            ], align="center", style={"gap": "10px"}),
            flex("spRow", "row", [
                label("caption", "Level setpoint", pos=ctl, extra="sp-cell"),
                value("asked", d + "askedSp}"),
                value("cloud", d + "spCloud}"),
                value("edge", d + "spEdge}"),
            ], align="center", style={"gap": "10px", "height": "30px"}),
            flex("modeRow", "row", [
                label("caption", "Mode", pos=ctl, extra="sp-cell"),
                value("asked", d + "askedMode}"),
                value("cloud", d + "modeCloud}"),
                value("edge", d + "modeEdge}"),
            ], align="center", style={"gap": "10px", "height": "30px"}),
        ], gap="6px", style={"padding": "8px 14px"})

    dash = "—"
    skel = {"spEdge": dash, "spCloud": dash, "modeEdge": dash, "modeCloud": dash, "askedSp": dash, "askedMode": dash}
    # No chart: two setpoint lines that match draw on top of each other, so it
    # needed a caption apologising for looking like one line. No command list
    # either -- the MQTT traffic drawer's Commands column is the same rows.
    root = scenario_root([
        try_line("Send a setpoint or mode from the rail; the edge follows about a second later."),
        block(0), block(1),
    ])
    custom = {"d": {"e0": dict(skel), "e1": dict(skel), "ready": False}}
    return view(root, custom=custom, pc={"custom.d": data_binding("page_commands()")})


def build_ctl_commands():
    """The setpoint lives on the SESSION (custom.mqttSetpoint), not beside the
    button: a RailButton's handler is demo_rail.fire(), which can read the
    session but cannot reach into a sibling component."""
    entry = comp("ia.input.numeric-entry-field", "setpoint",
                 {"value": 50, "style": {"height": "28px", "fontSize": "12px"}},
                 {"basis": "0px", "grow": 1, "shrink": 1},
                 {"props.value": {"binding": {"type": "property",
                                              "config": {"path": "session.custom.mqttSetpoint",
                                                         "bidirectional": True}}}})
    sp_row = flex("spRow", "row", [
        label("caption", "Setpoint", pos={"shrink": 0},
              extra_expr="%s + '/text/muted'" % S, style={"fontSize": "11px"}),
        entry,
    ], align="center", style={"gap": "8px", "width": "100%"})

    def pair(name, verb, icon):
        return ctl_pair(name,
                        rail_button(name + "0", "mqtt.cmd.%s0" % verb, can_of(verb + "0"), icon, quiet=True),
                        rail_button(name + "1", "mqtt.cmd.%s1" % verb, can_of(verb + "1"), icon, quiet=True))
    return ctl_view("SparkplugCtlCommands", [
        sp_row,
        ctl_heads(),
        pair("send", "send", "material/send"),
        pair("auto", "auto", "material/autorenew"),
        pair("manual", "manual", "material/pan_tool"),
    ], "commands", "mqtt.cmd.")


# ------------------------------------------------------------------ 4 Outages

def status_fact(name, caption, key):
    return flex(name, "row", [
        # A fixed 13px: the column is 140px, and a caption that scaled with the window ran under its badge.
        label("caption", caption, pos={"basis": "140px", "shrink": 0}, style={"fontSize": "13px"}),
        label("value", extra_expr="%s + '/status/' + {view.custom.d.%s.badge}" % (S, key),
              text_expr="{view.custom.d.%s.text}" % key),
    ], align="center", classes="sp-tr", style={"gap": "10px", "height": "30px"})


# Measured 22/09/2026 (SPARKPLUG.md, Three field questions): the cloud marked a
# cut Edge 3 Offline 14.8-15.7 s in; a Rebirth left 2.4-3.0 s with nothing from
# it, and 0 of 50 heartbeats were lost.
OUTAGE_LINE = "Cut Edge 3: the cloud says Offline ~15 s later; Restore fills the gap. Rebirth: ~3 s gap, 0 values lost."


def build_outage():
    # Cut and Restore are in the rail (SparkplugCtlOutage), with the countdown
    # on the button. The link's own state is now a fourth status row, not a
    # sentence, and the "lesson" paragraph went: the one-line instruction above
    # says what the three rows are about to disagree over.
    link = card("link", [
        label("title", "Edge 3 link", theme="text/card-title"),
        status_fact("cutState", "Link", "cut"),
        status_fact("engine", "Cloud (MQTT Engine)", "engine"),
        status_fact("wire", "Broker messages", "wire"),
        status_fact("edge", "Edge 3 itself", "edge"),
    ], gap="4px", style={"padding": "8px 14px"})
    history_list = grow_list(table("rows", "{view.custom.d.history}",
                                   [("ts", "Time", 116, True), ("what", "What happened", 1, False)],
                                   row_h=22, header=False, empty="Nothing yet"),
                             22, OUTAGE_LIST_OFF, "{view.custom.d.historyCount}", header=False)
    history = card("history", [
        label("historyTitle", "Link history", extra="sp-th"),
        history_list,
    ], gap="4px", style={"padding": "8px 14px"})
    # The alarms under the chart, not in the left column: at 1366x640 the left
    # column has no room for four more rows and still three of history.
    alarm_rows = table("alarms", "{view.custom.d.alarms}",
                       [("alarm", "Alarm", 100, True), ("edge", "At the edge", 1, False),
                        ("cloud", "In the cloud", 1, False), ("quality", "Tag quality", 104, True)],
                       pos={"basis": "%dpx" % (32 + 4 * 22 + 2), "grow": 0, "shrink": 0},
                       row_h=22, empty="Nothing yet")
    # The title carries the "as at" marking when Edge 3 has gone quiet -- the
    # same words scenario 2 puts beside the edge's name, and no extra line.
    alarms = card("alarmsCard", [label("alarmsTitle", "Alarms during the outage", extra="sp-th sp-ellipsis",
                                       text_expr="{view.custom.d.alarmsTitle}",
                                       tip_expr="{view.custom.d.alarmsTitle}",
                                       style={"minWidth": "0"}), alarm_rows],
                  pos={"basis": "auto", "grow": 0, "shrink": 0}, gap="4px", style={"padding": "8px 14px"})
    chart = flex("right", "column", [chart_card("Tank level in the cloud, last 10 minutes", trend_component()), alarms],
                 pos={"basis": "0px", "grow": 1, "shrink": 1}, style={"gap": "8px", "minWidth": "0", "minHeight": "0"})
    root = scenario_root([
        try_line(OUTAGE_LINE),
        side_by_side([link, history], chart, LEFT_BASIS, LEFT_MAX),
    ])
    blank = {"text": "", "badge": "badge-neutral"}
    custom = {"d": {"cut": dict(blank), "engine": dict(blank), "wire": dict(blank), "edge": dict(blank),
                    "history": [], "historyCount": 0, "alarms": [],
                    "alarmsTitle": "Alarms during the outage"}}
    return view(root, custom=custom, pc={"custom.d": data_binding("page_outage()")})


def build_ctl_outage():
    return ctl_view("SparkplugCtlOutage", [
        rail_button("cut", "mqtt.out.cut", can_of("cut"), "material/link_off", quiet=True),
        rail_button("restore", "mqtt.out.restore", can_of("restore"), "material/link", "primary", quiet=True),
        rail_button("rebirth", "mqtt.out.rebirth", can_of("rebirth"), "material/refresh", quiet=True),
    ], "outage", "mqtt.out.")


# ------------------------------------------------------------------ 5 UDT changes

def build_udt():
    def members(key):
        cols = [("member", "Tag", 3, False), ("note", "Held in", 4, False)] if key == "cloud" else \
            [("member", "Tag", 5, False), ("type", "Type", 2, False), ("note", "Notes", 3, False)]
        t = table("members", "{view.custom.d.%s.members}" % key, cols, row_h=20, empty="Nothing yet",
                  header=False, pos={"basis": "182px", "grow": 1, "shrink": 0})
        # At least its rows; on a taller window the list takes the height, rows packed at the top.
        t["propConfig"]["position.basis"] = expr("{view.custom.d.%s.height}" % key)
        return t

    def col(name, title, key):
        return card(name, [
            flex("head", "row", [
                label("title", title, theme="text/card-title", pos={"shrink": 0}),
                label("sub", theme="text/muted", extra="sp-ellipsis", text_expr="{view.custom.d.%s.sub}" % key,
                      tip_expr="{view.custom.d.%s.sub}" % key, pos={"basis": "0px", "grow": 1, "shrink": 1},
                      style={"minWidth": "0"}),
            ], align="baseline", style={"gap": "10px"}),
            members(key),
        ], pos={"basis": "0px", "grow": 1, "shrink": 1}, gap="4px", style={"padding": "8px 14px"})

    cols = flex("cols", "row", [
        col("edge3", "Edge 3", "e0"),
        col("edge4", "Edge 4", "e1"),
        col("cloud", "The cloud's templates", "cloud"),
    ], pos={"basis": "0px", "grow": 1, "shrink": 1}, align="stretch", style={"gap": "12px", "minHeight": "0"})

    # The drift check, as a table and not a chart: two rows, fixed, under the
    # three columns. It is the same sparkplug_demo.udt_drift() that
    # `make mqtt-udt-check` prints, so the page and the command can never
    # disagree, and it shows dashes while the demo is stopped.
    drift = table("drift", "{view.custom.d.drift}",
                  [("edge", "Edge", 100, True), ("drift", "Drift check", 1, False)],
                  pos={"basis": "%dpx" % (32 + 2 * 22 + 2), "grow": 0, "shrink": 0},
                  row_h=22, empty="Nothing yet")
    drift_card = card("driftCard", [drift], pos={"basis": "auto", "grow": 0, "shrink": 0}, gap="4px",
                      style={"padding": "8px 14px", "minHeight": "0"})

    # The problem / the fix / start again strip is the rail now
    # (SparkplugCtlUdt). The headline stays: it is the one sentence that says
    # which of those three states the templates are in.
    root = scenario_root([
        try_line("Diverge Edge 4: the cloud keeps the first layout it saw; the drift check names it, v2 fixes it."),
        flex("banner", "row", [
            label("headline", text_expr="{view.custom.d.headline}", wrap=True, style={"fontSize": "13px"},
                  pos={"basis": "0px", "grow": 1, "shrink": 1}),
        ], align="center", classes_expr="{view.custom.d.banner}", style={"gap": "12px"}),
        cols,
        drift_card,
    ])
    blank = {"sub": "", "members": [], "height": "182px"}
    custom = {"d": {"headline": "", "banner": "sp-banner sp-banner--neutral", "ready": False,
                    "e0": dict(blank), "e1": dict(blank), "cloud": dict(blank), "drift": []}}
    return view(root, custom=custom, pc={"custom.d": data_binding("page_udt()", 3000)})


def build_ctl_udt():
    """Three pairs: the problem (Edge 4's layout), the fix (a v2 per edge),
    and the way out. "Roll out to every edge" stays a make target
    (`make mqtt-udt-rollout`): it blanks both edges for five seconds and
    answers an engineer's question, not a customer's."""
    def b(name, verb, icon, kind="chip"):
        return rail_button(name, "mqtt.udt.%s" % verb, can_of(verb), icon, kind, quiet=True)
    return ctl_view("SparkplugCtlUdt", [
        ctl_pair("problem", b("diverge", "diverge", "material/call_split"),
                 b("putback", "putback", "material/undo")),
        ctl_pair("fix", b("v2edge3", "v2edge3", "material/upgrade"),
                 b("v2edge4", "v2edge4", "material/upgrade")),
        ctl_pair("end", b("retire", "retire", "material/delete_outline"),
                 b("reset", "reset", "material/replay")),
    ], "udt", "mqtt.udt.")


# ------------------------------------------------------------------ 6 Notifications

def build_notify():
    def way(name, title, tip, a, b):
        def latest(n, who, key):
            return flex(n, "row", [
                label("who", who, pos={"basis": "120px", "shrink": 0}, extra="sp-cell"),
                label("what", text_expr="{view.custom.d.%s}" % key, tip_expr="{view.custom.d.%s}" % key,
                      extra="sp-ellipsis sp-cell", pos={"basis": "0px", "grow": 1, "shrink": 1}, style={"minWidth": "0"}),
            ], align="center", style={"gap": "12px"})
        # The sentence explaining each way is the title's tooltip now: the
        # titles already say the difference, and two paragraphs side by side
        # was a third and fourth sentence on a page allowed one.
        return card(name, [
            label("title", title, theme="text/card-title", tip_expr="'%s'" % tip),
            latest("latest3", "Edge 3", a),
            latest("latest4", "Edge 4", b),
        ], pos={"basis": "0px", "grow": 1, "shrink": 1})

    ways = flex("ways", "row", [
        way("sparkplug", "Way 1: inside Sparkplug, as a tag",
            "About a second behind, and stored and resent if the link drops.", "spk0", "spk1"),
        way("raw", "Way 2: a plain MQTT message",
            "Milliseconds, but outside Sparkplug: nothing stores it during an outage.", "raw0", "raw1"),
    ], align="stretch", style={"gap": "12px"})

    rows_table = grow_list(table("rows", "{view.custom.d.log}",
                                 [("ts", "Time", 130, True), ("who", "Edge", 140, True), ("what", "Alarm", 1, False)],
                                 row_h=LOG_RH, empty="Nothing yet"),
                           LOG_RH, LOG_OFF, "{view.custom.d.logCount}")
    log = card("log", [
        flex("head", "row", [
            label("title", "The cloud's own notification log", theme="text/card-title",
                  tip_expr="'Fills only when the cloud\\'s own Pump Fault alarm fires. An alarm that "
                           "arrives over Sparkplug never runs a cloud notification.'"),
        ], align="center", style={"gap": "12px", "height": "30px"}),
        rows_table,
    ], style={"minHeight": "0"})

    root = scenario_root([
        try_line("Trip a fault from the rail: the alarm reaches the cloud two ways, and the cloud logs its own."),
        ways,
        log,
    ])
    custom = {"d": {"spk0": "", "spk1": "", "raw0": "", "raw1": "", "log": [], "logCount": 0}}
    return view(root, custom=custom, pc={"custom.d": data_binding("page_notify()", 3000)})


def build_ctl_notify():
    return ctl_view("SparkplugCtlNotify", [
        ctl_heads(),
        ctl_pair("trip",
                 rail_button("trip0", "mqtt.note.trip0", can_of("trip0"), "material/warning", quiet=True),
                 rail_button("trip1", "mqtt.note.trip1", can_of("trip1"), "material/warning", quiet=True)),
    ], "notify", "mqtt.note.")


# ------------------------------------------------------------------ popups + drawer

def build_traffic():
    events = table("events", "{view.custom.d.events}", [
        ("when", "When", 90, True), ("kind", "Kind", 170, True), ("dir", "Direction", 120, True),
        ("what", "What happened", 1, False), ("topic", "Topic", 280, True)],
        row_h=28, empty="No messages yet", pos={"basis": "200px", "grow": 0, "shrink": 0})
    # Whole rows only, in whatever height the drawer has.
    events["propConfig"]["position.basis"] = expr(
        "toStr(32 + 28 * max(3, floor((%s - %d - %d - 32) / 28))) + 'px'" % (VH, DRAWER_TOP, DRAWER_FIXED))
    root = flex("root", "column", [
        flex("head", "row", [
            label("title", "MQTT traffic", theme="text/card-title"),
            label("what", "A live copy of the messages crossing the broker, for viewing only. Repeats are "
                          "folded into one row with a count.",
                  theme="text/muted", pos={"basis": "0px", "grow": 1, "shrink": 1}, extra="sp-ellipsis",
                  style={"minWidth": "0"}),
            button("close", "Close", "\tsystem.perspective.closeDock('%s')\n" % DOCK_ID),
        ], align="center", style={"gap": "12px", "height": "30px"}),
        # Five tiles, not eight. Deaths, cloud STATE and the plain-MQTT count
        # are answers to questions a viewer has not asked; every one of them is
        # still a row in the list below, where it has its own words.
        flex("stats", "row", [
            stat("rate0", "Edge 3", "rate0"),
            stat("rate1", "Edge 4", "rate1"),
            stat("data", "Value updates (data)", "data"),
            stat("cmd", "Commands (cmd)", "cmd"),
            stat("life", "Startups and shutdowns", "life"),
        ], align="center", style={"height": "40px", "overflow": "hidden"}),
        events,
    ], classes_expr="%s + '/containers/content' + ' sparkplug-scope sp-drawer'" % S,
        style={"gap": "8px", "padding": "10px 16px 14px", "height": "100%", "width": "100%", "boxSizing": "border-box",
               "overflow": "hidden"})
    skel = {"data": "", "cmd": "", "life": "", "rate0": "", "rate1": ""}
    return view(root, custom={"d": {"rate": "", "stats": skel, "events": []}}, size=(1366, 350),
                pc={"custom.d": data_binding("page_traffic()", 1500)})


# ------------------------------------------------------------------ side files

def page_config():
    p = os.path.join(PERSP, 'page-config', 'config.json')
    cfg = json.load(open(p))
    page = cfg['pages']['/']
    docks = page.setdefault('docks', {})
    bottom = [d for d in docks.get('bottom', []) if d.get('id') != DOCK_ID]
    bottom.append({"id": DOCK_ID, "viewPath": "SparkplugTraffic", "size": 400, "show": "onDemand",
                   "content": "cover", "handle": "hide", "anchor": "fixed", "modal": False,
                   "resizable": False, "iconUrl": "", "autoBreakpoint": 480, "viewParams": {}})
    docks['bottom'] = bottom
    with open(p, 'w') as fh:
        json.dump(cfg, fh, indent=2)
        fh.write('\n')


CSS_MARK = "/* ---- The Sparkplug tab's guided scenarios"
CSS_END = "/* A label's root is display:flex"
CSS = r"""/* ---- The Sparkplug tab's guided scenarios (generated by scripts/gen-sparkplug-views.py) ----
 * Scoped to .psc-sparkplug-scope, which the tab's root, its popups and its
 * drawer carry: this stylesheet serves every tab. Colours come from theme
 * variables so the page follows the session theme. Selectors are doubled up
 * with the scope because a theme class on the same element is otherwise an
 * equal-specificity race decided by load order.
 */
.psc-sparkplug-scope *::-webkit-scrollbar { width: 10px; height: 10px; }
.psc-sparkplug-scope *::-webkit-scrollbar-track { background: rgba(255, 255, 255, 0.05); }
.psc-sparkplug-scope *::-webkit-scrollbar-thumb {
  background: var(--callToAction); border-radius: 5px;
  border: 2px solid transparent; background-clip: padding-box;
}
.psc-sparkplug-scope .psc-sp-card {
  background-color: var(--container);
  border: 1px solid var(--containerBorder);
  border-radius: 6px;
  padding: 10px 14px;
  box-sizing: border-box;
}
.psc-sparkplug-scope .psc-sp-try {
  background-color: var(--containerNested);
  border-left: 3px solid var(--callToAction);
  border-radius: 4px;
  padding: 5px 12px;
  font-size: 13px;
}
.psc-sparkplug-scope .psc-sp-key { font-weight: 700; color: var(--callToAction); font-size: 13px; }
.psc-sparkplug-scope .psc-sp-th {
  font-size: 12px; font-weight: 600; letter-spacing: .04em;
  text-transform: uppercase; color: var(--label--disabled);
}
.psc-sparkplug-scope .psc-sp-cell { font-size: 13px; }
.psc-sparkplug-scope .psc-sp-value { font-size: 14px; }
.psc-sparkplug-scope .psc-sp-th-plain { font-size: 12px; font-weight: 600; color: var(--label--disabled); }
.psc-sparkplug-scope .psc-sp-tr-soft { border-bottom: 1px dotted var(--containerBorder); box-sizing: border-box; }
.psc-sparkplug-scope .psc-sp-strong { font-weight: 600; }
.psc-sparkplug-scope .psc-sp-value { font-weight: 600; color: var(--label); }
.psc-sparkplug-scope .psc-sp-tr { border-bottom: 1px solid var(--containerBorder); box-sizing: border-box; }
.psc-sparkplug-scope .psc-sp-tr--active { background-color: rgba(245, 158, 11, 0.10); }
.psc-sparkplug-scope .psc-sp-alarm-on { color: #f87171; font-weight: 600; }
/* A cloud cell whose edge is offline: the last state that crossed the broker,
 * not the state now. Dimmed and grey on top of badge-neutral, which a live
 * "Cleared" also wears, so the whole block changes and not just its actives. */
.psc-sparkplug-scope .psc-sp-stale { opacity: .45; filter: grayscale(1); }
/* Table-row pills pad less than the theme's 12px: an alarm cell is 167px wide
 * at 1366x640 and "Cleared, unacknowledged (2)" has to fit it whole. */
.psc-sparkplug-scope .psc-sp-tr [class*="/status/badge-"] { padding-left: 8px !important; padding-right: 8px !important; }
.psc-sparkplug-scope .psc-sp-ellipsis > span {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* The demo is not running. Warm, not alarming: nothing is broken, it has
 * simply not been started -- and the sentence says where to start it. Colour
 * and weight only: this replaces the blurb on the header's second line, so a
 * box or padding here would move the whole page down. */
.psc-sparkplug-scope .psc-sp-notready,
.psc-sparkplug-scope .psc-sp-notready > span {
  color: #f59e0b !important;
  font-weight: 600;
}
.psc-sparkplug-scope .psc-sp-banner {
  border-left: 4px solid var(--containerBorder);
  background-color: var(--containerNested);
  border-radius: 4px;
  padding: 6px 12px;
}
.psc-sparkplug-scope .psc-sp-banner--good { border-left-color: #35d39a; }
.psc-sparkplug-scope .psc-sp-banner--warn { border-left-color: #f59e0b; }
.psc-sparkplug-scope .psc-sp-step-n {
  align-items: center; justify-content: center; font-weight: 700; font-size: 13px;
  border-radius: 50%; background-color: var(--containerNested); color: var(--callToAction);
  border: 1px solid var(--callToAction); box-sizing: border-box;
}
.psc-sparkplug-scope .psc-sp-step-n > span { line-height: 1; }
.psc-sparkplug-scope.psc-sp-drawer {
  border-top: 2px solid var(--callToAction);
  box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.45);
}
.psc-sparkplug-scope .psc-sp-table { background-color: transparent; font-size: 13px; }
/* Theme badges shout in 11px capitals; one-word states read calmer at 12px. */
.psc-sparkplug-scope [class*="/status/badge-"] {
  font-size: 13px !important; text-transform: none !important; letter-spacing: .02em !important;
}

"""


def stylesheet():
    p = os.path.join(PERSP, 'stylesheet', 'stylesheet.css')
    s = open(p).read()
    if CSS_MARK not in s:
        raise SystemExit('stylesheet: Sparkplug block marker not found')
    i = s.index(CSS_MARK)
    j = s.index(CSS_END)
    open(p, 'w').write(s[:i] + CSS + s[j:])


def main():
    out = {
        'Sparkplug': build_main(),
        'SparkplugLiveRow': build_live_row(),
        'SparkplugAlarmRow': build_alarm_row(),
        'SparkplugLive': build_live(),
        'SparkplugAlarms': build_alarms(),
        'SparkplugCommands': build_commands(),
        'SparkplugOutage': build_outage(),
        'SparkplugUdt': build_udt(),
        'SparkplugNotify': build_notify(),
        'SparkplugCtlLive': build_ctl_live(),
        'SparkplugCtlAlarms': build_ctl_alarms(),
        'SparkplugCtlCommands': build_ctl_commands(),
        'SparkplugCtlOutage': build_ctl_outage(),
        'SparkplugCtlUdt': build_ctl_udt(),
        'SparkplugCtlNotify': build_ctl_notify(),
        'SparkplugTraffic': build_traffic(),
    }
    for name, v in out.items():
        write_view(name, v)
    # Views this script no longer writes. SparkplugHelp and SparkplugNotifyAll
    # were the "How it works" popup and the notification log one column wider:
    # a third and a second copy of text the panels already carry.
    # SparkplugGateways was the "Gateways" popup: the rail's GATEWAYS links are
    # the same two gateways, one click nearer, on every demo tab alike.
    for gone in ('SparkplugTagRow', 'SparkplugEventCell', 'SparkplugRoad', 'SparkplugWriteControl',
                 'SparkplugWireCard', 'SparkplugNotifyRow', 'SparkplugWireMsgRow',
                 'SparkplugHelp', 'SparkplugNotifyAll', 'SparkplugGateways'):
        shutil.rmtree(os.path.join(VIEWS, gone), ignore_errors=True)
    page_config()
    stylesheet()
    print('wrote', len(out), 'views')


if __name__ == '__main__':
    main()
