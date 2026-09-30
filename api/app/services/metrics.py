"""
Whether managed builds remove the pain (docs/managed-builds.md, "Measuring
success"): the admin metrics page.

Events are recorded as things happen (record); compute turns them, and the
builds table, into the numbers the design asks for:

- installation to the first *verified* Ready preview, median and 90th
  percentile, split by managed builds and CI images;
- the share of first previews that needed help before they worked (a failed
  preview, a retry, a failed setup check);
- build outcomes by category, build minutes per repository, time queued, and
  build slots in use.

Events start with this release; repositories installed earlier have no
installation time and are left out of the time-to-first-preview numbers.
"""

import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Build, Event, RepositorySettings

logger = logging.getLogger(__name__)

HELP_KINDS = ("preview_failed", "retry", "setup_check_failed")


def record(db: Session, kind: str, repository_full_name: Optional[str] = None,
           environment_id: Optional[int] = None, **detail: Any) -> None:
    """Record an event. Never allowed to break what it records."""
    try:
        db.add(Event(kind=kind, repository_full_name=repository_full_name, environment_id=environment_id,
                     detail=detail or None))
        db.commit()
    except Exception as e:
        logger.warning(f"Could not record event {kind} for {repository_full_name}: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _spread(values: Iterable[float]) -> Dict[str, Any]:
    """Count, median and 90th percentile (nearest rank) of some durations in seconds."""
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "median_seconds": None, "p90_seconds": None}

    def rank(p: float) -> int:
        return int(ordered[max(0, math.ceil(len(ordered) * p) - 1)])
    return {"count": len(ordered), "median_seconds": rank(0.5), "p90_seconds": rank(0.9)}


def _first_previews(events: List[Event]) -> Dict[str, Any]:
    by_repo: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        if e.repository_full_name:
            by_repo[e.repository_full_name.lower()].append(e)

    to_ready = {"managed_builds": [], "ci_images": []}
    waiting, tried, helped = 0, 0, Counter()
    needed_help = 0
    for repo_events in by_repo.values():
        repo_events.sort(key=lambda e: (_aware(e.created_at) or datetime.min.replace(tzinfo=timezone.utc), e.id))
        installed = next((e for e in repo_events if e.kind == "installed"), None)
        if installed is None:
            continue
        after = [e for e in repo_events if e.id != installed.id and _aware(e.created_at) >= _aware(installed.created_at)]
        ready = next((e for e in after if e.kind == "preview_ready" and (e.detail or {}).get("verified")), None)
        if ready is not None:
            path = "managed_builds" if (ready.detail or {}).get("managed") else "ci_images"
            to_ready[path].append((_aware(ready.created_at) - _aware(installed.created_at)).total_seconds())
        elif not any(e.kind in ("preview_ready", "preview_failed") for e in after):
            waiting += 1
            continue
        tried += 1
        before = [e for e in after if ready is None or e.id != ready.id and _aware(e.created_at) <= _aware(ready.created_at)]
        reasons = {e.kind for e in before if e.kind in HELP_KINDS}
        if reasons:
            needed_help += 1
            helped.update(reasons)
    return {
        "installation_to_verified_ready": {path: _spread(v) for path, v in to_ready.items()},
        "installed_no_preview_yet": waiting,
        "first_previews": tried,
        "needed_help": needed_help,
        "needed_help_share": round(needed_help / tried, 3) if tried else None,
        "needed_help_by_reason": dict(helped),
    }


def _builds(db: Session, since: datetime) -> Dict[str, Any]:
    builds = [b for b in db.query(Build).all() if (_aware(b.created_at) or since) >= since]
    outcomes = Counter(b.failure_category or b.status for b in builds)
    minutes: Counter = Counter()
    for b in builds:
        if b.duration_seconds is not None:
            minutes[b.repository_full_name] += -(-b.duration_seconds // 60)
    return {
        "total": len(builds),
        "outcomes": dict(outcomes.most_common()),
        "build_time": _spread(b.duration_seconds for b in builds if b.status == "succeeded" and b.duration_seconds),
        "queued_time": _spread(b.queued_seconds for b in builds if b.queued_seconds is not None),
        "minutes_by_repository": [{"repository": r, "minutes": m} for r, m in minutes.most_common()],
    }


def compute(db: Session, now: Optional[datetime] = None, days: int = 30) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    events = db.query(Event).all()
    recent = [e for e in events if (_aware(e.created_at) or now) >= since]
    slots = db.query(RepositorySettings).filter(RepositorySettings.build_slot.isnot(None)).all()
    return {
        "generated_at": now,
        "window_days": days,
        **_first_previews(events),
        "builds": _builds(db, since),
        "deploys_waiting_to_build": sum(1 for e in recent if e.kind == "build_wait"),
        "slots": {"used": len(slots), "total": settings.managed_builds_slots,
                  "repositories": sorted((r.build_slot, r.repository_full_name) for r in slots)},
    }
