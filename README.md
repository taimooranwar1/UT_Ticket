# Longhorns FB26 Ticket Retry Bot

Retries "Find Best Available" on
<https://texaslonghorns.evenue.net/students/combo/FB26/FB02S> every 20 seconds
until either a result appears that is *not* the "Seats Not Found" error, or the
deadline (Sep 12, 2026, 4:30 PM local) passes.

This runs **in your own browser**, in the tab that is already logged in. It is a
page script, not a server — nothing outside your machine touches your session.

## Running it

**Tampermonkey (recommended).** Install the extension, create a new script,
paste in `longhorns-ticket-bot.user.js`, save, reload the FB26 page. It keeps
working across page navigations, which matters because a successful search will
probably navigate you to a seat-selection or cart page.

**Console (quick and dirty).** F12 on the logged-in tab → Console → paste the
whole file → Enter. It starts immediately. If the page navigates, the script is
gone and you have to paste it again.

Either way it starts on its own. Console controls:

| Command | What it does |
| --- | --- |
| `UTBOT.status()` | attempts so far, time left, whether the error modal is up |
| `UTBOT.stop()` / `UTBOT.start()` | pause / resume |
| `UTBOT.inspect()` | table of every clickable element it can see — use this if it can't find a button |
| `UTBOT.testAlert()` | fire the success alert without waiting |
| `UTBOT.reset()` | clear saved state |

## What one attempt does

1. Dismiss the error modal if it's still up.
2. Click the `+` icon once (set quantity to 1). Falls back to typing into a
   quantity field if no plus icon is found.
3. Click **Find Best Available**.
4. Poll the page for up to 9 seconds:
   - "Seats Not Found" text appears → click **OK**, wait out the rest of the 20s, retry.
   - URL changes, or 9s pass with no error → **treat as a hit** and alert.

## The alert

Simultaneously:

- full-screen orange banner + tab title change
- ~4 seconds of loud alternating beeps
- desktop notification (grant permission when asked)
- opens a Gmail compose tab, pre-filled: to `taimoor.anwar11@gmail.com`,
  subject and body `TICKET FOUND`, plus the page URL

**One honest caveat on the email:** a browser will not let a page script press
Send in your Gmail tab for you — that's a hard security boundary, not a
limitation of this script. The compose window opens fully filled in and you hit
Send. If you want a genuinely hands-off email, set up the webhook below.

### Optional: hands-off email

[ntfy.sh](https://ntfy.sh) forwards a webhook to an email address for free with
no account:

1. Pick an unguessable topic name, e.g. `ut-tix-8f3k9q2x`.
2. Set `ntfyTopic: 'ut-tix-8f3k9q2x'` in the CONFIG block of the script.
3. Visit `https://ntfy.sh/ut-tix-8f3k9q2x` once and send yourself a test to
   confirm delivery to your inbox.

The email then sends itself the moment a ticket is found, and the Gmail compose
tab still opens as a backup. Note that this routes the notification through a
third-party service — the only content sent is "TICKET FOUND" and the page URL.

## "could not find the button" / "no plus icon"

Almost always the wrong tab. The script now refuses to run anywhere except
`*.evenue.net` and says so in red — if you see that, paste it into the console
of the FB26 tab rather than whatever tab is focused.

(The earlier version would happily run on any page, and on a page that merely
*displayed* the words "Seats Not Found" it would report a phantom error modal.
Error detection is now scoped to dialog-shaped containers first.)

## If it still can't find the buttons

The selectors are text-based (`"Find Best Available"`, `"OK"`, a literal `+`)
and search same-origin iframes too, but the page is behind a login so the exact
markup was never verified against the real DOM. If the console says
`could not find ...`, run `UTBOT.inspect()`, find the real element in the table,
and either add its label to `CONFIG.findButtonText` / `CONFIG.okButtonText` or
edit `findPlusButton()`.

Set `dryRun: true` in CONFIG to watch it narrate every step without clicking
anything — worth one cycle before you leave it unattended.

## Also worth knowing

Twenty-second polling against a ticketing site is a rate you should be
comfortable defending; evenue's terms may prohibit automated access, and
aggressive retries can get an account throttled or blocked. The interval is a
single number at the top of CONFIG if you want it gentler.
