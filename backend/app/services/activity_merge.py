"""Field-wise priority merge for daily activity aggregates.

The original per-day selection (`SummariesService._filter_by_priority`) is
winner-takes-all: the highest-priority source's row is returned verbatim and
every other source's row for that date is discarded. That is correct for
session-shaped data (sleep), but wrong for daily activity aggregates: a Watch
left on the charger still emits a few samples (so its row exists) with
``steps_sum == 0``, and that zero then masks a phone row carrying the real
step count for the day.

Here the highest-priority source still wins per FIELD, but an empty metric
(None, or 0 for cumulative sums — a source that recorded nothing cumulates to
zero) falls back to the next-priority source that actually recorded it. Heart
rate stats (avg/max/min) move as a group so a day never mixes one source's
average with another's maximum.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from app.schemas.enums import ProviderName, infer_device_type_from_model

# Cumulative fields: 0 means "this source recorded nothing", so it is treated
# as empty and may be filled from a lower-priority source.
SUM_FIELDS: tuple[str, ...] = (
    "steps_sum",
    "active_energy_sum",
    "basal_energy_sum",
    "distance_sum",
    "flights_climbed_sum",
)

# Heart-rate stats are filled together from the first source that has an avg.
HR_FIELDS: tuple[str, ...] = ("hr_avg", "hr_max", "hr_min")

SourceKey = tuple[Any, str | None, str | None]


def priority_sort_key(
    entry: Mapping[str, Any],
    provider_order: Mapping[Any, int],
    device_type_order: Mapping[Any, int],
) -> tuple[int, int, str]:
    """(provider priority, device-type priority, device model) — lower wins."""
    source = entry.get("source") or "unknown"
    try:
        provider = ProviderName(source)
    except ValueError:
        provider = ProviderName.UNKNOWN
    provider_priority = provider_order.get(provider, 99)

    device_model = entry.get("device_model")
    device_type_priority = 99
    if device_model:
        device_type = infer_device_type_from_model(device_model)
        device_type_priority = device_type_order.get(device_type, 99)

    return (provider_priority, device_type_priority, device_model or "")


def _is_empty_sum(value: Any) -> bool:
    return value is None or value == 0


def merge_activity_rows(
    results: list[dict],
    provider_order: Mapping[Any, int],
    device_type_order: Mapping[Any, int],
    date_key: str = "activity_date",
) -> tuple[list[dict], dict[date, list[SourceKey]]]:
    """Merge per-source daily rows into one row per date, field-wise by priority.

    Returns the merged rows (one per date, in the input's date order) plus, for
    each date, the priority-ordered ``(date, source, device_model)`` keys of
    every contributing source — callers use these to fall back on secondary
    lookups (workout / active-minutes / intensity) the same way.
    """
    if not results:
        return results, {}

    by_date: dict[date, list[dict]] = {}
    for row in results:
        by_date.setdefault(row[date_key], []).append(row)

    merged: list[dict] = []
    fallback_keys: dict[date, list[SourceKey]] = {}

    for dt, entries in by_date.items():
        entries_sorted = sorted(
            entries, key=lambda e: priority_sort_key(e, provider_order, device_type_order)
        )
        fallback_keys[dt] = [
            (dt, e.get("source"), e.get("device_model")) for e in entries_sorted
        ]

        base = dict(entries_sorted[0])
        for other in entries_sorted[1:]:
            for field in SUM_FIELDS:
                if _is_empty_sum(base.get(field)) and not _is_empty_sum(other.get(field)):
                    base[field] = other[field]
            # HR stats move as a group — never mix sources within a day.
            if base.get("hr_avg") is None and other.get("hr_avg") is not None:
                for field in HR_FIELDS:
                    base[field] = other.get(field)
        merged.append(base)

    return merged, fallback_keys
