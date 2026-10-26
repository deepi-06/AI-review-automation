"""Central configuration. Every secret comes from environment variables / .env (never hardcoded)."""
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name, default=""):
    return os.getenv(name, default).strip()


def _int(name, default):
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name, default):
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


APP_NAME = "AI Review Automation System"
APP_URL = _env("APP_URL", "http://localhost:8501")

DB_PATH = Path(_env("DB_PATH", str(BASE_DIR / "data" / "review_system.db")))
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
SAMPLE_XLSX = BASE_DIR / "sample_candidates.xlsx"

ADMIN_PASSWORD = _env("ADMIN_PASSWORD")

# AI
AI_PROVIDER = _env("AI_PROVIDER", "gemini").lower()
GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.8-flash")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o-mini")
ALLOW_OFFLINE_FALLBACK = _bool("ALLOW_OFFLINE_FALLBACK", True)
AI_TIMEOUT = _int("AI_TIMEOUT", 90)

# Email
SMTP_USER = _env("SMTP_USER")
SMTP_APP_PASSWORD = _env("SMTP_APP_PASSWORD").replace(" ", "")
SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 465)
MAIL_FROM_NAME = _env("MAIL_FROM_NAME", APP_NAME)

# Google Calendar
GOOGLE_CREDENTIALS_FILE = _env("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = _env("GOOGLE_TOKEN_FILE", "token.json")

# Workflow
TIMEZONE = _env("TIMEZONE", "Asia/Kolkata")
TASK_DAYS = _int("TASK_DAYS", 3)
REVIEW_HOUR = _int("REVIEW_HOUR", 11)
MEETING_MINUTES = _int("MEETING_MINUTES", 30)
REMINDER_HOURS = _int("REMINDER_HOURS", 24)
REMINDER_CHECK_MINUTES = _int("REMINDER_CHECK_MINUTES", 30)

ISO = "%Y-%m-%dT%H:%M:%S"


def now():
    """Current local time (naive) in the configured timezone."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)
    except Exception:
        return datetime.now().replace(microsecond=0)


def fmt(dt):
    return dt.strftime(ISO)


def parse(s):
    return datetime.strptime(s, ISO)


def pretty(s):
    if not s:
        return "-"
    try:
        return parse(s).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return str(s)
