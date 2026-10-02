// End-to-end test of the Demos page. Drives the REAL page in a browser and
// checks the machine with docker, not with the same API the page used.
//
//   node scripts/e2e-demos.js [--url http://localhost:8090]
//
// WHY IT ASSERTS AGAINST DOCKER
//
// Every claim this page makes is a claim about containers. Verifying it by
// asking wd-control -- the thing that told the page in the first place -- would
// pass just as happily if the control plane were confidently wrong. So every
// state assertion here goes to `docker ps`, and the page is only ever the thing
// being tested.
//
// WHAT IT COSTS
//
// It starts real stacks, including two Ignition gateways, and stops them again.
// Budget about fifteen minutes and 4 GB. It restores the machine to core-only
// at the end, including if an assertion fails.
//
const path = require('path');
const { execFileSync } = require('child_process');

// Inside the toolbox image playwright is installed globally and NODE_PATH points
// at it, so the bare require is the answer and the rest of this list never runs.
// The remaining candidates are for running this natively on the machine it was
// written on; they are a convenience, not a dependency -- `./wd` needs none of
// them. Nothing here may be a hard-coded absolute path that only exists on one
// person's laptop: a bare require alone used to work here only because a
// gitignored `node_modules` symlink pointed into an unrelated repo, which is
// the same portability break `ign-gw.js` already carries this resolver to avoid.
function loadPlaywright() {
  const candidates = [
    null,
    process.env.PLAYWRIGHT_HOME && path.join(process.env.PLAYWRIGHT_HOME, 'node_modules', 'playwright'),
    path.join(__dirname, '..', 'node_modules', 'playwright'),
  ].filter(c => c !== undefined);
  for (const c of candidates) {
    try { return c ? require(c) : require('playwright'); } catch (e) { /* next */ }
  }
  console.error('e2e-demos: playwright not found.');
  console.error('  The supported way to run this is through the toolbox, which has it:');
  console.error('    ./wd -- node scripts/e2e-demos.js ...     (or wd.cmd on Windows)');
  console.error('  To run natively instead: npm i playwright && npx playwright install chromium');
  process.exit(2);
}

const { chromium } = loadPlaywright();

const arg = (name, dflt) => {
  const i = process.argv.indexOf(name);
  return i > -1 ? process.argv[i + 1] : dflt;
};
const BASE = arg('--url', 'http://localhost:8090');
const PAGE = BASE + '/data/perspective/client/GatewayAdmin';

let passed = 0, failed = 0, pending = 0;
const log = (s) => console.log(s);
const ok = (m) => { passed++; log('  ok   ' + m); };
const bad = (m) => { failed++; log('  FAIL ' + m); };
const check = (cond, m) => cond ? ok(m) : bad(m);
// A guard the page agent has not landed yet is the CURRENT tree's correct
// state, not a bug this file should trip on -- so it is reported separately
// from a real failure and does not fail the run. The day a guard lands and
// the button actually goes disabled, `guarded()` below starts asserting for
// real with no further edit here. See GUARDS-AUDIT.md mechanism A note under
// "Headless test".
const soon = (m) => { pending++; log('  soon ' + m); };

