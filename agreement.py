"""The Scout Agreement scouts accept when they apply. Edit the words here and bump VERSION when you change them.
This is a plain-language template, not legal advice. Have an attorney review it before you rely on it."""

VERSION = "2026-10-05.8"
TITLE = "Hierarchy Music Scout Agreement"

SECTIONS = [
    ("1. Independent scout",
     "You are an independent contractor, not an employee, partner or agent of Hierarchy Music LLC (\"Hierarchy\"). "
     "You can't sign or promise anything on Hierarchy's behalf. **You are solely responsible for all taxes on what you earn** "
     "(including income and self-employment taxes). **Hierarchy is not responsible for your taxes** and does not withhold or pay them for you, "
     "and doesn't give tax advice. "
     "You must give Hierarchy a completed W-9 before you are paid, and Hierarchy may report your payments to the IRS as the law requires."),
    ("2. What you do",
     "You submit creators' Instagram usernames and profile links as prospects. Hierarchy decides, at its sole discretion, "
     "whether to approve, contact or sign any creator. Hierarchy is not required to accept any prospect."),
    ("3. Your commission",
     "You earn a commission **only if a creator you submitted starts generating revenue**. For each creator you submitted who is approved, signed and confirmed by Hierarchy as **Effective**, you earn **5%** of that "
     "creator's TikTok LIVE \"Estimated bonus\" for each month that bonus is above $0 and they are Effective. For example, if a creator earns $1,000 in a month, "
     "your 5% is $50. If more than one scout submits the same creator, only the first scout to submit earns the commission."),
    ("4. If a creator is terminated",
     "If a creator's status becomes **Terminated**, or they otherwise stop being Effective, you earn **nothing** for any month after that. "
     "Amounts already credited for earlier months stay yours. Hierarchy may correct earlier months if TikTok changes its final numbers."),
    ("5. No guarantee of income",
     "Your earnings depend on how much your creators earn on LIVE and how many of your prospects are successfully signed. "
     "A creator who signs but doesn't generate revenue earns you nothing. Hierarchy does not promise any minimum amount. Any example of earnings is only an illustration, not a promise or a typical result."),
    ("6. Payment",
     "Commissions are calculated each month after Hierarchy receives the earnings data. Hierarchy pays your commission by ACH "
     "**within 15 days after Hierarchy receives its own payment** for that month. Payment requires your W-9 and Direct Deposit "
     "Authorization to be on file. Hierarchy may hold a payment while it looks into suspected fraud or a breach of this agreement."),
    ("7. How you must behave",
     "Give accurate information. Never promise a creator specific earnings or misrepresent Hierarchy. "
     "No spam, harassment or deception. Follow TikTok's and Instagram's rules and all applicable laws."),
    ("8. Confidentiality",
     "Everything you learn through this program is **confidential**: artists' names, contact details and deal information; other scouts' "
     "identities, submissions and earnings; how Hierarchy's program, pricing, systems and strategy work; and anything posted in "
     "Hierarchy's servers and channels. Keep it secret and use it **only to do this scouting**. Never share, post, screenshot, copy or sell it "
     "to anyone, including other scouts, other companies and the public. Never use it to contact, recruit or work with Hierarchy's artists "
     "for yourself or anyone else. This duty **continues after this agreement ends**. If the law requires you to disclose something, tell "
     "Hierarchy first if the law allows. Information that is already public through no fault of yours isn't covered."),
    ("9. If you break confidentiality",
     "Hierarchy may remove you immediately, and you earn nothing for any month after that (see Section 10). Hierarchy may also ask a court "
     "to stop you, and may recover the losses and legal costs it suffers because of it, to the extent the law allows. You agree that "
     "money alone may not repair this kind of harm."),
    ("10. Ending and changes",
     "**Hierarchy may end this agreement and remove you as a scout at any time, for any reason or no reason, with or without notice.** "
     "You may end it at any time by telling Hierarchy. When it ends you can't submit new prospects, but you keep earning your 5% "
     "(under Sections 3 to 6) on artists you already submitted, for as long as they stay Effective and generate revenue. "
     "**The exception:** if Hierarchy ends this agreement because you broke it, including fraud, misrepresentation or breaking these rules, "
     "you earn nothing for any month after that. Commissions earned for earlier months are not lost.",),
    ("11. Changes and the law",
     "Hierarchy may update this agreement; you will be asked to accept the new version. "
     "Florida law governs this agreement. This is the whole agreement between you and Hierarchy about scouting."),
    ("12. Signing electronically",
     "By checking the box and submitting your application you sign this agreement electronically and agree it is legally binding, "
     "and you agree to receive records electronically. Hierarchy keeps a record of your Discord account, the version and the time you accepted."),
]


def full_text():
    """Plain-text copy of the agreement, saved with every acceptance."""
    parts = [TITLE, f"Version {VERSION}", ""]
    for name, text in SECTIONS:
        parts += [name, text.replace("**", ""), ""]
    return "\n".join(parts)
