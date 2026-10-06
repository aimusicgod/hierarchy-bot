# Hierarchy scout bot

A Discord server for scouts only. Scouts press buttons. You use an admin panel. Scouts only ever see their own 5%.

## First-time setup
1. Create the bot at discord.com/developers, copy the token, invite it with the `bot` and `applications.commands` scopes (permissions: View Channels, Send Messages, Embed Links, Attach Files).
2. Make a private admin-only `#review` channel and a `#scout-commands` channel for scouts. Copy the `#review` channel ID.
3. `python3 -m pip install -r requirements.txt`, copy `.env.example` to `.env`, fill it in, run `python3 bot.py`.
4. In `#scout-commands`, type `/setup`. It posts the **scout panel** there and the **admin panel** in `#review`. Run it once; the buttons keep working after restarts.

## What scouts do (all buttons)
- **Submit a prospect**: a pop-up asks for the Instagram username and link. Duplicates are blocked; first scout gets credit.
- **My submissions**: each prospect and its status (Pending, Effective or Terminated). They also get a DM whenever a status changes.
- **My earnings**: their 5%, by month and per creator.
- **W-9 / tax form**: opens your DocuSign form.

## What you do (admin panel in #review)
- **Import creators**: upload TikTok's *Manage creators* export (.csv or .xlsx). Each prospect's status simply mirrors TikTok's Relationship status:
  - **Effective** → Effective, and the creator is linked to the scout who found them so earnings are credited
  - **Terminated** → Terminated
  - Not in the file yet (or any other TikTok status) → stays **Pending**
  - How a TikTok creator is matched to a submission: (1) you linked them with **Onboard prospect**, (2) the TikTok and Instagram usernames are the same, or (3) the manager typed the Instagram name in TikTok's **Notes** field while onboarding, for example `IG: @janedoe` or `instagram.com/janedoe`. No double entry.
  - It follows TikTok both ways, so a creator who is later terminated, or comes back, updates on the next import.
- **Import earnings**: upload the monthly TikTok LIVE export. The scout's 5% of the Estimated bonus is added to the ledger. Creators who are **Terminated** earn no 5% (earnings already credited stay). Re-importing a month never double-pays.
- **Payouts**: what each scout is owed. The **Paid** button stays disabled until their W-9 is on file.
- **Onboard prospect**: the scout only gives an Instagram name, and the TikTok name is learned during onboarding. Pick the prospect from the list, then type their TikTok username and, if you have them, email and phone. Contact details are visible only to admins (the **Creators** list) and are never shown to scouts. They are stored in the database, so keep your **Backup** files private. The next creators import then updates them.
- **Scouts / Creators**: who is attached to whom. **Backup**: sends you a copy of the database.
- Each review card has Pending / Effective / Terminated buttons as a manual override.
- Slash commands (`/importcreators`, `/importearnings`, `/payouts`, `/linkcreator`, `/adjust`, `/w9`, `/w9assign`, `/setbenchmark`, `/checkin`, `/backup`) do the same things if you prefer typing.

## Automatic posts (no setup commands)
Put `WELCOME_CHANNEL_ID` (public welcome channel), `SCOUT_CHANNEL_ID` (scout commands channel) and `REVIEW_CHANNEL_ID` in `.env`. Each time the bot starts it posts the welcome message with the Apply button, the scout guide and scout panel, and the admin panel, if they aren't there yet. If they are, it **edits them in place**, so changing `welcome.py` and restarting updates the text with no new posts. The bot needs View Channel, Send Messages and Embed Links in each of those channels. `/setup`, `/setupapply` and `/setupguide` still exist as manual fallbacks, but you don't need them.

## FAQs, help, and payouts
- Create two channels visible only to the Scout role: **faqs** (read-only for scouts) and **help** (a Forum channel works best, or an ordinary text channel). Put their IDs in `.env` as `FAQ_CHANNEL_ID` and `HELP_CHANNEL_ID`. The bot posts the FAQs (from `welcome.py`) and keeps them updated. For a Forum channel it writes the rules into the channel topic (the bot needs Manage Channels there); for a text channel it posts them.
- **Paperwork** is one DocuSign packet per scout: the W-9 and a Direct Deposit Authorization signed together. When both are signed, run `/w9` for that scout. **Bank details stay in DocuSign. The bot never stores them.**
- **Export payouts** (admin panel, or `/exportpayouts`) gives a CSV of name and amount owed for a month, only for scouts whose paperwork is on file. It lists who was held back. Enter the amounts into your bank's ACH upload, matching names to the signed forms in DocuSign. Scouts are paid within 15 days after Hierarchy receives its own payment (see the agreement).

