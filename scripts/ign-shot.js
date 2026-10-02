// Screenshot a Perspective page and say whether what came back is trustworthy.
//
//   node scripts/ign-shot.js --url http://localhost:29088 --project Site1 \
//        --page demo --out .shots/site1.png
//
// --click <selector> presses something first, for a view that is only reachable
// by pressing it. The demo console is one page with five TABS, so without this
// four of its five screens could not be verified at all -- and rule 6 asks for a
// screenshot of every view change, not of the ones that happen to load first.
//
// ADDRESS A COMPONENT BY ITS LABEL, NOT BY ITS NAME -- and by its ROLE, not by
// a text selector. Two things that both look right and both match nothing:
//
//   [data-component-path$="redundancyButton"]   Perspective DOES emit that
//       attribute, but it is a POSITIONAL INDEX (`C.0:0:0:3`). It carries
//       nothing of the component's name in the view JSON, and it changes when
//       a sibling is inserted above it.
//   button:text-is("Redundancy")   `:text-is` tests an element's OWN text, and
//       an ia.input.button renders its label into a CHILD -- so the button's own
//       text is empty and this matches ZERO, while `has-text` matches one.
//
// So --click-text goes through getByRole, which reads the accessible name from
// the descendants. Measured on this console: text-is 0, has-text 1, role 1.
//
// Driven by scripts/ign-shot.sh, which resolves the gateway URL through lib.sh
// so this works the same inside the toolbox and on the host. Read-only against
// the gateway.
//
// WHY THIS EXISTS AT ALL
//
// CLAUDE.md's rule 6 -- self-verify a view change by screenshotting it -- named
// a tool in a directory outside this repo, and that directory does not exist on
// the machine the rule was written for, let alone on a colleague's. A mandatory
// verification step that nobody can run is worse than none: it reads as done.
//
// THREE THINGS IT CHECKS THAT A BARE SCREENSHOT DOES NOT
//
// 1. THE TRIAL SPLASH. These gateways are unlicensed, so Perspective runs a
//    rolling 2-hour trial and every session is replaced by a splash when it
//    lapses. A perfect deploy then photographs as a broken one. Detected and
//    reported as its own outcome, because the repair is `make trial-reset` and
//    has nothing to do with the change being verified.
//
// 2. COMPONENT ERROR BOXES. A Perspective script transform that raises renders
//    a red ERROR box and logs NOTHING anywhere a script can see -- the failure
//    goes to the browser and stops. Counting them here is the only cheap way to
//    catch it.
//
// 3. A FRESH SESSION, by construction. A scan applies files, but an ALREADY
//    OPEN session can keep showing the previous structure of a view -- value
//    changes reach it, a changed component tree need not. Every run here is a
//    new browser and a new context, so what it photographs is what a new
//    session gets. That is the whole reason to trust it.
//
// Headless, always. This is verification, not a demonstration; a window popping
// up on someone's screen mid-task is a bug in the tool.
const path = require('path');

// The same resolver ign-gw.js and e2e-demos.js carry, for the same reason:
// inside the toolbox playwright is global and the bare require wins, and
// nothing here may be an absolute path that exists on one person's laptop.
function loadPlaywright() {
  const candidates = [
    null,
    process.env.PLAYWRIGHT_HOME && path.join(process.env.PLAYWRIGHT_HOME, 'node_modules', 'playwright'),
    path.join(__dirname, '..', 'node_modules', 'playwright'),
  ].filter(c => c !== undefined);
  for (const c of candidates) {
    try { return c ? require(c) : require('playwright'); } catch (e) { /* next */ }
  }
  console.error('ign-shot: playwright not found. Run this through ./wd, which has it.');
  process.exit(2);
}

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i === -1 ? fallback : process.argv[i + 1];
}

