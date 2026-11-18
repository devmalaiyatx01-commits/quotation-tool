# Context pack: the front end

Read this when you are changing anything people see: `static/app.js`, `static/style.css`, `templates/`.

## Where things are

- **One file, one page.** `static/app.js` (~23,000 lines) is the whole app: no framework, no build
  step. It is split by banners (`// ---------------- Library ----------------`), about 66 of them.
  The map: `python scripts/maintenance/code_map.py static/app.js --sections`, then
  `--grep <section or function>`.
- **Tabs render into one page.** Each tab has a renderer (`static/app.js::renderLibrary`,
  `static/app.js::renderEditor` for a BOQ, `static/app.js::renderMatch`, `static/app.js::renderNpd`).
  Renderers are async and take a `generation`. After every `await` they ask
  `static/app.js::renderSuperseded` and stop if a newer render has started; without that, a slow
  load writes into markup the next tab has already replaced. A renderer called from somewhere other
  than the tab switch claims a new generation (see the comment under `static/app.js::renderGeneration`).
- **Talking to the server**: always `static/app.js::api`. It turns "no reply" and Caddy's
  502/503/504 into one clear message (the worker died or restarted) and reads the app's JSON errors.
  Never call `fetch` directly for the app's own API.
- **Permissions on screen**: `static/app.js::can` reads the list the server put in the page. An
  element marked `data-needs="<permission>"` is removed for anyone who lacks it, wherever it is
  drawn. That only decides what is *offered*. The server's guard decides what is *allowed*.
- **Escaping**: every value put into HTML goes through `static/app.js::escapeHtml`, or
  `static/app.js::attr` inside an attribute, including names, codes and file names. Values that
  are HTML this code built (`rowsHtml`) are the only exception, and should be named like it.
- **Messages**: `static/app.js::showToast` for an answer that leaves by itself; an `error-box` for
  something the person must act on.
- **Styles**: `static/style.css`, the "Aperture" design system. Tokens (`--ink`, `--paper`,
  `--brown`, `--moss`, `--danger`) sit at the top, and a new colour uses one. The small-screen
  rules are in one section at the end ("Small screens: phones, tablets and narrow windows").

## Rules that bite here

- **Two top-level functions with the same name**: JavaScript silently keeps the last one.
  `verify_repo.py` (and so the gate) fails on it. It happened on 2026-09-21.
- **A brand's name in a condition** (`if (s.make === "Duragreen")`) is a special case. The server
  should say what the product can do, and the screen should show that. The gate counts them
  (9 in app.js on 2026-11-02).
- **Desktop must keep working exactly as before** when small screens change, and the reverse.
- **Prices are never computed in the browser.** The Configurator and the server do it. The screen
  shows what they send.

## How to check a change here

- Test the way a person acts (memory "UI tests use real input"): Playwright `click`, `type`,
  `mouse.wheel` on what is visible, then check what is on screen (`getBoundingClientRect` against
  the window). Setting `.value` or `scrollTop` proves nothing: a test that did passed 13/13 while
  people could not scroll.
- Sizes: desktop 1366x768, 1536x864, 1280x720, 1920x1080, plus 1093x614 and 1024x576 (125% zoom
  laptops). Phones and tablets: see `.claude/agents/mobile-user-tester.md`.
- The agents: `feature-tester` for the acceptance criteria, `mobile-user-tester` and
  `ui-ux-reviewer` for any visible change.
- `python scripts/maintenance/gate.py` lists new `${...}` in HTML that are not visibly escaped.
  Answer each one.
