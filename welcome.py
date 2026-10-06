"""The text of the welcome and scout-guide posts. Edit the words here, then restart the bot
and run /setupapply (welcome channel) or /setupguide (scout channel) again to post the new version."""

WELCOME = {
    "title": "Welcome to Hierarchy Scouts",
    "intro": ("Hierarchy Music is an official TikTok LIVE partner. As a scout you introduce creators to us and build "
              "**residual income, paid monthly, over time**. Every month a creator you brought in is signed with us, active and "
              "earning, you earn **5% of their monthly TikTok LIVE bonus** (TikTok's \"Estimated bonus\"). "
              "The more creators you recruit who stay active, the more that monthly income can grow."),
    "sections": [
        ("How to join",
         "1. Tap **Apply to be a scout** below.\n"
         "2. Read the Scout Agreement, then enter your full name, email and phone number.\n"
         "3. Check the box to accept the agreement. It is legally binding once you submit.\n"
         "4. We review it. You'll get a DM when you're approved and the scout channels unlock.\n"
         "5. We email you a W-9 and Direct Deposit form to sign. You need both on file before any payout."),
        ("What you can earn",
         "This is residual income: it keeps coming each month for as long as your creators stay active and keep earning, "
         "and it can build over time as you add more creators. Your monthly earnings depend on two things: **how much your creators earn on LIVE** (their volume) and "
         "**how many of the creators you submit are successfully signed and active**. More active creators, "
         "and more LIVE volume from them, means a bigger 5%. There's no set amount. **You only earn if a creator you submit actually starts generating revenue.** The guide shows an example."),
        ("Good to know",
         "You're an independent scout, not an employee, and you're responsible for your own taxes. "
         "Your information stays private. Other scouts never see it."),
    ],
}

GUIDE = [
    {
        "title": "How the scout tools work",
        "sections": [
            ("The buttons (scout panel below)",
             "**Submit a prospect**: send us an artist's Instagram username and profile link.\n"
             "**My submissions**: see where each of your prospects stands.\n"
             "**My earnings**: your 5% by month. Only you can see this.\n"
             "**W-9 / tax form**: check whether your paperwork is on file."),
            ("What happens after you submit",
             "1. We review the prospect and **approve or deny** it. You get a DM either way.\n"
             "2. If approved, we take it from there and reach out to the artist.\n"
             "3. Once Hierarchy Music confirms the artist as **Effective**, your 5% starts."),
            ("Statuses",
             "**In review**: we haven't decided yet.\n"
             "**Not moving forward**: we passed on this one.\n"
             "**Approved, awaiting confirmation**: we approved it and are working on it. Hierarchy Music hasn't confirmed them yet.\n"
             "**Effective**: confirmed by Hierarchy Music as signed and active. You earn your 5% in months they generate revenue.\n"
             "**Terminated**: Hierarchy Music has confirmed they are no longer with us. No new 5% is earned after this. Earnings already credited stay."),
        ],
    },
    {
        "title": "Who we're looking for",
        "sections": [
            ("The artists",
             "Young artists, **18 and older**, **male or female**, in **any genre**: singers, rappers, producers, DJs and instrumentalists."),
            ("The rules for every submission",
             "- They must have an ad running on Instagram **right now**, promoting their music. Only submit artists you've seen a current ad from. No ad, no submission.\n"
             "- They must have **750K followers or fewer**. Smaller accounts are better.\n"
             "- The **sweet spot is 1K to 20K followers**."),
            ("What makes a good prospect",
             "- **Aesthetically pleasing content.** Their page and videos look good and feel intentional.\n"
             "- **The music sounds good.** Listen before you submit.\n"
             "- **They have the artist look.** If they don't fit that image but have something special and you believe in them, **submit them anyway**. You never know.\n"
             "- Real, active artists posting their own music. No big labels or brands.\n"
             "- If you can't tell whether they're 18 or older, don't submit them."),
        ],
    },
    {
        "title": "How to find artists",
        "sections": [
            ("Step by step",
             "1. **Scroll your Instagram feed.**\n"
             "2. Look for posts marked **\"Ad\"** from new artists.\n"
             "3. **Tap on the ad and watch it.** This sets off a spiderweb effect. Instagram starts flooding your feed with similar ads from other new artists releasing music, so one good click leads to many more.\n"
             "4. Check their follower count on their profile, to make sure they're within range.\n"
             "5. Open their profile, tap the three dots (or Share), and choose **Copy link**.\n"
             "6. In the scout channel, tap **Submit a prospect** and paste their username and profile link.\n"
             "That's it. You don't message them. We reach out."),
            ("Tips",
             "- **First to submit gets credit.** The bot tells you if someone already submitted that artist.\n"
             "- Use the exact username from their profile.\n"
             "- Submit real artists only, and only once each.\n"
             "- Never promise an artist any earnings or results."),
        ],
    },
    {
        "title": "Train your Instagram to show you more artists",
        "sections": [
            ("Step 1: Set your interests with \"Your Algorithm\"",
             "1. Open Instagram and go to your **Reels**, **Explore page** or **main feed**.\n"
             "2. Tap the **slider icon with two hearts** in the top right corner.\n"
             "3. A panel opens showing the interests Instagram thinks you have.\n"
             "4. Add topics you want more of. Choose music-related ones such as music, new music, independent artists, hip hop, R&B and pop. You can also search for a topic.\n"
             "5. Remove or lower topics you don't want.\n"
             "6. A change in one place carries over to Explore and your main feed.\n"
             "If you don't see the icon yet, Instagram hasn't rolled it out to your account. Go to Step 2."),
            ("Step 2: Set your ad topics",
             "1. Tap your **profile picture**, then the **menu icon (three lines)** at the top right.\n"
             "2. Tap **Settings and privacy**, then **Accounts Center**.\n"
             "3. Under Account settings, tap **Ad preferences**, then **Ad topics**.\n"
             "4. Tap **View and manage topics**. On Android, tap **See all** first.\n"
             "5. Search for music-related topics. Choose **See more** on the ones you want, or switch a topic from \"See less\" back to **No preference**.\n"
             "6. For topics that have nothing to do with music, tap **See less**.\n"
             "Instagram changes its menus often, so the wording on your screen may differ a little."),
            ("Step 3: Keep training it every day",
             "- **Follow Apple Music, Spotify and Billboard** on Instagram.\n"
             "- **Tap into every ad** from a new artist and watch it all the way through. This is what sets off the spiderweb effect.\n"
             "- **Like, save and follow** new artists, and watch their videos to the end.\n"
             "- On an unrelated ad, tap the three dots and say you're not interested.\n"
             "- **Optional:** Consider a separate Instagram account just for scouting, so your personal feed stays your own.\n"
             "- Give it a few days. Your feed won't change overnight."),
        ],
    },
    {
        "title": "Your earnings and getting paid",
        "sections": [
            ("Example (illustration only)",
             "**You only earn if an artist starts generating revenue.** Being signed alone earns you nothing.\n"
             "If an artist earns **$1,000** in a month, you earn **5%**, which is **$50**.\n"
             "If 10 of your artists are active and average $1,000 each, your 5% that month is **$500**.\n"
             "**If an artist is terminated, you no longer earn anything from them** for the months after. "
             "Amounts already credited for earlier months stay yours.\n"
             "Some earn more, some less, and some may not sign. "
             "**These numbers only show how the math works. They are not a promise, a guarantee, or a typical result.** "
             "What you earn depends on how much your artists bring in on LIVE (their volume) and how many you successfully recruit."),
            ("Getting paid",
             "Each month, your 5% shows under **My earnings**. You're paid by ACH to your bank account, "
             "**within 15 days after Hierarchy Music receives its own payment** for that month. "
             "You need your W-9 and Direct Deposit form on file first."),
        ],
    },
]

