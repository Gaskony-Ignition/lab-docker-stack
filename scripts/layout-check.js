#!/usr/bin/env node
/*
 * Headless layout gate for the demo console: does a screen fit, at a size a
 * reviewer actually has, without anybody looking at a screenshot to find out.
 *
 *   node scripts/layout-check.js --url http://ignition:8088 --project GatewayAdmin
 *        --page "" --tab Sparkplug --steps "Tags|Alarms|UDT|Notifications"
 *        --sizes 1366x640,1920x1080 --out-dir .shots/layout
 *
 * Driven by scripts/layout-check.sh, which resolves the gateway URL through
 * lib.sh the same way scripts/ign-shot.sh does -- so this works the same
 * inside the toolbox and from the host. Read-only against the gateway.
 *
 * PORTED FROM mining-demo/projects/mining-demo-1/tools/layout-check.js and
 * ~/.claude/.../reference-perspective-layout-verification.md. That project's
 * gate carries dozens of exemptions keyed to ITS OWN classes
 * (`psc-md-card`, a declared `layout-intent.json`, a hand-tuned MAY_SCROLL
 * list). None of that transfers -- this console is a different app with
 * different markup. What transfers is the DESIGN, kept here in five checks:
 *
 *   1. does the PAGE fit -- scrollHeight vs innerHeight on the root itself,
 *      not only on its children (a root exemption answers "may this page
 *      scroll", never "does this dashboard have to").
 *   2. a PHANTOM SCROLLBAR -- an auto/scroll container overflowing by 1-4px.
 *      Chromium draws a full scroll rail for a one-pixel overflow, so the
 *      threshold is one pixel, not two; and the upper bound matters just as
 *      much, because a real scroller (a table body, a long list) overflows
 *      by a lot and reporting it is exactly the noise that buries a genuine
 *      finding. Anything over 4px is left alone on purpose.
 *   3. SLICED TEXT -- a line box more than 1px short of fully painted,
 *      attributed to whichever ancestor does the cutting. A scrollport
 *      excuses a line that is wholly below the fold (reachable by
 *      scrolling); it never excuses a line sliced through the middle,
 *      which is painted, wrong, and nobody knows to scroll for it. The one
 *      stock exemption is a table body's own last row straddling its
 *      bottom edge -- that IS the scroll affordance, not a defect.
 *   4. ESCAPING ITS OWN BOX -- an element whose border box extends past its
 *      nearest clipping ancestor (an `overflow` other than visible, or a
 *      class that reads as a card) where that ancestor is not a scrollport.
 *      A scrollport's whole job is to hold more than it shows; this is only
 *      about a box big enough to spill past a boundary that has no scroll
 *      to offer.
 *   5. A STARVED LIST -- a row-bearing container (a table, a repeater) that
 *      holds more rows than it shows fewer than three of. Three is the point
 *      a list reads as a list rather than as the top of one; a component
 *      that fits perfectly at one visible row is not a layout defect by any
 *      other check here and is exactly what a customer would reject.
 *
 * EXEMPTIONS MATCH THE ELEMENT ITSELF, NEVER ITS ANCESTORS. An exemption
 * implemented by climbing parentElement matches every descendant of the page
 * and reports a confident zero -- a false negative here is worse than no
 * gate at all. The two stated exceptions (gateway chrome, and classifying
 * -- never exempting -- a whole subtree as "inside a scrollport") both COUNT
 * what they skip, so the number is visible on the summary line rather than
 * silently absorbed.
 */
const fs = require('fs');
const path = require('path');

function loadPlaywright() {
  const candidates = [
    null,
    process.env.PLAYWRIGHT_HOME && path.join(process.env.PLAYWRIGHT_HOME, 'node_modules', 'playwright'),
    path.join(__dirname, '..', 'node_modules', 'playwright'),
  ].filter(c => c !== undefined);
  for (const c of candidates) {
    try { return c ? require(c) : require('playwright'); } catch (e) { /* next */ }
  }
  console.error('layout-check: playwright not found. Run this through ./wd, which has it.');
  process.exit(2);
}
const { chromium } = loadPlaywright();

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i === -1 ? fallback : process.argv[i + 1];
}
const has = (name) => process.argv.includes('--' + name);

