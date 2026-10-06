"""Run this ONCE on your computer to connect the Google account that will send the calendar invites.
1. In Google Cloud Console, create an OAuth client of type "Desktop app" and download its JSON as client_secret.json
   into this folder.  2. Run: python3 get_google_token.py   3. Sign in, allow access, and copy the three values it prints."""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

from scheduler import SCOPES

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open("client_secret.json"))
info = info.get("installed") or info.get("web")
print("\nAdd these to your .env (and Railway):\n")
print(f"GOOGLE_CLIENT_ID={info['client_id']}")
print(f"GOOGLE_CLIENT_SECRET={info['client_secret']}")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