## Removing a scout
`/removescout scout:@name` ends someone's scouting: they lose the Scout role and can't submit new prospects, but their 5% continues on artists they already submitted while those artists stay Effective (this matches Section 10 of the agreement). Add `for_breach:True` if you remove them for breaking the agreement: the bot then credits them nothing for months imported after that, and earlier months stay payable.

## Scout applications (before anyone gets in)
1. In the scouts server create a role called **Scout**. Hide the scout commands channel from @everyone and show it only to the Scout role. Keep a welcome channel visible to everyone. Put the Scout role's ID in `.env` as `SCOUT_ROLE_ID`. The bot's own role must sit above the Scout role.
2. In the welcome channel run `/setupapply`. It posts a welcome message (what scouting is, how to join) and the **Apply to be a scout** button. In the scout commands channel run `/setupguide` (then `/setup`) to post the how-it-works and how-to-recruit guide above the scout buttons. All the wording is in `welcome.py`; edit it, restart, and post again.
3. A new person taps it, reads the **Scout Agreement** (`agreement.py`; `/agreement` shows it any time), checks the box to accept it, and enters full name, email and phone. Your `#review` card records which version they accepted and when. Every acceptance saves the full text, name, email, phone, Discord ID, time and version. **Export agreements** (admin panel or `/exportagreements`) gives a zip with one file per scout for your records. If you change the wording, change `VERSION` too. It's a template, not legal advice, so have an attorney review it. You see the application in `#review` with **Approve scout** and **Deny**. Approve gives them the Scout role and tells you to send their W-9; deny sends a polite DM (they can apply again).
4. People who submitted prospects before this existed are treated as already approved.

## W-9s (sent by hand through DocuSign)
When you approve a scout, `#review` reminds you to send the W-9 template in DocuSign to their email. When it's signed, run `/w9` for that scout. Payouts stay blocked until then. The scout's **W-9 / tax form** button doesn't send anything itself. It posts a **W-9 requested** note in `#review` with their email (once a day per scout at most) and tells the scout the form is coming from you. The automatic DocuSign link-back below is optional and needs a DocuSign plan with PowerForms and Connect.

## Approve / deny and the creator server gate
- Every submission lands in `#review` with **Approve** and **Deny** buttons. The scout gets a DM either way. A denied or not-yet-reviewed Instagram name can't be used to onboard.
- **Export approved** (admin panel, or `/exportapproved`) gives a one-column CSV of Instagram usernames for approved prospects that weren't in a previous export, to use as your DM list.
- Manager vetting happens in TikTok Backstage. When you import the Manage creators file, anyone TikTok shows as **Effective** gets the creator role in the creator server automatically (and loses it if **Terminated**). Set `CREATOR_ROLE_ID`; in the creator server hide every channel except the welcome/onboarding one from @everyone and show the rest only to the creator role. The bot's own role must sit above the creator role.

## Creator server and automatic manager assignment
Creators join a **separate Discord server**. The same bot runs in both servers, but the creator server gets only one admin command and one button, so creators never see scouts, earnings, or each other.

**How everything connects**
1. A scout submits the Instagram name. The creator DMs you (or you DM them) their **email, preferred date and time, and time zone**.
2. You tap **Schedule call** in the admin panel (or an Instagram DM tool sends it automatically, see below). The bot checks your managers' Google Calendars, picks whichever manager is free (rotating fairly), and Google emails the creator and the manager an invite with a Meet link.
3. The creator joins the creator server and taps **Complete onboarding**: Instagram username, TikTok username, email, phone.
4. The bot connects them: Instagram name → the scout's submission → scout; TikTok name → what the monthly TikTok files match on; **email → the booking → manager**. Booking and onboarding can happen in either order. The creator must use the **same email** for both.
5. You get a note in `#review` for each booking and onboarding. `/cancelcall email` removes a call. `/assignmanager` sets a manager by hand.