FAQ = [
    ("What is a scout?",
     "You find new artists and submit their Instagram profile. If they sign with Hierarchy Music, become active and earn, "
     "you earn 5% of their monthly TikTok LIVE bonus, month after month. It's residual income that can build over time."),
    ("Who should I submit?",
     "Artists 18 or older, male or female, any genre, with a current Instagram ad promoting their music and 750K followers "
     "or fewer (sweet spot 1K to 20K). See the scout guide for the full list."),
    ("Do I contact the artist?", "No. Submit them with the button and Hierarchy Music reaches out."),
    ("How do I know if my submission was accepted?",
     "You get a DM when it's approved or denied. **My submissions** shows where each one stands."),
    ("What do the statuses on My submissions mean?",
     "In review: we haven't decided yet. Not moving forward: we passed. Approved, awaiting confirmation: we approved it and "
     "Hierarchy Music hasn't confirmed them yet. Effective: confirmed as signed and active, and you earn your 5% "
     "in months they generate revenue. Terminated: no longer with us, so no new 5%. Earnings already credited stay."),
    ("Someone already submitted that artist. Now what?",
     "The first scout to submit gets credit. The bot tells you when an artist is already taken."),
    ("When and how do I get paid?",
     "Monthly, by ACH to your bank account, **within 15 days after Hierarchy Music receives its payment** for that month. "
     "Your W-9 and Direct Deposit form must be on file first."),
    ("How do I get my W-9 and Direct Deposit form?",
     "Tap **W-9 / tax form**. Hierarchy Music emails you one DocuSign packet with both. Check your spam folder. "
     "It's sent by hand, so it isn't instant."),
    ("Am I an employee? What about taxes?",
     "No. You're an independent scout and you're solely responsible for your own taxes. Hierarchy Music doesn't withhold or pay them for you."),
    ("Can I see other scouts' submissions or earnings?",
     "No. Everything in your panel is private to you, and other scouts can't see yours."),
    ("Who do I contact for help?",
     "Ask in **help** to learn from other scouts. For anything about your own account, application or payments, message Hierarchy Music staff."),
]

HELP_RULES = [
    "Be respectful, and help each other.",
    "Don't post artists you're about to submit. First to submit gets credit, so submit privately with the button.",
    "Don't share your earnings or anyone's email, phone number or bank details.",
    "Questions about your own payouts, W-9 or application go to Hierarchy Music staff, not the group.",
    "Never promise artists earnings or results.",
]