const URL_BASE = (arg('url', '') || '').replace(/\/+$/, '');
const PROJECT = arg('project', 'GatewayAdmin');
const PAGE = arg('page', '');
const TAB = arg('tab', '');
// STEPS is `|`-separated so a label can carry spaces ("1 Live data"). Empty
// means "no sub-tab to click" -- run once, against whatever TAB (or the root)
// already shows.
const STEPS = arg('steps', '').split('|').map(s => s.trim()).filter(Boolean);
const SIZES = arg('sizes', '1366x640,1920x1080').split(',').map(s => {
  const [w, h] = s.split('x').map(Number);
  return { w, h, label: s };
});
const WAIT = parseInt(arg('wait', '8000'), 10);
const SETTLE = parseInt(arg('settle', '2500'), 10);
const OUT_DIR = arg('out-dir', '.shots/layout');
const QUIET = has('quiet');

if (!URL_BASE) { console.error('layout-check: --url is required'); process.exit(2); }

fs.mkdirSync(OUT_DIR, { recursive: true });

// Ignition's own trial notice and the collapsed app bar are gateway chrome --
// not in any project view, not stylable away, identical on every page.
// Counted, so their absence from the findings is a stated exclusion, not a
// silent one.
const CHROME = '.secondary-message,.primary-message,.app-bar,.trial-banner,[class*="trialCountdown"]';

// Stock Perspective row containers: table bodies and the virtualised grid
// they render as. These are Ignition's own markup, not this console's, so
// they are the one place a part-visible last row is the scroll affordance
// rather than a slice.
const ROW_CONTAINER = /ia_table__body|ReactVirtualized__Grid|ia_alarmStatusTable/;
const ROW_SELECTOR = '.ia_table__row, .ia_table__body__row, [role="row"], [data-column-id]';

const slug = (s) => (s || '').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'root';

