"""Unit tests for the field-wise activity priority merge (no DB needed)."""

from datetime import date

from app.schemas.enums import DeviceType, ProviderName
from app.services.activity_merge import merge_activity_rows

PROVIDER_ORDER: dict[ProviderName, int] = {ProviderName.APPLE: 1}
DEVICE_ORDER: dict[DeviceType, int] = {
    DeviceType.WATCH: 1,
    DeviceType.PHONE: 4,
    DeviceType.UNKNOWN: 99,
}

DAY = date(2026, 6, 9)


def watch_row(**overrides) -> dict:
    row = {
        "activity_date": DAY,
        "source": "com.apple.health.ED447642",
        "device_model": "Watch7,5",
        "steps_sum": 0,
        "active_energy_sum": 0.0,
        "basal_energy_sum": 0.0,
        "hr_avg": None,
        "hr_max": None,
        "hr_min": None,
        "distance_sum": None,
        "flights_climbed_sum": None,
    }
    row.update(overrides)
    return row


def phone_row(**overrides) -> dict:
    row = watch_row(device_model="iPhone16,2")
    row.update(overrides)
    return row


class TestMergeActivityRows:
    def test_zero_steps_on_priority_source_falls_back_to_real_phone_steps(self):
        rows = [watch_row(steps_sum=0), phone_row(steps_sum=1202)]
        merged, _ = merge_activity_rows(rows, PROVIDER_ORDER, DEVICE_ORDER)
        assert len(merged) == 1
        assert merged[0]["steps_sum"] == 1202
        # Source metadata stays the priority winner's.
        assert merged[0]["device_model"] == "Watch7,5"

    def test_priority_source_with_real_value_wins(self):
        rows = [watch_row(steps_sum=3404), phone_row(steps_sum=2100)]
        merged, _ = merge_activity_rows(rows, PROVIDER_ORDER, DEVICE_ORDER)
        assert merged[0]["steps_sum"] == 3404

    def test_hr_stats_move_as_a_group(self):
        rows = [
            watch_row(hr_avg=None, hr_max=None, hr_min=None),
            phone_row(hr_avg=70, hr_max=120, hr_min=52),
        ]
        merged, _ = merge_activity_rows(rows, PROVIDER_ORDER, DEVICE_ORDER)
        assert merged[0]["hr_avg"] == 70
        assert merged[0]["hr_max"] == 120
        assert merged[0]["hr_min"] == 52

    def test_hr_stats_never_mix_sources(self):
        rows = [
            watch_row(hr_avg=60, hr_max=None, hr_min=None),
            phone_row(hr_avg=70, hr_max=120, hr_min=52),
        ]
        merged, _ = merge_activity_rows(rows, PROVIDER_ORDER, DEVICE_ORDER)
        # Watch has an avg, so its (partial) group is kept untouched.
        assert merged[0]["hr_avg"] == 60
        assert merged[0]["hr_max"] is None

    def test_fallback_keys_are_priority_ordered_per_date(self):
        rows = [phone_row(), watch_row()]
        _, fallback = merge_activity_rows(rows, PROVIDER_ORDER, DEVICE_ORDER)
        assert [k[2] for k in fallback[DAY]] == ["Watch7,5", "iPhone16,2"]

    def test_single_source_days_pass_through(self):
        rows = [phone_row(steps_sum=900)]
        merged, fallback = merge_activity_rows(rows, PROVIDER_ORDER, DEVICE_ORDER)
        assert merged[0]["steps_sum"] == 900
        assert len(fallback[DAY]) == 1

    def test_empty_input(self):
        merged, fallback = merge_activity_rows([], PROVIDER_ORDER, DEVICE_ORDER)
        assert merged == []
        assert fallback == {}

    def test_multiple_dates_keep_one_row_each(self):
        other_day = date(2026, 6, 8)
        rows = [
            watch_row(steps_sum=0),
            phone_row(steps_sum=1202),
            watch_row(activity_date=other_day, steps_sum=5000),
        ]
        merged, _ = merge_activity_rows(rows, PROVIDER_ORDER, DEVICE_ORDER)
        by_date = {m["activity_date"]: m for m in merged}
        assert by_date[DAY]["steps_sum"] == 1202
        assert by_date[other_day]["steps_sum"] == 5000
