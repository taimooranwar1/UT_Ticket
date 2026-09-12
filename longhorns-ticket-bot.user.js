// ==UserScript==
// @name         Longhorns FB26 Ticket Retry Bot
// @namespace    https://texaslonghorns.evenue.net/
// @version      1.0
// @description  Every N seconds: set quantity to 1, click "Find Best Available", dismiss the "Seats Not Found" modal, repeat until something other than that error happens (or the deadline passes), then scream about it.
// @match        https://texaslonghorns.evenue.net/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/* =============================================================================
   HOW TO USE
   -----------------------------------------------------------------------------
   Option A (recommended): install Tampermonkey, create a new script, paste this
   file in, save, then reload the FB26 page in your logged-in window. The bot
   survives page navigations this way.

   Option B (quick): open DevTools (F12) on the already-open logged-in tab,
   paste this whole file into the Console, hit Enter. Works until the page
   navigates away, at which point you'd have to paste it again -- which is
   exactly why Option A is better.

   Controls, once running (type these in the console):
     UTBOT.stop()      - stop the loop
     UTBOT.start()     - start it again
     UTBOT.status()    - where it's at
     UTBOT.inspect()   - dump the clickable elements it can see, so the
                         selectors below can be corrected if the page differs
     UTBOT.testAlert() - fire the success alert without waiting for a ticket
   ============================================================================= */

