"""Text messages (Twilio) and email (SMTP to send, IMAP to read replies). Everything here is optional:
if the keys aren't set, the bot simply doesn't use that channel and falls back to the next one."""
import asyncio
import base64
import email as email_lib
import hashlib
import hmac
import imaplib
import os
import re
import smtplib
from email.message import EmailMessage
from email.policy import default as default_policy

import aiohttp

import creators

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_FROM", "")                       # your Twilio number, e.g. +13055550100
TWILIO_SERVICE = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")   # or a Messaging Service (recommended for A2P)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")   # e.g. https://your-app.up.railway.app

MAIL_ADDRESS = os.getenv("MAIL_ADDRESS", "")                     # the dedicated bookings mailbox
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")                   # an app password, not your normal password
MAIL_SMTP_HOST = os.getenv("MAIL_SMTP_HOST", "smtp.gmail.com")
MAIL_SMTP_PORT = int(os.getenv("MAIL_SMTP_PORT", "465"))
MAIL_IMAP_HOST = os.getenv("MAIL_IMAP_HOST", "imap.gmail.com")


def sms_configured():
    return bool(TWILIO_SID and TWILIO_TOKEN and (TWILIO_FROM or TWILIO_SERVICE))


def mail_configured():
    return bool(MAIL_ADDRESS and MAIL_PASSWORD)


class SmsError(Exception):
    pass


class BadNumber(Exception):
    pass


# Twilio error codes: 21610 = recipient replied STOP; 21211/21614/21408 = unusable number or region
_BLOCK_CODES = {21610}
_BAD_NUMBER_CODES = {21211, 21614, 21401, 21408}


async def send_sms(to, body):
    """Send one text. Raises creators.SmsBlocked if the number can't be texted, BadNumber for an invalid number."""
    if not sms_configured():
        raise SmsError("Text messaging isn't set up.")
    data = {"To": to, "Body": body}
    if TWILIO_SERVICE:
        data["MessagingServiceSid"] = TWILIO_SERVICE
    else:
        data["From"] = TWILIO_FROM
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post(url, data=data, auth=aiohttp.BasicAuth(TWILIO_SID, TWILIO_TOKEN)) as r:
            if r.status in (200, 201):
                return True
            try:
                err = await r.json()
            except Exception:
                err = {}
            code = err.get("code")
            if code in _BLOCK_CODES:
                raise creators.SmsBlocked(err.get("message", "blocked"))
            if code in _BAD_NUMBER_CODES:
                raise BadNumber(err.get("message", "bad number"))
            raise SmsError(f"Twilio {r.status}: {err.get('message', 'error')}")


def twilio_signature(url, params, token):
    """X-Twilio-Signature = base64(HMAC-SHA1(token, url + each POST field name+value sorted by name))."""
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()


def verify_twilio(url, params, header, token=None):
    token = token or TWILIO_TOKEN
    if not token or not header:
        return False
    return hmac.compare_digest(twilio_signature(url, params, token), header)


# ---------- email ----------
def _send_mail_blocking(to, subject, body, reply_to=None):
    msg = EmailMessage()
    msg["From"] = MAIL_ADDRESS
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if MAIL_SMTP_PORT == 465:
        with smtplib.SMTP_SSL(MAIL_SMTP_HOST, MAIL_SMTP_PORT, timeout=30) as s:
            s.login(MAIL_ADDRESS, MAIL_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(MAIL_SMTP_HOST, MAIL_SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(MAIL_ADDRESS, MAIL_PASSWORD)
            s.send_message(msg)


async def send_email(to, subject, body, reply_to=None):
    if not mail_configured():
        raise RuntimeError("Email isn't set up.")
    await asyncio.to_thread(_send_mail_blocking, to, subject, body, reply_to)
    return True


_TAG = re.compile(r"\[HM-BOOK-(\d+)\]")
_QUOTE_START = [re.compile(p, re.I | re.M) for p in (
    r"^On .{5,120}wrote:\s*$", r"^-{2,}\s*Original Message\s*-{2,}", r"^From:\s.+$", r"^_{5,}$")]


def strip_quoted(text):
    """Keep what the studio wrote; drop the copy of our own email that mail programs append below it."""
    cut = len(text)
    for rx in _QUOTE_START:
        m = rx.search(text)
        if m:
            cut = min(cut, m.start())
    kept = [ln for ln in text[:cut].splitlines() if not ln.startswith(">")]
    return "\n".join(kept).strip()


def parse_reply(raw_bytes):
    """-> (booking_id, from_address, text, message_id) or None if it isn't a reply to one of our requests."""
    msg = email_lib.message_from_bytes(raw_bytes, policy=default_policy)
    m = _TAG.search(str(msg.get("Subject", "")))
    if not m:
        return None
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return int(m.group(1)), str(msg.get("From", "")), strip_quoted(text), str(msg.get("Message-ID", ""))


def _fetch_replies_blocking():
    out = []
    with imaplib.IMAP4_SSL(MAIL_IMAP_HOST) as im:
        im.login(MAIL_ADDRESS, MAIL_PASSWORD)
        im.select("INBOX")
        typ, data = im.search(None, "UNSEEN", "SUBJECT", '"HM-BOOK-"')
        for num in (data[0].split() if typ == "OK" and data and data[0] else []):
            typ, parts = im.fetch(num, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            parsed = parse_reply(raw) if raw else None
            if parsed:
                out.append(parsed)
            im.store(num, "+FLAGS", "\\Seen")
    return out


async def fetch_replies():
    if not mail_configured():
        return []
    return await asyncio.to_thread(_fetch_replies_blocking)
