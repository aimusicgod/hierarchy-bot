"""Everything the bot says in the creators server, in one place so you can edit the wording."""

APPLY_TITLE = "Apply to join Hierarchy Music"
APPLY_TEXT = (
    "Hierarchy Music is an invite-only network of vetted artists and models. "
    "Choose the button that fits you and fill in five quick details.\n\n"
    "**What happens next**\n"
    "1. Fill in the form.\n"
    "2. We text you a 6-digit code to confirm your number. Enter it here.\n"
    "3. If your details match our records, you're in right away. If not, the team reviews your application "
    "and you'll hear back here.\n\n"
    "**Why we ask for your mobile number**\n"
    "To verify your account and to send you account and check-in messages. Message frequency varies. "
    "Message and data rates may apply. Reply **STOP** at any time to stop texts. You'll still be reached by Discord, "
    "email and phone. Reply **HELP** for help.\n\n"
    "Your details stay private. They are never posted in public channels."
)
APPLY_FOOTER = "Invite-only. Applying doesn't guarantee a place."

WELCOME_TITLE = "Welcome to Hierarchy Music"
WELCOME_TEXT = (
    "This is the home of the Hierarchy Music creator network: a private community for vetted artists and models.\n\n"
    "**Inside, you'll find**\n"
    "• A community to meet and collaborate with other creators\n"
    "• The Hierarchy Academy: short lessons with quizzes and badges\n"
    "• Weekly check-ins and goals, with support from the team\n"
    "• For artists: help finding and booking recording studios\n\n"
    "**How to join**\n"
    "Head to **#apply**, choose Artist or Model, and follow the steps. It takes about two minutes."
)

RULES_TITLE = "Server rules"
RULES_TEXT = (
    "**1. Respect everyone.** Be kind. No harassment, hate or bullying.\n"
    "**2. No spam.** No unrequested promotion, repeated posts or mass tagging.\n"
    "**3. Protect privacy.** Never share another member's personal information, including phone numbers, emails or addresses.\n"
    "**4. Keep it inside the network.** Don't share this server's content, links or conversations outside of it.\n"
    "**5. Be honest and supportive.** Give feedback that helps, and celebrate each other's wins.\n\n"
    "The team may remove messages or members who break these rules. Questions? Ask in #ask-for-help."
)

ACADEMY_TITLE = "Welcome to the Hierarchy Academy"
ACADEMY_TEXT = (
    "Short lessons to help you grow as a creator.\n\n"
    "**How it works**\n"
    "1. Open **#lessons**. Each lesson is its own post.\n"
    "2. Watch the video, do the checklist and try the assignment.\n"
    "3. Tap **Mark complete**, then **Take quiz**.\n"
    "4. Pass with **70%** or more. If you miss it, you can retake the quiz after an hour.\n"
    "5. Passing earns you a **Module Complete** role and unlocks the next module. Your win is celebrated in #wins.\n\n"
    "Talk about a lesson in its thread. Need help? Ask in **#ask-for-help**, or join **#office-hours**.\n"
    "Type **/progress** any time to see how far you've come."
)

STUDIO_TITLE = "Book a studio session"
STUDIO_INTRO = ("Pick a room below, then tell us your preferred dates, session length and any engineer or gear needs. "
                "We'll reach out to the studio, and you'll hear back here.\n\n"
                "Both studios are in the Los Angeles area. Travel and lodging are up to you; if you can't make it to LA, "
                "you can skip this one.")

FAQ_TITLE = "FAQs"
# (question, answer). Edit freely; run /setup-server afterward and the post updates in place. Keep answers to things that are true.
FAQS = [
    ("I changed my TikTok or Instagram username. What do I do?",
     "Go to #support and tap Change my TikTok username or Change my Instagram username. Type the new one and we'll update your account. "
     "Your old username stays on file so your stats and history stay connected."),
    ("What is the unlock quota?",
     "New members start with a small part of the server. Once you reach the LIVE-days goal, the whole community and the academy open up "
     "automatically. Use /progress to see how close you are. Need help? Ask in #support."),
    ("Who can join?",
     "Hierarchy Music is an invite-only network of vetted artists and models. Apply in #apply by choosing Apply as Artist or Apply as Model."),
    ("Why do you need my mobile number?",
     "To verify your account and to send account and check-in messages. Message frequency varies and message and data rates may apply. "
     "Reply STOP at any time to stop texts. You'll still be reached by Discord, email and phone. Reply HELP for help."),
    ("I didn't get my code.",
     "Codes are valid for 10 minutes and you have 5 tries. Tap Send a new code if you need another (up to 3 per application), or I have a code if you closed the window. "
     "Check that your number was entered correctly. Still stuck? Write in #support, but don't post your phone number or email there."),
    ("How long does approval take?",
     "If your details match what we have on file, you're approved right away. Otherwise a team member reviews your application, "
     "and you'll get a message here on Discord when it's decided."),
    ("Who can see my information?",
     "Your phone number and email are kept private. They are never posted in public channels, and only the Hierarchy Music team can see them."),
    ("What is my private check-in channel?",
     "When you're approved you get a private channel that starts with checkin-. Only you and the Hierarchy Music team can see it. "
     "Check-ins and replies to our texts show up there."),
    ("How do the academy lessons and quizzes work?",
     "Each lesson is a post in #lessons. Tap Mark complete or Take quiz. You need 70% to pass, and if you miss it you can retake the quiz after an hour. "
     "Passing a module earns you a role and unlocks the next one. Type /progress to see where you are."),
    ("How does studio booking work? (artists)",
     "Pick a room in #book-studio and send your preferred dates. We contact the studio and share their reply with you. "
     "Hierarchy Music is making an introduction only. Booking, payment and terms are between you and the studio, you are responsible for paying "
     "the studio, and Hierarchy Music is not responsible for unpaid bills. Rates may change. You can have up to 2 open requests at a time."),
    ("Where do I ask for help?",
     "Members: #ask-for-help. Applicants who aren't in yet: #support. Please keep personal details out of public channels."),
]

