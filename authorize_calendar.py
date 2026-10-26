"""Run ONCE to authorise Google Calendar:  python authorize_calendar.py
Needs credentials.json (OAuth client of type 'Desktop app') in the project folder. Creates token.json."""
from pathlib import Path

import config
from calendar_service import SCOPES

if __name__ == "__main__":
    from google_auth_oauthlib.flow import InstalledAppFlow

    cred = Path(config.GOOGLE_CREDENTIALS_FILE)
    if not cred.exists():
        raise SystemExit(f"{cred} not found. See README step 'Google Calendar'.")
    flow = InstalledAppFlow.from_client_secrets_file(str(cred), SCOPES)
    creds = flow.run_local_server(port=0)
    Path(config.GOOGLE_TOKEN_FILE).write_text(creds.to_json())
    print(f"Authorised. Saved {config.GOOGLE_TOKEN_FILE}")
