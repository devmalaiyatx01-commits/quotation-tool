# Setting Up Google Sign-In (Complete Beginner's Guide)

This is the one-time setup that makes "Sign in with Google" work for the Quotation Tool,
restricted to `@example.com` accounts. Everything in this document happens on Google's
own website (`console.cloud.google.com`) and then two values get pasted into this app's
configuration - there's no code to write, and every step is explained as it comes up.

**Time needed**: 10-15 minutes, done once (plus a couple minutes again later, if/when the
app moves to a real deployed URL - see [Part 5](#5-adding-more-redirect-uris-later)).

**What you'll end up with**: a login page that shows one button, "Sign in with Google" -
anyone who clicks it and signs in with a real `@example.com` Google account gets in
automatically; anyone else is turned away, even if they somehow reach the button.

---

## Table of contents

1. [Before you start](#1-before-you-start)
2. [Create a Google Cloud project](#2-create-a-google-cloud-project)
3. [Configure the OAuth consent screen](#3-configure-the-oauth-consent-screen)
4. [Create the OAuth Client ID](#4-create-the-oauth-client-id)
5. [Adding more redirect URIs later](#5-adding-more-redirect-uris-later)
6. [Set the two values this app needs](#6-set-the-two-values-this-app-needs)
7. [Test it](#7-test-it)
8. [Troubleshooting](#8-troubleshooting)
9. [Security notes](#9-security-notes)
10. [Glossary](#10-glossary)

---

## 1. Before you start

You need a Google account to sign into `console.cloud.google.com` with - any Google
account works for creating the project and OAuth client below, it doesn't have to be an
`@example.com` one, and **you don't need to switch away from `intern@example.com`
either** - Part 2 covers exactly how to make that account work cleanly, including what to
do if it hits a permissions wall.

You do **not** need to know what any of the following mean yet - explained the first time
they come up: OAuth, client ID, client secret, redirect URI, consent screen. There's a
[Glossary](#10-glossary) at the end too.

### Does the studio actually have Google Workspace?

Worth checking *before* Part 2, since it changes what you should expect to see there -
but nothing below is required reading if you just want to get started; skip to Part 2 and
come back here only if you hit a permissions error.

Receiving an `@example.com` Gmail address does **not** by itself mean the company has
real, paid Google Workspace set up. Google automatically creates a bare-bones
**"Cloud Identity Free"** organization the moment *any* `@example.com` account first
touches a Google service like Cloud Console - this happens completely automatically,
whether or not anyone at the company ever intentionally set up Workspace, and often with
literally nobody assigned as its administrator yet.

To check which situation you're actually in, sign in at **`https://admin.google.com`**
with your `intern@example.com` account and see what happens:

- **A real admin console** - existing user accounts, devices, billing, someone else
  already listed as admin - means real Workspace exists. If so, that person is who to ask
  before Part 2, the same way you'd ask any IT admin for a new tool's access.
- **"Become the administrator"**, or a mostly-empty setup screen, or an access-denied page
  - means nobody has claimed this yet; it's just the automatic free shell. This is the
  likely case if (like most people) you've never used any Workspace-specific feature
  (shared company calendar, centrally-managed devices, Google Groups, admin-provisioned
  accounts) - receiving your address from the company isn't the same as someone having
  configured Workspace.

**Either way, this doesn't block Google Sign-In from working for this app** - it only
affects which specific option you'll see in Part 2 (and, optionally, one extra setting in
Part 3). The actual security guarantee this whole document is for - **only real
`@example.com` accounts can sign in to the Quotation Tool** - is enforced by this app's
own code (`app/google_auth.py`), completely independent of Workspace, organizations, or
which Google account sets any of this up. Keep that in mind through Part 2 below if it
starts to feel like a bureaucratic maze - the end result is the same either way.

---

## 2. Create a Google Cloud project

A **project** is just a named container Google Cloud uses to group settings/credentials
together - this app only needs one.

1. Go to **https://console.cloud.google.com** and sign in (with `intern@example.com`,
   or whichever account you're using).
2. Click the project dropdown near the top-left (it might already say "Select a project"
   or show an existing project name).
3. Click **New Project**.
4. **Project name**: something recognizable, e.g. `Quotation Tool`.
5. **Location / Organization** - this is the field worth pausing on:
   - If it shows **"No organization"** as an option (either already selected, or pickable
     from a dropdown/"Browse" button) - **choose it explicitly.** This creates the
     project as your own, entirely outside `example.com`'s organization structure, so
     none of that organization's policies (or lack of anyone administering it - see the
     [Workspace check](#does-the-studio-actually-have-google-workspace) above) apply to
     it at all. This is the recommended choice for exactly the situation of "a
     company-domain account with nobody around to grant extra permissions," and it's
     still the real `intern@example.com` account doing the creating - nothing about
     Part 6 or how the app restricts sign-ins changes.
   - If it only offers **`example.com`** with no "No organization" choice, that
     organization's policies require every project to live inside it - leave it as-is and
     click Create; this usually still works fine for the account that's already a member.
6. Click **Create**. Wait a few seconds for it to finish, then make sure the new project
   is actually selected in that same top-left dropdown before continuing.

**If step 6 fails with a permissions error** (something like *"You need additional access
to the project"* or *"Missing or blocked permissions: resourcemanager.projects.get"*) -
this means the `example.com` organization exists (per the Workspace check above, likely
the automatic free shell, not something anyone configured on purpose) and its policies are
blocking your account from creating or accessing projects inside it. Try these in order:

1. **Go back to step 5 and explicitly pick "No organization"** if you didn't already -
   this alone resolves it for most people in this situation, since it sidesteps the
   organization's policies entirely rather than needing permission to act within them.
2. **If "No organization" genuinely isn't offered at all**, sign in at
   `https://admin.google.com` with the same account and look for a **"Become the
   administrator"** prompt (see the Workspace check above) - accepting it makes you the
   real admin of the organization, which then makes every permission problem below moot.
   It requires proving you control the `example.com` domain itself (a DNS record or a
   file uploaded to the company website), so this only works if you - or someone you can
   ask directly - has access to wherever `example.com` is registered/hosted.
3. **If neither applies**, someone else already has administrative control over this
   organization (even if they never intentionally set anything up) - worth asking around
   at the studio, starting with whoever manages the company's website or originally
   registered the domain, since that's very likely the same person, admin status or not.

None of this changes what the finished setup actually does: whichever path resolves it,
the app still only ever lets in real, verified `@example.com` accounts (Part 9) - this
is entirely about which Google account gets to hold the credentials, not about loosening
that restriction.

### Exactly what to ask that admin for

If step 3 above applies and you've found the right person, don't just forward them the
raw Google error - it tends to suggest an oddly-specific, wrong-for-the-job role (e.g.
**"Security Reviewer"**, a read-only audit role that only shows up because it happens to
include the one missing permission for *that particular screen*, not because it's suited
to what you actually need). Ask for one of these instead, easiest first:

- **Have them create the project and add you to it** (needs the least IAM knowledge on
  their end): they sign in, create a new project themselves (any name), then go to that
  project's **IAM & Admin → IAM → Grant Access**, add your `@example.com` address, and
  give it the **Owner** role. You then select that project yourself in Cloud Console and
  continue from Part 3 onward - nothing else about this guide changes.
- **Or have them let you create your own projects going forward**: with the
  **organization** (not a project) selected in the console's top-left switcher, **IAM &
  Admin → IAM → Grant Access**, your address, role **"Project Creator"**
  (`roles/resourcemanager.projectCreator`) - not Security Reviewer. One-time grant, no
  further admin involvement needed for this or future projects.

**A faster way to ask, if you already have an error page open**: Google's own **IAM Policy
Troubleshooter** diagnoses exactly this kind of "why can't this account do that" question -
on the error page, look for a **"Copy Troubleshooter URL"** link/button and send that link
to whoever you're asking for help, along with these steps:

1. They open the link and sign in with whichever account manages `example.com`.
2. The page shows the diagnostic (which account, which permission, which resource, why
   it's denied) and usually a **"Grant Access"** button right there in the results.
3. They add your `@example.com` address with role **Owner** (not whatever narrower role
   the page defaults to) and save.
4. You refresh your own Cloud Console and continue.

**If they hit their own "additional access" error trying to do step 2 or 3** - that means
they don't actually hold Owner/IAM-admin rights on that specific project either, and the
project itself is a dead end (not fixable through it). At that point the fix is to abandon
that project rather than keep troubleshooting it: have them create a **brand-new** project
themselves (they'll automatically be its Owner), then grant you Owner on that new one the
same way, and you continue from there instead.

A ready-to-send email covering all of this:

> **Subject: Access needed on Google Cloud (example.com) to set up sign-in for the Quotation Tool**
>
> Hi [Name],
>
> I'm setting up Google Sign-In for the Quotation Tool (the internal BOQ/quotation app), so
> that only real `@example.com` accounts can log in. To do that I need to create a small
> Google Cloud project under our domain, but I've hit a permissions wall I can't resolve
> myself.
>
> Google's own diagnostic tool shows exactly what's missing - could you open this link and
> take a look?
>
> [paste your Troubleshooter link here]
>
> If you're able to grant access, the simplest fix is:
>
> 1. Sign in to console.cloud.google.com.
> 2. Create a new project yourself (any name, e.g. "Quotation Tool").
> 3. In that project, go to IAM & Admin → IAM → Grant Access.
> 4. Add `intern@example.com` with the Owner role.
>
> That's the only access I need - once I have it, I can finish the setup myself from
> there, and it won't need anything further from you.
>
> If you're not able to make this change either, could you point me to whoever manages our
> Google Workspace/domain? A couple of my attempts have hit organization-level restrictions
> that seem to need someone with admin rights at that level.
>
> Thanks,
> [Your name]

**If none of this resolves it within a reasonable amount of back-and-forth**: stop spending
more time on the `example.com` organization and use a personal Google account for just
this one project instead (sign out, or use an incognito window, and start Part 2 fresh with
a personal Gmail or a fresh throwaway account). This is not a lesser or less secure setup -
the account that owns the Google Cloud project has no bearing on who can sign into the
finished app; that restriction is enforced entirely by this app's own code (`is_allowed_email()`
in `app/google_auth.py`), checked on every sign-in regardless of which account created the
OAuth client behind the scenes. Three or more distinct organization-permission failures
(project access denied, org IAM view denied, a stuck/hung project creation, etc.) is a
reasonable point to stop pushing on this path - it usually means more than "nobody's
claimed admin yet" is going on, and isn't worth the time for a one-time setup task.

---

## 3. Configure the OAuth consent screen

The **consent screen** is the "Sign in with Google" permission screen a user sees before
they're sent back to this app - Google requires you to fill in some basic details about
your app before it'll let anyone use it.

1. In the left-hand menu (☰), go to **APIs & Services** → **OAuth consent screen**.
2. **User type**:
   - If a banner or option offers **Internal** (only appears if you're signed in with an
     account inside the studio's own *real, configured* Google Workspace - see [the
     Workspace check](#does-the-studio-actually-have-google-workspace) in Part 1; if you
     picked "No organization" back in Part 2, or the check turned up the free/unclaimed
     Cloud Identity shell rather than real Workspace, you won't see this option, and
     that's expected, not a problem) - picking this means **Google itself** only ever
     lets `@example.com` accounts see this app at all, on top of what this app already
     checks on its own. Nice extra layer if it's available, not required either way - skip
     to the next step if you don't see it.
   - Otherwise pick **External** - this is completely normal and fine; it does not mean
     the app becomes public. "External" just means the option isn't Workspace-only from
     Google's side, since this app's own code (`app/google_auth.py`) is what actually
     enforces the `@example.com` restriction regardless of which option you pick here.
3. Fill in the required fields: **App name** (e.g. `Quotation Tool`), **User support
   email** (pick your own or a shared one), **Developer contact email** (same is fine).
   Leave everything else on its default.
4. Click through **Save and Continue** on the Scopes and Test Users pages without
   changing anything - this app only ever asks for `openid`, `email`, and `profile`
   (your basic identity), which are already included by default and don't need to be
   added by hand.
5. If you picked **External** above, you'll land on a summary showing the app in
   **"Testing"** mode. That's fine to leave as-is for now - a "Testing" app works
   normally for accounts you explicitly add as test users (Part 8 has the fix if this
   trips someone up), or you can click **Publish App** to make it available to anyone
   with a Google account (your own domain check in this app still only lets
   `@example.com` addresses actually log in either way).

---

## 4. Create the OAuth Client ID

This is the actual credential pair - a **client ID** (not secret, safe to be visible) and
a **client secret** (must stay private) - that lets this specific app talk to Google.

1. Still under **APIs & Services**, go to **Credentials**.
2. Click **+ Create Credentials** → **OAuth client ID**.
3. **Application type**: **Web application**.
4. **Name**: anything, e.g. `Quotation Tool - Web`.
5. Under **Authorized redirect URIs**, click **+ Add URI** and add exactly:
   ```
   http://127.0.0.1:8000/auth/google/callback
   ```
   This has to match **exactly** what the app sends Google (scheme, host, port, and path
   all included) - this is the address for how you run the app today (`run.bat`, port
   8000). If you'll also test using `run_dev.bat` (port 8001), add a second URI the same
   way:
   ```
   http://127.0.0.1:8001/auth/google/callback
   ```
6. Click **Create**. A popup shows your **Client ID** and **Client secret** - copy both
   somewhere safe for a moment (you can always come back to this screen later too - go to
   Credentials, click the client's name, the values are shown there again).

---

## 5. Adding more redirect URIs later

**Whenever this app's address changes** (moving to a real server, getting a real domain,
Oracle Cloud, etc. - see `deployment.md`/`oracleDep.md`), Google Sign-In needs a matching
redirect URI added, or it'll fail with `redirect_uri_mismatch` (see
[Troubleshooting](#8-troubleshooting)):

1. Google Cloud Console → **APIs & Services** → **Credentials** → click your OAuth
   client's name.
2. Under **Authorized redirect URIs**, click **+ Add URI**, add the new address with
   `/auth/google/callback` on the end (e.g. `https://quotes.yourcompany.com/auth/google/callback`).
3. **Save**. Takes effect within a few minutes, no restart needed on Google's side. You
   can keep the old URIs in the list too - having several doesn't cause any problem, only
   an exact non-match does.

---

## 6. Set the two values this app needs

Same pattern as `DATABASE_URL`/`SESSION_SECRET` (see `HOW_TO_RUN.md`) - set as this
Windows user's **persistent environment variables**, never written into any file this
project's git history tracks.

Open **PowerShell** and run (replace the placeholders with your real values from Part 4):

```powershell
[Environment]::SetEnvironmentVariable('GOOGLE_CLIENT_ID', 'paste-your-client-id-here', 'User')
[Environment]::SetEnvironmentVariable('GOOGLE_CLIENT_SECRET', 'paste-your-client-secret-here', 'User')
```

Close and reopen any terminal window you use to run the app (`run.bat`/`run_dev.bat`) so
it picks up the new values - environment variables set this way only apply to *new*
terminal windows, not ones already open.

**Going live for the team** (`setup_production_services.ps1`)? It already checks for
these two values and carries them into `.env.production` automatically if they're set -
see that script's own comments. Nothing extra to do there beyond setting the two values
above before running it.

---

## 7. Test it

1. Start the app (`run.bat`, or `run_dev.bat` if you added the `:8001` redirect URI in
   Part 4).
2. Visit the app in a browser - the login page should now show a single **"Sign in with
   Google"** button instead of the old email/password form.
3. Click it, sign in with a real `@example.com` Google account.
4. You should land back in the app, signed in. First time signing in with any given
   `@example.com` address automatically creates an account for it (as a regular
   "member" - see `README.md`'s Accounts section for how to promote someone to admin
   afterward).
5. Try it once with a non-`@example.com` Google account (a personal Gmail, for
   instance) to confirm it's correctly turned away with "That Google account isn't an
   @example.com address."

---

## 8. Troubleshooting

**"You need additional access to the project" / "Missing or blocked permissions:
resourcemanager.projects.get" while creating or opening a project**
This is an organization permissions problem, not anything specific to this app - see
[Part 2](#2-create-a-google-cloud-project)'s own troubleshooting steps (picking "No
organization," becoming the organization's administrator, or finding whoever already
controls it) for the full walkthrough.

**`redirect_uri_mismatch` error from Google**
The address the app sent Google doesn't exactly match anything in Part 4's list. Check
you're running the app on the port you registered (8000 vs 8001), and that the URI in
Google Cloud Console has no typo (including `http` vs `https`, and no trailing slash).

**"Access blocked: this app's request is invalid" / "App not verified"**
Normal for a freshly-created **External** app still in "Testing" mode (Part 3, step 5) -
Google only lets you sign in with accounts explicitly added as test users while in that
mode. Fix: OAuth consent screen → **Audience** (or **Test users**, depending on Google's
current UI) → add the specific Google account(s) you want to test with, or click
**Publish App** to lift the restriction entirely (this app's own `@example.com` check
still applies either way, so publishing doesn't open it up to the public).

**Login page still shows the old email/password form**
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` aren't both set yet, or the terminal running the
app was opened before you set them (see Part 6 - close and reopen it). Confirm with:
```powershell
[Environment]::GetEnvironmentVariable('GOOGLE_CLIENT_ID', 'User')
[Environment]::GetEnvironmentVariable('GOOGLE_CLIENT_SECRET', 'User')
```

**Signed in fine, but with the wrong role (member instead of admin)**
Expected for a brand-new address - every first-time Google sign-in starts as "member".
Promote with `python create_user.py` using that same email (password field can be left
as anything; it's not used once Google Sign-In is active - only the role matters here).

---

## 9. Security notes

- The **client secret** from Part 4 is a real credential - treat it like a password.
  Never paste it into a file this project's git history tracks; the environment-variable
  approach in Part 6 is specifically to avoid that.
- The `@example.com` domain restriction is enforced by this app's own code
  (`app/google_auth.py`'s `is_allowed_email()`), not just by Google Cloud Console
  settings - so it holds regardless of whether the consent screen is Internal or
  External, Testing or Published.
- Revoking someone's access: since accounts are matched by email, deleting their row from
  the `users` table (or asking an admin to do so) stops them from being able to sign in
  again, even though Google itself would still let them attempt to.

---

## 10. Glossary

- **OAuth** - the standard way one app ("Quotation Tool") lets a user prove who they are
  via a second app ("Google") they already trust, without ever handling that user's
  Google password directly.
- **Client ID / Client secret** - the two-part credential Google issues to *this specific
  app* so it can participate in that OAuth flow. The ID identifies the app (not secret);
  the secret proves it's really this app asking (must stay private).
- **Redirect URI** - the exact web address Google sends the user back to once they've
  signed in - has to be pre-registered so a malicious app can't claim to be this one and
  intercept the sign-in.
- **Consent screen** - the permissions page Google shows the user before sending them
  back - what it says about the app (name, contact email) comes from Part 3.
- **Scope** - what information the app is asking Google's permission to see - this app
  only ever asks for `openid`/`email`/`profile` (basically: "who is this"), never anything
  like Gmail or Drive access.
- **Organization (Google Cloud)** - a container above projects that groups everything
  under one company domain, with its own permission policies. Created automatically the
  first time any account on that domain touches Google Cloud - see [the Workspace
  check](#does-the-studio-actually-have-google-workspace) - not something that only
  exists if a company deliberately set it up.
- **Cloud Identity (Free)** - the bare-bones, no-cost version of what backs a Google Cloud
  organization when nobody's paid for or configured real Google Workspace. Same
  organization mechanics, just with nobody assigned as administrator until someone claims
  it (Part 2's troubleshooting steps).