(function () {
  'use strict';

  if (window.__UTBOT_LOADED__) {
    console.log('[UTBOT] already loaded; use UTBOT.status()');
    return;
  }
  window.__UTBOT_LOADED__ = true;

  // ===========================================================================
  // CONFIG -- edit these
  // ===========================================================================
  const CONFIG = {
    // Stop trying at this local time. Months are 0-indexed: 8 === September.
    deadline: new Date(2026, 8, 12, 16, 30, 0),

    // Seconds between attempts.
    intervalSeconds: 20,

    // How many tickets to select (how many times to click the "+" icon).
    quantity: 1,

    // Where to send the alert.
    notifyEmail: 'taimoor.anwar11@gmail.com',
    emailSubject: 'TICKET FOUND',
    emailBody: 'TICKET FOUND',

    // Optional: a webhook that actually sends the email by itself, with no
    // click from you. See EMAIL SETUP in README.md. Leave '' to disable and
    // rely on the pre-filled Gmail compose tab instead.
    ntfyTopic: '',

    // Text that identifies the "no luck, try again" modal. Case-insensitive,
    // any one match is enough.
    errorMarkers: [
      'seats not found',
      'no seats that matched',
      'adjust your selections'
    ],

    // Button label matching (case-insensitive substring).
    findButtonText: ['find best available', 'best available'],
    okButtonText: ['ok', 'okay', 'close', 'continue'],

    // Only run on the ticketing site. This is what stops the script from
    // firing on some other tab (e.g. a chat window that happens to contain
    // the words "Seats Not Found") and reporting nonsense. Set to true only
    // if you know what you're doing.
    allowAnyHost: false,
    hostPattern: /evenue\.net$/i,

    // Set true to watch every step in the console without clicking anything.
    dryRun: false
  };

  // ===========================================================================
  // State (persisted so the bot can resume after a page navigation)
  // ===========================================================================
  const SKEY = 'UTBOT_STATE_V1';
  const loadState = () => {
    try { return JSON.parse(localStorage.getItem(SKEY)) || {}; } catch (e) { return {}; }
  };
  const saveState = (s) => {
    try { localStorage.setItem(SKEY, JSON.stringify(s)); } catch (e) {}
  };

  let state = Object.assign({
    running: true,
    attempts: 0,
    alerted: false,
    lastClickAt: 0,
    searchUrl: location.href
  }, loadState());

  let timer = null;

  const log = (...args) => console.log('%c[UTBOT]', 'color:#bf5700;font-weight:bold', ...args);

  // ===========================================================================
  // DOM helpers -- text-based so they survive class-name changes, and they
  // reach into same-origin iframes because evenue likes those.
  // ===========================================================================
  function documents() {
    const docs = [document];
    for (const frame of document.querySelectorAll('iframe, frame')) {
      try {
        if (frame.contentDocument) docs.push(frame.contentDocument);
      } catch (e) { /* cross-origin, skip */ }
    }
    return docs;
  }

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    if (rect && rect.width === 0 && rect.height === 0) return false;
    const style = (el.ownerDocument.defaultView || window).getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  }

  function labelOf(el) {
    return (
      el.innerText || el.textContent || el.value || el.getAttribute('aria-label') ||
      el.getAttribute('title') || el.getAttribute('alt') || ''
    ).trim();
  }

  const CLICKABLE = [
    'button', 'input[type=button]', 'input[type=submit]', 'input[type=image]',
    'a', 'img', 'label', '[role=button]', '[onclick]', '[class*=btn]',
    '[class*=button]', '[class*=plus]', '[id*=plus]', '[class*=qty]',
    '[id*=qty]', '[class*=increment]', '[class*=arrow]'
  ].join(', ');

  function clickables() {
    const out = [];
    const seen = new Set();
    for (const doc of documents()) {
      for (const el of doc.querySelectorAll(CLICKABLE)) {
        if (visible(el) && !seen.has(el)) { seen.add(el); out.push(el); }
      }
      // Anything the page styles as clickable, even a bare div or td.
      for (const el of doc.querySelectorAll('div, span, td, li')) {
        if (seen.has(el) || !visible(el)) continue;
        const view = el.ownerDocument.defaultView || window;
        if (view.getComputedStyle(el).cursor === 'pointer') { seen.add(el); out.push(el); }
      }
    }
    return out;
  }

  // Resolve an arbitrary element to the thing that should actually be clicked.
  function asClickable(el) {
    if (!el) return null;
    const hit = el.closest('button, input, a, [role=button], [onclick]');
    return hit && visible(hit) ? hit : el;
  }

  function findByText(candidates) {
    const wanted = candidates.map((c) => c.toLowerCase());
    let best = null;
    let bestLen = Infinity;

    const consider = (el) => {
      const text = labelOf(el).toLowerCase();
      if (!text || text.length > 120) return;
      if (!wanted.some((w) => text.includes(w))) return;
      // Prefer the shortest match: "OK" should beat "OK, go to settings",
      // and the button itself should beat the panel containing it.
      if (text.length < bestLen) { bestLen = text.length; best = el; }
    };

    for (const el of clickables()) consider(el);
    if (best) return asClickable(best);

    // Nothing in the clickable set matched. The label may live in a plain
    // element with the real handler on an ancestor -- evenue does this.
    for (const doc of documents()) {
      for (const el of doc.querySelectorAll('*')) {
        if (el.children.length === 0 && visible(el)) consider(el);
      }
    }
    return asClickable(best);
  }

  function findPlusButton() {
    // 1. Anything whose visible label is literally a plus sign.
    for (const el of clickables()) {
      const text = labelOf(el);
      if (text === '+' || text === '＋' || text === '[+]') return el;
    }
    // 2. Anything whose class / id / src hints at an increment control.
    const hint = /(plus|increment|increase|add[-_]?qty|qty[-_]?up|arrow[-_]?up|spinner[-_]?up)/i;
    for (const el of clickables()) {
      const blob = [el.className, el.id, el.getAttribute('src') || '', el.getAttribute('aria-label') || ''].join(' ');
      if (hint.test(blob)) return el;
    }
    return null;
  }

  function findQuantityInput() {
    for (const doc of documents()) {
      for (const el of doc.querySelectorAll('input[type=number], input[type=text], select')) {
        if (!visible(el)) continue;
        const blob = [el.name || '', el.id || '', el.className || '', el.getAttribute('aria-label') || ''].join(' ');
        if (/(qty|quant|numseats|seat|ticket)/i.test(blob)) return el;
      }
    }
    return null;
  }

  function pageText() {
    let text = '';
    for (const doc of documents()) {
      text += ' ' + ((doc.body && doc.body.innerText) || '');
    }
    return text.toLowerCase();
  }

  const DIALOGISH = '[role=dialog], [aria-modal=true], dialog, .modal, .ui-dialog, .popup, .overlay, .lightbox, [class*=modal], [class*=dialog]';

  // Returns the dialog element showing the "Seats Not Found" error, or null.
  // Scoped to dialog-looking containers first so that stray text elsewhere on
  // the page can't be mistaken for the modal.
  function errorModal() {
    const markers = CONFIG.errorMarkers.map((m) => m.toLowerCase());
    for (const doc of documents()) {
      for (const el of doc.querySelectorAll(DIALOGISH)) {
        if (!visible(el)) continue;
        const text = (el.innerText || '').toLowerCase();
        if (markers.some((m) => text.includes(m))) return el;
      }
    }
    return null;
  }

  function errorModalPresent() {
    if (errorModal()) return true;
    // Fallback: the modal may not use any recognizable dialog markup. Only
    // trusted because the host guard has already confirmed we're on evenue.
    const text = pageText();
    return CONFIG.errorMarkers.some((m) => text.includes(m.toLowerCase()));
  }

  // Prefer the OK button inside the error dialog over any other OK on the page.
  function findOkButton() {
    const modal = errorModal();
    if (modal) {
      const wanted = CONFIG.okButtonText.map((t) => t.toLowerCase());
      let best = null;
      for (const el of modal.querySelectorAll(CLICKABLE)) {
        if (!visible(el)) continue;
        const text = labelOf(el).toLowerCase();
        if (text && wanted.some((w) => text.includes(w))) {
          if (!best || text.length < labelOf(best).length) best = el;
        }
      }
      if (best) return asClickable(best);
    }
    return findByText(CONFIG.okButtonText);
  }

  function click(el, what) {
    if (!el) { log('could not find:', what); return false; }
    if (CONFIG.dryRun) { log('[dry run] would click', what, '->', labelOf(el) || el); return true; }
    el.scrollIntoView({ block: 'center' });
    el.click();
    log('clicked', what, '->', JSON.stringify(labelOf(el).slice(0, 40)));
    return true;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ===========================================================================
  // The alert
  // ===========================================================================
  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      for (let i = 0; i < 12; i++) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.frequency.value = i % 2 ? 880 : 1320;
        gain.gain.value = 0.25;
        osc.start(ctx.currentTime + i * 0.35);
        osc.stop(ctx.currentTime + i * 0.35 + 0.3);
      }
    } catch (e) { log('audio blocked:', e.message); }
  }

  function gmailComposeUrl() {
    return 'https://mail.google.com/mail/u/0/?fs=1&tf=cm' +
      '&to=' + encodeURIComponent(CONFIG.notifyEmail) +
      '&su=' + encodeURIComponent(CONFIG.emailSubject) +
      '&body=' + encodeURIComponent(CONFIG.emailBody + '\n\n' + location.href);
  }

  async function sendEmail() {
    // Path 1: a webhook that sends the mail with zero interaction, if set up.
    if (CONFIG.ntfyTopic) {
      try {
        await fetch('https://ntfy.sh/' + CONFIG.ntfyTopic, {
          method: 'POST',
          headers: { 'Email': CONFIG.notifyEmail, 'Title': CONFIG.emailSubject },
          body: CONFIG.emailBody + '\n' + location.href
        });
        log('email dispatched via ntfy');
      } catch (e) {
        log('ntfy failed:', e.message);
      }
    }

    // Path 2: pop a pre-filled Gmail compose window in the logged-in session.
    // The browser will not let a script press Send for you -- one click.
    const win = window.open(gmailComposeUrl(), '_blank');
    if (!win) {
      log('POPUP BLOCKED. Open this yourself:', gmailComposeUrl());
    }
  }

  function alertSuccess(reason) {
    if (state.alerted) return;
    state.alerted = true;
    state.running = false;
    saveState(state);
    clearInterval(timer);

    log('%cTICKET FOUND -- ' + reason, 'color:#fff;background:#bf5700;font-size:20px;padding:4px');
    document.title = '🎟️ TICKET FOUND 🎟️';
    beep();

    try {
      const banner = document.createElement('div');
      banner.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(191,87,0,.96);color:#fff;font:bold 6vw/1.2 system-ui,sans-serif;display:flex;align-items:center;justify-content:center;text-align:center;cursor:pointer';
      banner.textContent = 'TICKET FOUND — click to dismiss';
      banner.onclick = () => banner.remove();
      document.body.appendChild(banner);
    } catch (e) {}

    if (Notification && Notification.permission === 'granted') {
      new Notification('TICKET FOUND', { body: location.href, requireInteraction: true });
    }

    sendEmail();
  }

  // ===========================================================================
  // One attempt
  // ===========================================================================
  async function attempt() {
    if (!state.running || state.alerted) return;

    if (new Date() >= CONFIG.deadline) {
      log('deadline reached (' + CONFIG.deadline.toLocaleString() + ') -- stopping. No ticket.');
      state.running = false;
      saveState(state);
      clearInterval(timer);
      return;
    }

    state.attempts++;
    saveState(state);
    log('attempt #' + state.attempts + ' at ' + new Date().toLocaleTimeString());

    // 1. Clear the error modal if it is still up from last time.
    if (errorModalPresent()) {
      click(findOkButton(), 'OK on leftover error modal');
      await sleep(700);
    }

    // 2. Set quantity to 1 (plus icon preferred, input as fallback).
    const plus = findPlusButton();
    if (plus) {
      for (let i = 0; i < CONFIG.quantity; i++) {
        click(plus, 'plus icon');
        await sleep(250);
      }
    } else {
      const qty = findQuantityInput();
      if (qty && String(qty.value) !== String(CONFIG.quantity)) {
        if (!CONFIG.dryRun) {
          qty.value = CONFIG.quantity;
          qty.dispatchEvent(new Event('input', { bubbles: true }));
          qty.dispatchEvent(new Event('change', { bubbles: true }));
        }
        log('set quantity field to', CONFIG.quantity);
      } else if (!qty) {
        log('no plus icon and no quantity field found -- run UTBOT.inspect() and fix the selectors');
      }
    }
    await sleep(400);

    // 3. Find Best Available.
    const findBtn = findByText(CONFIG.findButtonText);
    if (!findBtn) {
      log('could not find the "Find Best Available" button -- run UTBOT.inspect()');
      return;
    }
    const urlBefore = location.href;
    click(findBtn, 'Find Best Available');
    state.lastClickAt = Date.now();
    saveState(state);

    // 4. Wait for the result, checking as it comes in rather than once at the end.
    for (let waited = 0; waited < 9000; waited += 500) {
      await sleep(500);
      if (errorModalPresent()) {
        log('  -> "Seats Not Found". Dismissing, will retry in ' + CONFIG.intervalSeconds + 's.');
        click(findOkButton(), 'OK');
        return;
      }
      if (location.href !== urlBefore) {
        alertSuccess('page navigated to ' + location.href);
        return;
      }
    }

    // 5. No error modal appeared and we're still here. Something changed --
    //    a seat map, a cart, a different dialog. That counts as "not the error".
    if (!errorModalPresent()) {
      alertSuccess('no "Seats Not Found" error after clicking -- something else came up');
    }
  }

  // ===========================================================================
  // Resume-after-navigation check
  // ===========================================================================
  function checkResumeAfterNavigation() {
    // If we clicked recently and the page then loaded somewhere new without an
    // error modal, that's a hit.
    if (!state.lastClickAt || state.alerted) return;
    const sinceClick = Date.now() - state.lastClickAt;
    if (sinceClick < 30000 && !errorModalPresent() && location.href !== state.searchUrl) {
      alertSuccess('landed on a new page after the last click: ' + location.href);
    }
  }

  // ===========================================================================
  // Public API
  // ===========================================================================
  const UTBOT = {
    start() {
      state.running = true;
      state.alerted = false;
      saveState(state);
      clearInterval(timer);
      attempt();
      timer = setInterval(attempt, CONFIG.intervalSeconds * 1000);
      log('started. every ' + CONFIG.intervalSeconds + 's until ' + CONFIG.deadline.toLocaleString());
    },
    stop() {
      state.running = false;
      saveState(state);
      clearInterval(timer);
      log('stopped.');
    },
    status() {
      log({
        running: state.running,
        attempts: state.attempts,
        alerted: state.alerted,
        deadline: CONFIG.deadline.toLocaleString(),
        minutesLeft: Math.round((CONFIG.deadline - new Date()) / 60000),
        errorModalOnScreen: errorModalPresent()
      });
    },
    inspect() {
      log('host:', location.hostname, '| iframes:', document.querySelectorAll('iframe, frame').length);
      const rows = clickables().map((el) => ({
        tag: el.tagName,
        label: labelOf(el).slice(0, 60),
        id: el.id,
        class: typeof el.className === 'string' ? el.className.slice(0, 60) : ''
      }));
      console.table(rows);

      // Anything on the page mentioning availability/quantity, clickable or
      // not -- this is what to feed back if the buttons still aren't found.
      const hints = [];
      for (const doc of documents()) {
        for (const el of doc.querySelectorAll('*')) {
          if (el.children.length || !visible(el)) continue;
          const text = labelOf(el);
          if (text && text.length < 80 && /(available|quantity|qty|seat|\+)/i.test(text)) {
            hints.push({ tag: el.tagName, text: text.slice(0, 60), id: el.id, class: String(el.className).slice(0, 40) });
          }
        }
      }
      console.table(hints);
      log('plus candidate:', findPlusButton());
      log('find-best candidate:', findByText(CONFIG.findButtonText));
      log('ok candidate:', findOkButton());
      return rows.length + ' clickable elements';
    },
    testAlert() { state.alerted = false; alertSuccess('TEST -- not a real ticket'); },
    config: CONFIG,
    reset() { localStorage.removeItem(SKEY); log('state cleared'); }
  };

  window.UTBOT = UTBOT;

  if (Notification && Notification.permission === 'default') {
    Notification.requestPermission();
  }

  // ---------------------------------------------------------------------------
  // Host guard: refuse to run anywhere but the ticketing site.
  // ---------------------------------------------------------------------------
  const onTicketSite = CONFIG.hostPattern.test(location.hostname);
  if (!onTicketSite && !CONFIG.allowAnyHost) {
    console.error(
      '%c[UTBOT] WRONG TAB\n' +
      'This is ' + location.hostname + ', not the evenue ticketing site.\n' +
      'Open https://texaslonghorns.evenue.net/students/combo/FB26/FB02S in your\n' +
      'logged-in window, then paste this script into THAT tab\'s console.',
      'color:#fff;background:#c00;font-size:14px;padding:6px'
    );
    window.__UTBOT_LOADED__ = false;
    return;
  }

  checkResumeAfterNavigation();

  if (new Date() >= CONFIG.deadline) {
    log('deadline already passed -- not starting.');
  } else if (state.alerted) {
    log('already alerted on a previous run. UTBOT.reset() then UTBOT.start() to go again.');
  } else {
    UTBOT.start();
  }
})();
