"""Long-running entry point for the HelpfulDjinn scheduler process.

Run by helpfuldjinn-scheduler.service. Sets HELPFULDJINN_ROLE=scheduler so
create_app() starts APScheduler and registers jobs in this single process
only — gunicorn workers (HELPFULDJINN_ROLE=web) never run scheduled jobs.
"""
import os
import signal
import time

os.environ.setdefault("HELPFULDJINN_ROLE", "scheduler")

from app import create_app, scheduler  # noqa: E402

app = create_app()


def _shutdown(signum, frame):
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

if __name__ == "__main__":
    # APScheduler runs in a background thread; keep the main thread alive.
    while True:
        time.sleep(3600)