// --- the machine, independently of the page ---------------------------------
function running() {
  const out = execFileSync('docker', [
    'ps', '--format', '{{.Label "au.gaskony.wd.stack"}}'], { encoding: 'utf8' });
  return new Set(out.split('\n').map(s => s.trim()).filter(Boolean));
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// What the machine has been ASKED for, read from the volume rather than the
// API: this is the file that survives a restart, and the one a lost click
// corrupts.
function desiredSet() {
  try {
    const out = execFileSync('docker',
      ['exec', 'wd-control', 'cat', '/state/desired.json'], { encoding: 'utf8' });
    return new Set(JSON.parse(out).demos);
  } catch (e) {
    return new Set(['<unreadable>']);
  }
}

// --- the page ----------------------------------------------------------------
//
// FINDING A CARD IS THE PART THAT HAS TO BE EXACT.
//
// The first version of this walked up from each button until an ancestor's text
// contained the demo title. That ancestor is eventually the container holding
// ALL the cards, at which point every fragment matches every button -- so
// "click Start on store-forward" clicked the first Start on the page and
// started whichever demo was listed first instead. Every assertion after
// that was about a demo nobody had asked for, and they would mostly have passed.
//
// A repeater instance is one card, so the boundary is structural: the flex
// repeater's direct children. Nothing here searches by text until the card is
// already identified.
async function cards(page) {
  return page.evaluate(() => {
    const btns = [...document.querySelectorAll('button')]
      .filter(b => ['Start', 'Stop'].includes((b.textContent || '').trim()));
    if (!btns.length) return [];
    // Walk up from one button until the ancestor holds exactly one of them:
    // that node is a single card, and its depth is the same for all of them.
    let depth = 0, n = btns[0];
    while (n && depth < 12) {
      if (btns.filter(b => n.contains(b)).length === 1 &&
          (n.innerText || '').split('\n').length >= 3) break;
      n = n.parentElement; depth++;
    }
    const cardOf = (b) => { let x = b; for (let i = 0; i < depth; i++) x = x.parentElement; return x; };
    return btns.map(b => {
      const el = cardOf(b);
      return {
        action: (b.textContent || '').trim(),
        text: el.innerText,
        lines: el.innerText.split('\n').map(s => s.trim()).filter(Boolean),
      };
    });
  });
}

async function card(page, titleFragment) {
  const all = await cards(page);
  const hits = all.filter(c => c.text.includes(titleFragment));
  if (hits.length > 1) throw new Error(
    `'${titleFragment}' matched ${hits.length} cards -- the fragment is ambiguous`);
  return hits[0];
}

async function pageState(page) {
  return page.evaluate(() => {
    const t = document.body.innerText;
    // Anchored on "Core N/M up", not on "Core": the HEADLINE also begins with
    // that word ("Core only -- nothing else is running") and sorts first, so a
    // loose match reads the headline and reports the core bar as wrong.
    const core = (t.match(/^Core \d+\/\d+ up.*$/m) || [''])[0];
    return { core, body: t };
  });
}

// Click the Start/Stop button on exactly one card, and refuse to guess.
//
// It returns the card's own text so the caller's log records WHAT was clicked
// rather than what was asked for -- the difference between those two is what
// the first version of this file got wrong, silently.
async function clickCard(page, fragment, expect) {
  const idx = await page.evaluate(({ frag, want }) => {
    const btns = [...document.querySelectorAll('button')]
      .filter(b => ['Start', 'Stop'].includes((b.textContent || '').trim()));
    let depth = 0, n = btns[0];
    while (n && depth < 12) {
      if (btns.filter(b => n.contains(b)).length === 1 &&
          (n.innerText || '').split('\n').length >= 3) break;
      n = n.parentElement; depth++;
    }
    const hits = [];
    btns.forEach((b, i) => {
      let x = b; for (let k = 0; k < depth; k++) x = x.parentElement;
      if ((x.innerText || '').includes(frag)) hits.push({ i, label: (b.textContent || '').trim() });
    });
    if (hits.length !== 1) return { error: `${hits.length} cards match '${frag}'` };
    if (hits[0].label !== want) return { error: `card reads '${hits[0].label}', wanted '${want}'` };
    // MARK the element rather than returning its index. The page repolls every
    // three seconds, so an index computed here and used a moment later can
    // address a different button after a re-render. A marked element that has
    // been re-rendered away makes the click TIME OUT, which is a failure you
    // can see -- an index that has shifted is a click on the wrong demo, which
    // is not.
    document.querySelectorAll('[data-e2e-target]')
      .forEach(e => e.removeAttribute('data-e2e-target'));
    btns[hits[0].i].setAttribute('data-e2e-target', '1');
    return { marked: true };
  }, { frag: fragment, want: expect });

  if (idx.error) { log('       ' + idx.error); return false; }
  try {
    await page.click('[data-e2e-target="1"]', { timeout: 5000 });
  } catch (e) {
    log('       the target button was re-rendered before it could be clicked');
    return false;
  }
  return true;
}

// Reads whether a demo card's own Start/Stop button is unavailable, without
// clicking it. Mirrors clickCard's card-boundary walk so it addresses the
// same button clickCard would -- kept as its own copy rather than shared,
// because each runs inside an isolated page.evaluate() closure that cannot
// reference a function declared outside it.
//
// Checks every signal Perspective is known to render a disabled control
// with (the native `disabled` property, `aria-disabled`, or a state class)
// rather than assuming one: GUARDS-AUDIT.md notes the page agent is still
// standardising a single binding shape for guarded buttons.
async function cardButtonDisabled(page, fragment) {
  return page.evaluate((frag) => {
    const isDisabled = (el) => el.disabled === true ||
      (el.getAttribute('aria-disabled') || '').toLowerCase() === 'true' ||
      /(^|[\s_-])disabled([\s_-]|$)/i.test(el.className || '');
    const btns = [...document.querySelectorAll('button')]
      .filter(b => ['Start', 'Stop'].includes((b.textContent || '').trim()));
    let depth = 0, n = btns[0];
    while (n && depth < 12) {
      if (btns.filter(b => n.contains(b)).length === 1 &&
          (n.innerText || '').split('\n').length >= 3) break;
      n = n.parentElement; depth++;
    }
    const hits = [];
    btns.forEach((b) => {
      let x = b; for (let k = 0; k < depth; k++) x = x.parentElement;
      if ((x.innerText || '').includes(frag)) hits.push(b);
    });
    if (hits.length !== 1) return { error: `${hits.length} cards match '${frag}'` };
    return { label: (hits[0].textContent || '').trim(), disabled: isDisabled(hits[0]) };
  }, fragment);
}

// Same disabled-read, for a single page-level control already resolved to a
// Playwright element handle (eg. "Stop every demo" -- not a demo card, so it
// needs no card-boundary walk).
async function elementDisabled(handle) {
  return handle.evaluate((el) => {
    const btn = el.closest('button') || el;
    return btn.disabled === true ||
      (btn.getAttribute('aria-disabled') || '').toLowerCase() === 'true' ||
      /(^|[\s_-])disabled([\s_-]|$)/i.test(btn.className || '');
  });
}

// Reports a disabled-state result against the situation it was read in.
// `state.error` (the button could not be found at all) is always a hard
// failure -- that is a broken test, not an unlanded guard. `state.disabled
// === false` is the pending case: say plainly which button and what it was
// doing when it should have been unavailable.
function guarded(state, label, situation) {
  if (state.error) { bad(`${label}: ${state.error}`); return; }
  if (state.disabled) ok(`${label} is disabled while ${situation}`);
  else soon(`${label} is NOT disabled while ${situation} -- guard not landed yet`);
}

// Has this session lost its websocket? Perspective renders a banner and the
// DOM then stops updating -- every binding is frozen at its last value.
//
// This has to be checked, because it is INDISTINGUISHABLE from the thing being
// tested. A frozen page says "Starting" for ever, which is exactly what a demo
// that never comes up looks like. It happened on the first full run: the
// session dropped with a 1006 right after two rapid clicks and the test then
// polled a dead page for twelve minutes before timing out, and the failure it
// eventually reported was about the demo console rather than about the socket.
// Read the banner's CLASS, never its text. "No Connection to Gateway" is in the
// DOM of a perfectly healthy page -- Perspective ships the banner permanently
// and toggles `banner-inactive` on its parent. Matching the words means every
// page looks disconnected, which is how a detector for a rare fault becomes a
// permanent false alarm.
async function disconnected(page) {
  return page.evaluate(() => {
    const b = document.querySelector('.connection-banner');
    return !!b && !b.className.includes('banner-inactive');
  });
}

// Poll the PAGE until a card reads the state we are waiting for. Deliberately
// reads the card rather than the API: "the page eventually says Running" is the
// property under test, and a poll against wd-control would pass on a page that
// never updates.
async function waitForCard(page, fragment, wantedState, timeoutMs) {
  const started = Date.now();
  let last = '', reloads = 0;
  while (Date.now() - started < timeoutMs) {
    if (await disconnected(page)) {
      if (++reloads > 3) { log('       session kept dropping -- giving up'); return false; }
      log(`       session lost its websocket -- reloading (${reloads})`);
      await page.reload({ waitUntil: 'networkidle', timeout: 60000 });
      await page.waitForSelector('[class*="psc-"]', { timeout: 120000 });
      await page.getByText('Demos', { exact: false }).first().click();
      await page.waitForTimeout(5000);
      continue;
    }
    const c = await card(page, fragment);
    if (c) {
      last = c.lines.slice(0, 2).join(' / ');
      if (c.text.includes(wantedState)) return true;
    }
    await sleep(3000);
  }
  log('       last seen: ' + last);
  return false;
}

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();

  try {
    log('\n== 0. baseline ==');
    await page.goto(PAGE, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForSelector('[class*="psc-"]', { timeout: 120000 });
    await page.getByText('Demos', { exact: false }).first().click();
    await page.waitForTimeout(5000);

    const all = await cards(page);
    // DERIVED, never a literal. This read `all.length === 5` through two demo
    // removals -- it was written when there were five and nothing re-checked
    // it, so the run failed on its first assertion for weeks while looking
    // like a broken page.
    const demoCount = require('../demos.json').demos.length;
    check(all.length === demoCount,
      `${demoCount} demo cards rendered (saw ${all.length})`);

    // THE FRAME. The grid is padded with inert slots up to the next whole row
    // of demo_control.ACROSS, so "how many tiles" and "how many demos" are now
    // different questions and the page can be wrong about either. A slot must
    // carry NO Start/Stop button -- that is what keeps cards() above counting
    // demos rather than tiles, and it is structural: a slot is a different view
    // (DemoSlot), not a DemoCard with its button hidden.
    //
    // ACROSS is READ, not written down here. This block was authored against a
    // six-tile frame and said `=== 6` in two places; the frame became one row
    // of four the same week and nothing connected the two, which is the exact
    // shape of the `all.length === 5` bug directly above. The regex is how
    // scripts/validate.py reads the same constant -- Jython 2 source will not
    // survive a JSON or AST parse, and one line of it is not worth a parser.
    const across = Number(/^ACROSS\s*=\s*(\d+)/m.exec(require('fs').readFileSync(
      require('path').join(__dirname, '..', 'ignition', 'projects', 'GatewayAdmin',
        'ignition', 'script-python', 'demo_control', 'code.py'), 'utf8'))[1]);
    const wantTiles = Math.ceil(demoCount / across) * across;
    const wantRows = wantTiles / across;

    const frame = await page.evaluate(() => {
      const btns = [...document.querySelectorAll('button')]
        .filter(b => ['Start', 'Stop'].includes((b.textContent || '').trim()));
      if (!btns.length) return { tiles: 0, withButton: 0, rows: 0 };
      // Climb until the ancestor holds EVERY Start/Stop button: that node is
      // the repeater and its children are the tiles. Do NOT reuse cards()'s
      // fixed-depth walk and take .parentElement -- that finds the CARD, and
      // a card's parent stopped being the repeater when DemoTile was added,
      // so it reports one tile containing everything. Measured, on the way in.
      let grid = btns[0];
      while (grid && btns.filter(b => grid.contains(b)).length < btns.length)
        grid = grid.parentElement;
      const tiles = [...grid.children];
      return {
        tiles: tiles.length,
        withButton: tiles.filter(t => t.querySelector('button')).length,
        rows: new Set(tiles.map(t => Math.round(t.getBoundingClientRect().top))).size,
      };
    });
    check(frame.tiles === wantTiles,
      `${wantTiles} tiles in the frame (saw ${frame.tiles})`);
    check(frame.withButton === demoCount,
      `exactly ${demoCount} tiles carry a button -- the rest are inert slots ` +
      `(saw ${frame.withButton})`);
    check(frame.rows === wantRows,
      `the frame is ${wantRows} row(s) of ${across} (saw ${frame.rows})`);

    // THE HARNESS CHECKS ITSELF FIRST. Every later assertion assumes "this card
    // is that demo", and when that assumption broke the run did not fail -- it
    // tested the wrong demo and passed. So: the cards carry the titles from
    // demos.json, one each, and no card contains another's title.
    const wanted = require('../demos.json').demos.map(d => d.title);
    const matched = wanted.map(t => all.filter(c => c.text.includes(t)).length);
    check(matched.every(n => n === 1),
      'each demo title identifies exactly one card ' +
      `(${wanted.map((t, i) => t.split(',')[0] + '=' + matched[i]).join(', ')})`);
    check(new Set(all.map(c => c.text)).size === all.length,
      'the cards have distinct text -- the card boundary is a real boundary');
    const st = await pageState(page);
    check(/Core 3\/3 up/.test(st.core), 'core reads 3/3 up: ' + st.core.slice(0, 60));

    const before = running();
    check(before.has('ignition') && before.has('npm') && before.has('wd-control'),
      'core containers up before the test');

    log('\n== 1. start a demo from the page ==');
    check(await clickCard(page, 'Store & Forward Demo', 'Start'),
      'clicked Start on store-forward');
    check(await waitForCard(page, 'Store & Forward Demo', 'Running', 600000),
      'store-forward card reaches Running (two gateways -- slow)');
    let now = running();
    check(['postgres', 'ignition-edge1', 'ignition-edge2'].every(s => now.has(s)),
      'all three store-forward stacks are actually up in docker');
    const sfCard = await card(page, 'Store & Forward Demo');
    check(/~\d+(\.\d+)? ?(MB|GB)/.test(sfCard.text),
      'card shows a measured RAM cost: ' + (sfCard.text.match(/[~>][^\n]*/) || [''])[0]);

    log('\n== 2. a second job while one is running is refused, and changes NOTHING ==');
    check(await clickCard(page, 'Redundancy Demo', 'Start'), 'clicked Start on redundancy');
    await page.waitForTimeout(600);
    const wantedBefore = desiredSet();

    // GUARDS-AUDIT.md findings 1 and 8: while this reconcile is in flight,
    // a demo card's own Start/Stop AND the page-level "Stop every demo"
    // button should both be unavailable (one busy latch, mechanism A) --
    // not merely refused server-side once clicked. Read the DOM directly;
    // do not just infer it from the click below still going through.
    guarded(await cardButtonDisabled(page, 'EAM Demo'),
      "EAM Demo card's Start/Stop button", 'a reconcile is in flight');
    const stopAllHandle = await page.getByText('Stop every demo', { exact: false })
      .first().elementHandle();
    guarded(stopAllHandle ? { disabled: await elementDisabled(stopAllHandle) }
                          : { error: "'Stop every demo' button not found" },
      "the 'Stop every demo' button", 'a reconcile is in flight');

    check(await clickCard(page, 'EAM Demo', 'Start'),
      'clicked Start on eam while that job is still running');
    // Poll rather than sample once. The message is written when the call
    // returns and then stays, so waiting for it is stable; a single read three
    // seconds later is a coin toss on how quickly the gateway ran the script.
    let sawBusy = false;
    for (let i = 0; i < 8 && !sawBusy; i++) {
      await page.waitForTimeout(1500);
      sawBusy = /Ignored:/i.test((await pageState(page)).body);
    }
    check(sawBusy, 'the page says the click was ignored rather than swallowing it');
    // The regression this earns its place for: the refused click used to write
    // the desired set anyway, and nothing reconciles when a job ENDS -- so the
    // demo was wanted for ever and never started. "Busy" has to mean nothing
    // happened, or it is just a nicer way of losing the click.
    const wantedAfter = desiredSet();
    check(JSON.stringify([...wantedBefore].sort()) === JSON.stringify([...wantedAfter].sort()),
      `a refused click left the desired set alone (${[...wantedBefore]} -> ${[...wantedAfter]})`);

    log('\n== 3. redundancy comes up beside it ==');
    check(await waitForCard(page, 'Redundancy Demo', 'Running', 600000),
      'redundancy card reaches Running');
    now = running();
    check(['ignition-backup', 'ignition-ha'].every(s => now.has(s)),
      "redundancy's own stacks are up");

    log('\n== 4. THE SHARED-STACK CLAIM: stopping one demo must not gut another ==');
    // EAM and store-forward both need the two edge gateways; only store-forward
    // needs postgres. So stopping store-forward has to do BOTH things
    // at once -- take away what only it wanted, and leave what somebody else
    // still wants. This is the property the whole reconciler exists for, and
    // the one a reference-counting button gets wrong.
    check(await clickCard(page, 'EAM Demo', 'Start'), 'clicked Start on eam');
    check(await waitForCard(page, 'EAM Demo', 'Running', 300000),
      'eam card reaches Running (its edges are already up)');
    check(await clickCard(page, 'Store & Forward Demo', 'Stop'),
      'clicked Stop on store-forward');
    await page.waitForTimeout(20000);
    for (let i = 0; i < 40; i++) {
      if (!running().has('postgres')) break;
      await sleep(3000);
    }
    now = running();
    check(!now.has('postgres'),
      "the stacks only store-forward wanted were stopped");
    check(['ignition-edge1', 'ignition-edge2'].every(s => now.has(s)),
      'both EDGES survived -- eam still needs them');
    const eam = await card(page, 'EAM Demo');
    check(eam && eam.text.includes('Running'), 'eam card still reads Running');
    // store-forward now has two of its four stacks up, and both are up for
    // somebody else. That is what "Shared" means, and the card has to
    // distinguish it from the demo you asked for -- and from a stack left
    // running by hand, which is "Up anyway" and will be stopped by the next
    // reconcile.
    const sf = await card(page, 'Store & Forward Demo');
    check(sf && sf.text.includes('Shared'),
      'store-forward reads Shared -- up, but only because eam needs the edges');

    log('\n== 5. the page cannot stop the core ==');
    const stopAll = await page.getByText('Stop every demo', { exact: false }).first();
    await stopAll.click();
    await page.waitForTimeout(20000);
    for (let i = 0; i < 60; i++) {
      const r = running();
      if (!r.has('ignition-edge1') && !r.has('postgres')) break;
      await sleep(3000);
    }
    now = running();
    check(now.has('ignition') && now.has('npm') && now.has('wd-control'),
      'core is STILL up after Stop every demo');
    // Every stack any demo declares, read from demos.json. This was a typed
    // list, and it would have passed with a newer demo's stacks still running.
    const demoStacks = [...new Set(require('../demos.json').demos
      .flatMap(d => d.stacks || []))];
    check(!demoStacks.some(s => now.has(s)),
      `every demo stack is down (${demoStacks.join(', ')})`);

    log('\n== 6. the page survives its control plane going away ==');
    execFileSync('bash', ['scripts/stack.sh', 'down', 'wd-control'],
      { cwd: process.cwd(), stdio: 'ignore' });
    await page.waitForTimeout(12000);
    const downBody = (await pageState(page)).body;
    check(/not answering|not reachable|control plane/i.test(downBody),
      'page reports the control plane is down instead of rendering nothing');
    check(downBody.includes('wd-control'),
      'and names the stack to start');
    execFileSync('bash', ['scripts/stack.sh', 'up', 'wd-control'],
      { cwd: process.cwd(), stdio: 'ignore' });
    await page.waitForTimeout(15000);
    check(await waitForCard(page, 'EAM Demo', 'Stopped', 60000),
      'page recovers on its own once the control plane is back');

  } finally {
    // The machine must come back to core-only even when an assertion threw --
    // an aborted run that leaves two gateways up is how a test costs you the
    // afternoon it was meant to save.
    log('\n== cleanup ==');
    try {
      // wd-control refuses while a job is running, and a failed run is exactly
      // when one is. Wait for idle, then ask, then wait for it to finish.
      for (let i = 0; i < 200; i++) {
        const j = execFileSync('bash', ['-c',
          'curl -s --max-time 5 http://localhost:8485/state | ' +
          'python3 -c \'import json,sys;print(json.load(sys.stdin)["job"]["state"])\' 2>/dev/null || echo gone'],
          { encoding: 'utf8' }).trim();
        if (j !== 'working') break;
        await sleep(5000);
      }
      execFileSync('bash', ['scripts/wd-demos.sh', 'stop-all'],
        { cwd: process.cwd(), stdio: 'ignore', timeout: 900000 });
    } catch (e) { log('  cleanup: ' + String(e.message).slice(0, 120)); }
    const end = [...running()].sort();
    log('  running now: ' + end.join(', '));
    const strays = end.filter(s => !['npm', 'ignition', 'wd-control'].includes(s));
    if (strays.length) log('  LEFT BEHIND: ' + strays.join(', ') + ' -- run: make demos-stop');
    await browser.close();
  }

  // `pending` never fails the run on its own -- it counts guards this file
  // checked for but the page has not landed yet (see `soon` above). Shown
  // separately so a green run still says how much of the guard sweep is
  // real today, the same way validate.py's NOT RUN count does for skips.
  log(`\n${passed + failed} assertions, ${failed} failed, ${pending} pending`);
  process.exit(failed ? 1 : 0);
}

main();