UNLOCK_TITLE = "Unlock the full community"


def unlock_text(r):
    need = f"**{r['min_valid_days']} valid LIVE days**" + (f" and **{r['min_hours']} hours**" if r.get("min_hours") else "")
    return ("Welcome! You're in. To keep the community active and focused on creators who are going LIVE, the rest of the server and "
            f"the Hierarchy Academy open up once you reach {need}.\n\n"
            f"You have **{r['window_days']} days** from joining. Use /progress any time to see where you are. When you reach it, "
            "the roles are added automatically and you'll get a message.\n\n"
            "Stuck, or something got in the way? Message us in #support and a manager will help. "
            "Until then you can introduce yourself, share when you're going LIVE, and use your private check-in channel.")


SUPPORT_TOPIC = "Questions about applying? Ask here. Please don't post your phone number or email."
FAQS_TOPIC = "Answers to common questions."

WEEKLY_GOALS_TOPIC = "Post your goal for the week here. Your private check-in channel is separate."

STAFF_NOTES = {
    "staff-approvals": ("Approvals (admins)", "Things that need an admin's decision: new applications, check-in messages to approve, "
                        "username changes, studio requests. Nothing goes to a creator until someone taps Approve or Send here."),
    "staff-alerts": ("Alerts (admins and managers)", "Things a manager should act on: 📞 creators who haven't gone LIVE in 7+ days, creators who "
                     "can't reach the monthly minimum, and ⏰ people past their 30-day unlock window. Tap **Contacted** once you've reached them."),
    "staff-reports": ("Reports (admins)", "The monthly goals report (who is on pace, behind, or can't reach the minimum), the active-creator "
                      "percentage and the top 10. It posts every Monday after you upload the TikTok file, or tap Weekly report in the admin panel. "
                      "Manager results are in here, so this one is admins only."),
    "staff-backups": ("Backups (admins)", "A copy of the whole database is posted here every night. It contains creator contact details, so keep this channel private. The newest 14 are kept."),
}

TOPICS = {
    "staff-approvals": "Admins: applications and messages waiting for a decision.",
    "staff-alerts": "Admins and managers: people to call or reach out to.",
    "staff-reports": "Admins: monthly goals, manager and top 10 reports.",
    "staff-backups": "Admins: nightly database backups. Private.",
    "welcome": "Start here.",
    "apply": "Apply as an Artist or Model.",
    "faqs": FAQS_TOPIC,
    "support": SUPPORT_TOPIC,
    "unlock-quota": "How to unlock the full community and the academy.",
    "announcements": "News from the Hierarchy Music team.",
    "rules": "Server rules.",
    "introductions": "Say hello.",
    "creator-lounge": "Network-wide chat.",
    "wins": "Celebrate wins, big and small.",
    "go-live-schedule": "Share when you're going LIVE so others can join.",
    "collab-board": "Looking to collaborate? Post it here.",
    "opportunities": "Opportunities shared by the team.",
    "academy-start": "How the academy works.",
    "lessons": "One post per lesson. Discuss in the thread.",
    "module-discussion": "Talk about the modules.",
    "resources": "Helpful links and tools.",
    "ask-for-help": "Ask anything.",
    "artist-lounge": "Artists only.",
    "song-feedback": "Share a song and give feedback.",
    "release-plans": "Plan your releases.",
    "book-studio": "Request a studio session.",
    "model-lounge": "Models only.",
    "shoot-feedback": "Share shoots and give feedback.",
    "bookings": "Model bookings and opportunities.",
    "weekly-goals": WEEKLY_GOALS_TOPIC,
}


def welcome_dm(first, kind, quota=None):
    """quota: the unlock rules dict when new members must earn the rest of the server, else None."""
    if quota:
        goal = f"{quota['min_valid_days']} valid LIVE days" + (f" and {quota['min_hours']} hours" if quota.get("min_hours") else "")
        third = (f"3. Go LIVE! Reach {goal} within {quota['window_days']} days and the whole community and the academy unlock "
                 "automatically. Use /progress to check.\n\n")
    else:
        third = "3. Start the academy in #academy-start\n\n"
    return (f"Welcome to Hierarchy Music, {first}! You're in as an **{kind.capitalize()}**. 🎉\n\n"
            "Here's what to do next:\n"
            "1. Say hello in #introductions\n"
            "2. Post your weekly goal in #weekly-goals\n"
            + third +
            "You also have a private check-in channel just for you and the team.")


def denied_dm(first):
    return (f"Hi {first}, thank you for applying to Hierarchy Music. We weren't able to approve your application right now. "
            "If you think we got this wrong, please message the team and we'll take another look.")


def review_dm(first):
    return (f"Thanks, {first}! Your number is verified and your application is with the team. "
            "You'll get a message here as soon as it's reviewed.")
