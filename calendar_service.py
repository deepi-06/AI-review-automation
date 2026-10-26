"""Google Calendar API: creates review events with a Google Meet link.

If Google Calendar is not authorised, a real, working Jitsi Meet room link is generated instead
(clearly reported as source='jitsi-fallback') so the workflow never breaks.
"""
import uuid
from datetime import timedelta
from pathlib import Path

import config

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def calendar_ready():
    return Path(config.GOOGLE_TOKEN_FILE).exists()


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = Path(config.GOOGLE_TOKEN_FILE)
    if not token.exists():
        raise RuntimeError("token.json not found - run: python authorize_calendar.py")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token.write_text(creds.to_json())
        else:
            raise RuntimeError("Google token invalid - run: python authorize_calendar.py again")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _times(start, minutes):
    end = start + timedelta(minutes=minutes)
    return ({"dateTime": start.isoformat(), "timeZone": config.TIMEZONE},
            {"dateTime": end.isoformat(), "timeZone": config.TIMEZONE})


def _jitsi():
    return f"https://meet.jit.si/AIReview-{uuid.uuid4().hex[:10]}"


def create_meeting(summary, description, start, minutes, attendee_email):
    """Returns dict(link, event_id, source, note)."""
    note = ""
    if calendar_ready():
        try:
            svc = _service()
            s, e = _times(start, minutes)
            body = {"summary": summary, "description": description, "start": s, "end": e,
                    "attendees": [{"email": attendee_email}],
                    "conferenceData": {"createRequest": {"requestId": uuid.uuid4().hex,
                                                         "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}
            ev = svc.events().insert(calendarId="primary", body=body, conferenceDataVersion=1,
                                     sendUpdates="all").execute()
            link = ev.get("hangoutLink")
            if not link:
                for ep in ev.get("conferenceData", {}).get("entryPoints", []):
                    if ep.get("entryPointType") == "video":
                        link = ep.get("uri")
            if link:
                return {"link": link, "event_id": ev.get("id"), "source": "google-meet", "note": ""}
            note = "Calendar event created but no Meet link returned"
            return {"link": _jitsi(), "event_id": ev.get("id"), "source": "jitsi-fallback", "note": note}
        except Exception as e:  # noqa: BLE001
            note = f"Google Calendar error: {str(e)[:150]}"
    else:
        note = "Google Calendar not authorised"
    return {"link": _jitsi(), "event_id": None, "source": "jitsi-fallback", "note": note}


def update_meeting(event_id, start, minutes):
    """Reschedule an existing Google event. Returns True on success."""
    if not event_id or not calendar_ready():
        return False
    try:
        s, e = _times(start, minutes)
        _service().events().patch(calendarId="primary", eventId=event_id, body={"start": s, "end": e},
                                  sendUpdates="all").execute()
        return True
    except Exception:  # noqa: BLE001
        return False