(async () => {
  const url      = arg('url');
  const project  = arg('project', 'Site1');
  const page_    = arg('page', 'demo');
  const out      = arg('out', '.shots/shot.png');
  const wait     = parseInt(arg('wait', '12000'), 10);
  const click    = arg('click', '');
  const clickText = arg('click-text', '');
  // After a click the session re-renders in place -- no navigation, so there is
  // nothing to wait for except the render itself.
  const settle   = parseInt(arg('settle', '4000'), 10);
  const width    = parseInt(arg('width', '1440'), 10);
  const height   = parseInt(arg('height', '900'), 10);
  if (!url) { console.error('ign-shot: --url is required'); process.exit(2); }

  const target = `${url}/data/perspective/client/${project}` + (page_ ? `/${page_}` : '');
  const { chromium } = loadPlaywright();

  const browser = await chromium.launch({ headless: true });
  // ignoreHTTPSErrors: the front door and the site proxies use this machine's
  // own CA, which is machine-local on purpose and is not in chromium's store.
  const ctx = await browser.newContext({ viewport: { width, height }, ignoreHTTPSErrors: true });
  const pg = await ctx.newPage();

  const consoleErrors = [];
  pg.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  pg.on('pageerror', e => consoleErrors.push(String(e).slice(0, 200)));

  let status = 0;
  try {
    // domcontentloaded, not networkidle: a Perspective session holds an open
    // websocket and polls, so networkidle never fires and the wait times out on
    // a page that is working perfectly.
    const resp = await pg.goto(target, { waitUntil: 'domcontentloaded', timeout: 45000 });
    if (resp && resp.status() >= 400) {
      console.error(`ign-shot: ${resp.status()} from ${target}`);
      if (resp.status() === 404) {
        console.error('  a 404 here is usually the wrong project name. An EDGE gateway runs');
        console.error('  exactly one project and it is always called Edge -- use PROJECT=Edge.');
      }
      status = 4;
    }
    await pg.waitForTimeout(wait);

    // BOTH clicks, in this order: --click-text (a console TAB) and then --click
    // (something inside it -- a sub-tab, say). This used to be one OR the other,
    // `clickText ? … : click`, so `make shot TAB=Sparkplug CLICK=…` silently
    // dropped the CLICK and photographed the tab's default sub-tab three times
    // under three different names (11/09/2026) -- the exact wrong-screen evidence
    // the paragraph below exists to refuse.
    const steps = [];
    if (clickText) steps.push({ what: `--click-text "${clickText}"`,
                                loc: () => pg.getByRole('button', { name: clickText, exact: true }) });
    if (click)     steps.push({ what: `--click "${click}"`, loc: () => pg.locator(click) });
    for (const step of steps) {
      // A click that finds nothing must FAIL here rather than photograph the
      // wrong screen: a shot of the default tab, captioned as the tab you asked
      // for, is exactly the kind of evidence that reads as verification and is
      // not. The wait is generous because the nav renders late in this app.
      try {
        await step.loc().first().click({ timeout: 15000 });
      } catch (e) {
        console.error(`ign-shot: ${step.what} matched nothing clickable.`);
        console.error(`  ${String(e.message || e).split('\n')[0]}`);
        console.error('  Labels live in a CHILD of the button, so :text-is matches zero.');
        console.error('  Use --click-text (accessible name), or --click with');
        console.error('  button:has(:text-is("Label")) for an exact label.');
        await browser.close();
        process.exit(4);
      }
      await pg.waitForTimeout(settle);
    }

    await pg.screenshot({ path: out, fullPage: false });

    const verdict = await pg.evaluate(() => {
      const text = (document.body && document.body.innerText) || '';
      return {
        trial: /trial\s*expired/i.test(text),
        sessions: /sessions\s*exceeded/i.test(text),
        noProject: /does not exist/i.test(text),
        errorBoxes: document.querySelectorAll(
          '.component-error,[class*="error-box"],[class*="componentError"]').length,
        components: document.querySelectorAll('[data-component]').length,
        title: document.title,
      };
    });

    const clicked = [clickText, click].filter(Boolean).join(' then ');
    console.log(`wrote ${out}  (${width}x${height}, ${project}/${page_}${clicked ? ` after clicking ${clicked}` : ''})`);
    console.log(`  components rendered : ${verdict.components}`);
    console.log(`  component error box : ${verdict.errorBoxes}`);
    console.log(`  console errors      : ${consoleErrors.length}`);
    consoleErrors.slice(0, 5).forEach(e => console.log(`      ${e}`));

    if (verdict.sessions) {
      console.log('');
      console.log('  SESSIONS EXCEEDED -- almost always ONE SETTING, not a licence.');
      console.log('  An Edge gateway has a single Panel session, and visualizationName');
      console.log('  (ignition/edge-system-properties) decides which module gets it. It');
      console.log('  defaults to VISION, so Perspective is refused on a healthy gateway.');
      console.log('  Fix: ./wd edge-visual      Detail: docs/EAM.md');
      status = 5;
    } else if (verdict.noProject) {
      console.log('');
      console.log('  NO SUCH PROJECT on this gateway. An Edge gateway runs exactly one and');
      console.log('  it is always called Edge, whatever the pushed project was named.');
      status = 4;
    } else if (verdict.trial) {
      console.log('');
      console.log('  TRIAL EXPIRED -- this says nothing about your change.');
      console.log('  The session was replaced by the splash. Fix: make trial-reset');
      status = 3;
    } else if (verdict.errorBoxes > 0) {
      console.log('');
      console.log('  A component rendered as an ERROR box. A Perspective script transform');
      console.log('  that raises does this and logs NOTHING -- read the PNG to find which.');
      status = 4;
    } else if (verdict.components === 0) {
      console.log('');
      console.log('  Nothing rendered. Wrong project or page route, or the session never');
      console.log('  started -- try a longer --wait before believing the page is broken.');
      status = 4;
    }
  } finally {
    await browser.close();
  }
  process.exit(status);
})();
