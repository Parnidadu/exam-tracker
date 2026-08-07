"""Placeholder worker process for Compose wiring.

Touches a heartbeat file on an interval so its healthcheck can prove the
worker service is alive before EXT-003/S4 replace this with real Celery
tasks.
"""

import time
from pathlib import Path

HEARTBEAT = Path("/tmp/worker_heartbeat")

if __name__ == "__main__":
    while True:
        HEARTBEAT.touch()
        time.sleep(5)
