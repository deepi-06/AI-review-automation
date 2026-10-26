"""APScheduler background job that sends reminders periodically."""
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

import config
import reminders

log = logging.getLogger("scheduler")


def _job():
    try:
        n = reminders.check_reminders()
        if n:
            log.info("Sent %s reminder(s)", n)
    except Exception:  # noqa: BLE001
        log.exception("Reminder job failed")


def start():
    sch = BackgroundScheduler(daemon=True)
    sch.add_job(_job, "interval", minutes=config.REMINDER_CHECK_MINUTES, id="reminders",
                max_instances=1, coalesce=True, next_run_time=datetime.now())
    sch.start()
    return sch
