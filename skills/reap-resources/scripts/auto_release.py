"""Whether Orca releases a settled worker's terminal by itself, read from a `worker-list` row.

A runtime that advertises orchestration.worker-auto-release.v1 sets `resource.autoReleaseAt` on a reclaimable worker
about two minutes after an accepted worker_done, and clears it when the worker is reused or kept open. The janitor's
orca-worker kind and the orchestrator's SETTLED lines both leave such a row to Orca."""
import datetime as dt
import time


def ahead(row, now=None):
    """True only for a reclaimable row whose autoReleaseAt is readable and still to come. A missing, unreadable or past
    time is not counted on: the caller releases that worker itself."""
    at = (row.get("resource") or {}).get("autoReleaseAt")
    if row.get("terminalState") != "reclaimable" or at is None or isinstance(at, bool):
        return False
    try:
        if isinstance(at, (int, float)):
            t = float(at) / (1000 if float(at) > 1e11 else 1)  # epoch milliseconds, as Orca's other times
        else:
            t = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return False
    return t > (time.time() if now is None else now)
