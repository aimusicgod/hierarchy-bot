# Creators server: setup guide

The creators server runs on the same bot and the same Railway project as the scouts/staff server. Nothing about money, scouts or admin work appears there.

## 1. Create the server and add the bot
1. In Discord, create a new server called "Hierarchy Music". **Server Settings → Enable Community** (needed for the #lessons forum and the office-hours stage).
2. Invite the same bot (Developer Portal → OAuth2 → URL generator → scopes `bot` + `applications.commands`, permission **Administrator**).
3. Copy the new server's ID (right-click server icon → Copy Server ID) and set `CREATOR_GUILD_ID` in Railway Variables. Redeploy.
4. Optional but recommended: Developer Portal → Bot → turn on **Server Members Intent**, then set `MEMBERS_INTENT=1`. This lets the bot give every new join the Pending role automatically. Without it, new joins see only what @everyone sees (the setup locks everything else, so they still can't get in).

## 2. Build it
In the creators server run **`/setup-server`**. It creates the roles (Pending, Member, Artist, Model, Admin, Manager, Module N Complete), all categories and channels with their permissions, AutoMod, slowmode, and posts the welcome, rules, academy, apply and studio panels. It is safe to run again; it updates instead of duplicating.
Then: give yourself the **Admin** role, give managers the **Manager** role, and drag the bot's role to the top of Server Settings → Roles.

## 3. Staff side (in the staff server)
Open the admin panel. New buttons:
- **Creator roster**: upload a CSV with columns `Handle`, `Full name`, `Email`, `Phone`, `Role` (Artist/Model). This is "what's on file" for auto-approval. The Import creators button (TikTok export) also feeds the managed-creator list.
- **Live stats**: upload the TikTok LIVE Backstage CSV. Column names are matched using the aliases in `nudge_rules.json`; edit them if your export uses different headings.
- **Run nudges** / **Weekly report**.
Review cards for applications that aren't auto-approved appear in your review channel with Approve / Deny. Check-in nudges appear there too (or in `NUDGE_CHANNEL_ID`) with Send / Skip. Studio replies appear there with Available / Suggest other time / Not available.
Commands: `/nudgeskip`, `/nudgeunskip`, `/setgoal`.

## 4. Text messages (Twilio)
1. Create a Twilio account and buy a number. In the US you must complete **A2P 10DLC registration** before texts are delivered reliably (brand + campaign; use "account verification and check-in messages"). Plan for a few days.
2. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`.
3. In Railway: Settings → Networking → **Generate Domain**. Set `PUBLIC_BASE_URL` to it (https, no trailing slash).
4. In Twilio, set the number's "A message comes in" webhook to `{PUBLIC_BASE_URL}/sms` (POST). Replies from creators are forwarded into their check-in channel. STOP is always honored.
Until Twilio is set up, verification codes can't be sent, so applicants can't finish verification.

## 5. Studio booking
1. Make a mailbox for bookings (e.g. bookings@yourdomain). For Gmail: turn on 2-step verification, create an **App password**, and set `MAIL_ADDRESS`, `MAIL_PASSWORD` (host settings default to Gmail).
2. Open `studios.json` and fill in each studio's rooms, rates, minimums, inclusions, "rates_as_of", booking contact email and referral wording, **using what the studios gave you**. Nothing is pre-filled.
3. In #book-studio nothing more is needed; `/setup-server` already posted the panel (or run `/setup-studio` in a channel).

## 6. Academy
Lessons are JSON files in `lessons/` (see `module1-welcome.json`). Add files, upload them to GitHub, then run `/publish-lesson` in the creators server. Re-running updates the same post.

## 7. Check-in rules and weekly prompts
Edit `nudge_rules.json` (rules, wording, goal defaults, run hour; `auto_send` stays `false` until you trust the wording) and `prompts.json` (Monday prompts).

## 8. Upload to GitHub
Upload every new/changed file from this folder to your private repo (drag and drop → Commit). Railway redeploys. Do not upload `.env`.

## What I could not test
The Discord layer (channel/permission building, buttons, forms) and live Twilio/mail delivery need your real server and accounts; the logic underneath (validation, codes, auto-approve rules, STOP, contact order, nudge guardrails, quizzes, booking limits) is covered by `python3 test_creators.py`. Have an attorney review the SMS consent wording and the scout agreement before launch.