**Google Calendar setup (for automatic invites)**
1. Pick one Google account as the "organizer" (the invites come from it). At console.cloud.google.com create a project, enable the **Google Calendar API**.
2. APIs & Services → OAuth consent screen: External, add yourself as test user, then click **Publish app** (in "Testing" mode the login expires after 7 days). Credentials → Create credentials → OAuth client ID → **Desktop app** → download JSON, save as `client_secret.json` in this folder.
3. Run `python3 get_google_token.py`, sign in as the organizer account. It prints three values. Put them in `.env` / Railway as `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`. Also set `ORG_TIMEZONE` (default America/New_York).
4. **Each manager** opens Google Calendar → Settings → their calendar → "Share with specific people" → add the organizer account with **"See all event details"** (or at least free/busy). Without this their calendar counts as unavailable.
5. In Discord: `/addmanager email name` for each manager, then `/managers` to confirm every calendar says "readable".
6. `/schedulesettings` sets working hours (default Mon-Fri 9-6 in `ORG_TIMEZONE`) and call length (default 30 min).

**Instagram DM automation (optional)**: the bot can't read Instagram DMs. A tool such as ManyChat can collect email/date/time/time zone in the DM and POST to `https://YOUR-DOMAIN/intake` with header `X-Intake-Secret: <INTAKE_SECRET>` and JSON `{"email","name","date","time","timezone","instagram"}`. The reply JSON has `message` (including next open times if nobody is free) to show the creator. Without such a tool, just use the **Schedule call** button.

**Setup**
1. Create the creator server. Re-invite the bot to it with the same invite link as before.
2. Add to `.env` (and Railway): `CREATOR_GUILD_ID` (the creator server's ID) and optionally `CREATOR_ROLE_ID` (a role creators get after onboarding).
3. Restart the bot. In the creator server's welcome channel run `/setupcreators`.
4. *Optional alternative:* if you'd rather use Calendly or Cal.com than the built-in scheduler, pick one and point its webhook at your Railway domain (Settings → Networking → Generate Domain):
   - **Calendly**: needs a paid plan for webhooks and for round-robin team events. Webhooks are created through Calendly's API (`POST /webhook_subscriptions`, organization scope, events `invitee.created` and `invitee.canceled`, URL `https://YOUR-DOMAIN/calendly`, and a signing key). Put the key in `CALENDLY_SIGNING_KEY`.
   - **Cal.com**: create a team round-robin event, then Settings → Developer → Webhooks → new webhook, URL `https://YOUR-DOMAIN/calcom`, triggers Booking Created, Rescheduled, Cancelled, and a secret. Put the secret in `CALCOM_SECRET`.
   - Each manager connects their own calendar (Google, etc.) in the tool, so only open times are offered.
   - The booking form must ask for the creator's email.
5. The webhook handlers were built from each tool's published webhook format and tested with sample messages, but not against a live Calendly or Cal.com account. Do a test booking first.

## W-9s through DocuSign (traceable)
1. In DocuSign, create your W-9 template and turn it into a **PowerForm**. Add a text field labeled exactly `ScoutID` (no underscore). The bot fills it in for each scout.
2. Put the PowerForm link in `DOCUSIGN_POWERFORM_URL`.
3. In DocuSign **Connect**, add a configuration for the "envelope completed" event, JSON format, include recipient and tab data, and URL `https://YOUR-RAILWAY-DOMAIN/docusign`. Turn on **HMAC** and put the key in `DOCUSIGN_HMAC_KEY`.
4. In Railway, generate a public domain for the service (Settings → Networking).
5. When a scout finishes the form, DocuSign notifies the bot, the W-9 is marked on file, and the DocuSign envelope ID is saved with their record so you can trace it back to the signed document. If the bot can't tell which scout it was, it posts in `#review` and you run `/w9assign`.
The bot never sees the form's contents, only that it was completed. `/w9` still works as a manual fallback.

## Monthly routine
1. Download TikTok's *Manage creators* file → **Import creators**.
2. Download the monthly earnings file → **Import earnings** → **Payouts**.
3. Pay scouts outside Discord, then tap **Paid**. Tap **Backup** and save the file privately.

## Testing and hosting
- `python3 test_core.py` checks everything that doesn't need Discord, using `sample_*.csv` (fake data).
- Hosting on Railway: private GitHub repo, start command `python3 bot.py`, variables from `.env`, and a Volume mounted at `/data` with `DB_PATH=/data/hierarchy.db`.


`python3 test_scheduler.py` tests the scheduling logic with a fake calendar. It has not been run against live Google, so book a test call to yourself first.
