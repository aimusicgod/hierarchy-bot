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

WEEKLY_GOALS_TOPIC = "Post your goal for the week here. Your private check-in channel is separate."

TOPICS = {
    "welcome": "Start here.",
    "apply": "Apply as an Artist or Model.",
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


def welcome_dm(first, kind):
    return (f"Welcome to Hierarchy Music, {first}! You're in as an **{kind.capitalize()}**. 🎉\n\n"
            "Here's what to do next:\n"
            "1. Say hello in #introductions\n"
            "2. Post your weekly goal in #weekly-goals\n"
            "3. Start the academy in #academy-start\n\n"
            "You also have a private check-in channel just for you and the team.")


def denied_dm(first):
    return (f"Hi {first}, thank you for applying to Hierarchy Music. We weren't able to approve your application right now. "
            "If you think we got this wrong, please message the team and we'll take another look.")


def review_dm(first):
    return (f"Thanks, {first}! Your number is verified and your application is with the team. "
            "You'll get a message here as soon as it's reviewed.")