const PROBE = (cfg) => {
  // page.evaluate serializes PROBE by itself -- closures over this file's
  // outer consts do not survive the trip into the browser, so everything the
  // probe needs (CHROME, the row selectors) comes in through cfg instead.
  const CHROME = cfg.chrome;
  const ROW_CONTAINER = new RegExp(cfg.rowContainer);
  const ROW_SELECTOR = cfg.rowSelector;
  const out = { doc: null, scrollY: [], scrollX: [], sliced: [], escaping: [], starved: [],
                reachable: 0, gatewayChrome: 0, svgText: 0 };

  const own = (el) => {
    const c = el.getAttribute && el.getAttribute('class');
    return typeof c === 'string' ? c.split(/\s+/).filter(Boolean) : [];
  };
  const describe = (el) => {
    const r = el.getBoundingClientRect();
    const cls = own(el).slice(0, 3).join('.');
    let txt = '';
    try { txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 48); } catch (e) { /* detached */ }
    return { path: `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}`, w: Math.round(r.width), h: Math.round(r.height), text: txt };
  };
  const scrolls = (v) => v === 'auto' || v === 'scroll' || v === 'overlay';

  // 1. does the PAGE fit? asked of the root, not only of its children.
  const de = document.documentElement, body = document.body;
  const dx = Math.max(de.scrollWidth, body.scrollWidth) - de.clientWidth;
  const dy = Math.max(de.scrollHeight, body.scrollHeight) - de.clientHeight;
  if (dx > 0 || dy > 0) out.doc = { x: Math.max(0, dx), y: Math.max(0, dy) };

  const all = document.querySelectorAll('*');
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
    if (el.closest(CHROME)) { out.gatewayChrome++; continue; }
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) continue;

    // 2. a PHANTOM SCROLLBAR: 1-4px of overflow on an auto/scroll container.
    // Anything more is a real scroller (a table body, a long list) and is
    // left alone -- flagging it would be the false positive this gate exists
    // to avoid.
    const oy = el.scrollHeight - el.clientHeight;
    const ox = el.scrollWidth - el.clientWidth;
    if (oy >= 1 && oy <= 4 && scrolls(cs.overflowY)) {
      out.scrollY.push({ ...describe(el), by: oy });
    }
    if (ox >= 1 && ox <= 4 && scrolls(cs.overflowX)) {
      out.scrollX.push({ ...describe(el), by: ox });
    }
  }

  // 3. SLICED TEXT, measured against the box that clips it rather than
  // against scrollHeight (which cannot see overflow through the TOP of a
  // box -- a row pushed upward by `align: end` can sit at top: -20 inside a
  // 7px parent and report no overflow by that arithmetic at all).
  for (const el of document.querySelectorAll('div,span,td,th,p,label,a,button')) {
    if (el.children.length) continue;
    const txt = (el.textContent || '').trim();
    if (!txt) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
    if (el.closest(CHROME)) { out.gatewayChrome++; continue; }
    if (el.namespaceURI === 'http://www.w3.org/2000/svg') { out.svgText++; continue; }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;

    // A container collapsed to zero is HIDDEN, not clipping -- a footer that
    // only appears once something is selected sits at height 0 with its
    // content still in the DOM.
    let collapsedAncestor = false;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      if (a.clientHeight === 0 || a.clientWidth === 0) { collapsedAncestor = true; break; }
    }
    if (collapsedAncestor) continue;

    // The nearest ancestor that actually scrolls something (not merely
    // `overflow: auto` with nothing to scroll -- Perspective writes that on
    // most flex containers, and treating every one as a scrollport would
    // excuse everything).
    let nearestScroller = null;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const acs = getComputedStyle(a);
      if (!scrolls(acs.overflowY) && !scrolls(acs.overflowX)) continue;
      if (a.scrollHeight - a.clientHeight <= 1 && a.scrollWidth - a.clientWidth <= 1) continue;
      nearestScroller = a; break;
    }
    const rowScroller = !!nearestScroller && ROW_CONTAINER.test(nearestScroller.className || '');

    let top = r.top, bottom = r.bottom, left = r.left, right = r.right;
    let hiddenLoss = 0, scrollLoss = 0;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const acs = getComputedStyle(a);
      const clipsY = acs.overflowY !== 'visible', clipsX = acs.overflowX !== 'visible';
      if (!clipsY && !clipsX) continue;
      const ar = a.getBoundingClientRect();
      const before = Math.max(0, bottom - top) + Math.max(0, right - left);
      if (clipsY) { top = Math.max(top, ar.top); bottom = Math.min(bottom, ar.bottom); }
      if (clipsX) { left = Math.max(left, ar.left); right = Math.min(right, ar.right); }
      const cut = before - (Math.max(0, bottom - top) + Math.max(0, right - left));
      if (cut > 0) {
        if (scrolls(acs.overflowY) || scrolls(acs.overflowX)) scrollLoss += cut;
        else hiddenLoss += cut;
      }
    }
    const lostY = r.height - Math.max(0, bottom - top);
    const lostX = r.width - Math.max(0, right - left);

    // A SCROLLPORT EXCUSES A LINE THAT IS ABSENT, NEVER ONE THAT IS SLICED.
    // Full loss inside a scrollport is reachable; partial loss on either
    // axis is a finding whichever kind of box does the cutting.
    const isSliced = !rowScroller
                  && ((lostY > 1 && lostY < r.height - 1)
                   || (lostX > 1 && lostX < r.width - 1));
    if (hiddenLoss <= 1 && !isSliced) { if (scrollLoss > 1) out.reachable++; continue; }
    out.sliced.push({ ...describe(el), axis: lostY > lostX ? 'y' : 'x',
                      by: Math.round(hiddenLoss || scrollLoss) });
  }

  // 4. ESCAPING ITS OWN BOX: an element whose border box extends past its
  // nearest CARD or CLIPPING ancestor, where that ancestor is not itself a
  // scrollport. "Card" is read generically -- any class containing "card"
  // -- because this console's own class names are not known to this file;
  // "clipping" is any ancestor whose overflow is not visible. Matched on the
  // ELEMENT vs that one ancestor, never by walking further than the nearest
  // one -- an ancestor-spanning exemption is exactly the false negative
  // rule 011 of the reference warns against, and this check is the same
  // shape in reverse (a positive that must not over-fire past one boundary).
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
    if (el.closest(CHROME)) continue;
    if (el.namespaceURI === 'http://www.w3.org/2000/svg') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;

    let boundary = null;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const acs = getComputedStyle(a);
      const clips = acs.overflowX !== 'visible' || acs.overflowY !== 'visible';
      const isCard = /card/i.test(a.className || '');
      if (clips || isCard) { boundary = { el: a, cs: acs }; break; }
    }
    if (!boundary) continue;
    // A boundary that actually scrolls is a scrollport doing its job --
    // content inside it is reachable, not escaping, and is the other
    // checks' business (2 and 3), not this one's.
    if (scrolls(boundary.cs.overflowX) || scrolls(boundary.cs.overflowY)) continue;
    const br = boundary.el.getBoundingClientRect();
    const over = Math.max(0, r.right - br.right, br.left - r.left,
                              r.bottom - br.bottom, br.top - r.top);
    if (over > 1) {
      out.escaping.push({ ...describe(el), by: Math.round(over), of: describe(boundary.el).path });
    }
  }

  // 5. A STARVED LIST: a row-bearing container holding more rows than it
  // shows fewer than three of. AT LEAST, never a total -- a virtualised
  // grid sizes its scroll extent to a capacity rather than to the data, so
  // rows actually found in the DOM are the one real number here.
  {
    const MIN_ROWS = 3;
    for (const port of all) {
      const cs = getComputedStyle(port);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      if (!scrolls(cs.overflowY)) continue;
      if (port.closest(CHROME)) continue;
      const hidden = port.scrollHeight - port.clientHeight;
      if (hidden <= 4) continue; // showing all of it

      const tops = new Set();
      let rowH = 0;
      for (const c of port.querySelectorAll(ROW_SELECTOR)) {
        const cr = c.getBoundingClientRect();
        if (cr.height <= 0) continue;
        tops.add(Math.round(cr.top));
        rowH = Math.max(rowH, Math.round(cr.height));
      }
      if (tops.size < 2 || rowH <= 0) continue; // not row-bearing
      const shown = Math.floor(port.clientHeight / rowH);
      if (shown >= MIN_ROWS) continue;
      out.starved.push({ ...describe(port), shown, total: tops.size, rowH });
    }
  }

  return out;
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = [];
  let total = 0;

  const url = `${URL_BASE}/data/perspective/client/${PROJECT}` + (PAGE ? `/${PAGE}` : '');

  for (const size of SIZES) {
    const ctx = await browser.newContext({ viewport: { width: size.w, height: size.h }, deviceScaleFactor: 1 });
    const pg = await ctx.newPage();
    let err = null;
    try {
      await pg.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await pg.waitForTimeout(WAIT);

      // Dismiss the Maker Edition "Personal Use Only" overlay if present --
      // it has no dismiss control and intercepts every click, so a themed or
      // tabbed sweep would otherwise fail every step on every size.
      await pg.evaluate(() => {
        for (const m of document.querySelectorAll('.modal')) {
          if (/Personal Use Only|Maker Edition/i.test(m.textContent || '')) m.remove();
        }
      });

      if (TAB) {
        await pg.getByRole('button', { name: TAB, exact: true }).first().click({ timeout: 15000 });
        await pg.waitForTimeout(SETTLE);
      }
    } catch (e) { err = e.message.split('\n')[0]; }

    if (err) {
      console.log(`ERR   ${size.label} ${TAB || PAGE || '/'} — ${err}`);
      total++;
      await ctx.close();
      continue;
    }

    // At least one "step" always runs -- an empty STEPS list means "probe
    // whatever TAB (or the root) already shows", labelled '(none)'.
    const steps = STEPS.length ? STEPS : [''];
    for (const step of steps) {
      let stepErr = null;
      if (step) {
        try {
          await pg.getByRole('button', { name: step, exact: true }).first().click({ timeout: 15000 });
          await pg.waitForTimeout(SETTLE);
        } catch (e) { stepErr = e.message.split('\n')[0]; }
      }

      const label = step || '(none)';
      if (stepErr) {
        console.log(`ERR   ${size.label}  ${label.padEnd(16)} — button not found: ${stepErr}`);
        total++;
        continue;
      }

      const shotName = `${slug(PROJECT)}-${slug(TAB)}-${slug(step)}-${size.label}.png`;
      const shotPath = path.join(OUT_DIR, shotName);
      await pg.screenshot({ path: shotPath, fullPage: false });

      let res;
      try {
        res = await pg.evaluate(PROBE, {
          chrome: CHROME,
          rowContainer: ROW_CONTAINER.source,
          rowSelector: ROW_SELECTOR,
        });
      } catch (e) {
        console.log(`ERR   ${size.label}  ${label.padEnd(16)} — probe failed: ${e.message.split('\n')[0]}`);
        total++;
        continue;
      }

      const n = (res.doc ? 1 : 0) + res.scrollY.length + res.scrollX.length
              + res.sliced.length + res.escaping.length + res.starved.length;
      total += n;
      report.push({ size: size.label, step: label, ...res });

      const head = `${n ? 'FAIL' : 'ok  '}  ${size.label}  ${label.padEnd(16)}`;
      console.log(`${head}${n ? '  ' + n : ''}  (${shotPath})`);
      if (!n || QUIET) continue;
      if (res.doc) console.log(`      PAGE DOES NOT FIT   x+${res.doc.x} y+${res.doc.y}`);
      for (const s of res.scrollY) console.log(`      scroll-y +${s.by}px  ${s.w}x${s.h}  ${s.path}  "${s.text}"`);
      for (const s of res.scrollX) console.log(`      scroll-x +${s.by}px  ${s.w}x${s.h}  ${s.path}  "${s.text}"`);
      for (const s of res.sliced) console.log(`      sliced-${s.axis} +${s.by}px  ${s.w}x${s.h}  ${s.path}  "${s.text}"`);
      for (const e2 of res.escaping) console.log(`      escapes +${e2.by}px of ${e2.of}  ${e2.w}x${e2.h}  ${e2.path}  "${e2.text}"`);
      for (const s of res.starved) console.log(`      starved  shows ${s.shown} of at least ${s.total} rows (${s.rowH}px each)  ${s.w}x${s.h}  ${s.path}`);
    }
    await ctx.close();
  }
  await browser.close();

  const skipped = report.reduce((a, r) => a + (r.reachable || 0), 0);
  const chrome = report.reduce((a, r) => a + (r.gatewayChrome || 0), 0);
  const svg = report.reduce((a, r) => a + (r.svgText || 0), 0);
  console.log(`\n${total} finding(s) over ${SIZES.length} size(s) x ${(STEPS.length || 1)} step(s)`);
  console.log(`excluded, with reason: ${skipped} off-viewport but inside a scrollport ` +
              `(reachable), ${chrome} gateway chrome (trial notice, app bar), ` +
              `${svg} sub-pixel SVG text`);

  process.exit(total ? 1 : 0);
})();
