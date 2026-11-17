"""Day/night classifications, source fidelity, and opt-in HA behaviour."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import CoreState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mercury_co_nz.tariff import (
    Schedule,
    UTC,
    normalize_hours,
    build_breakdown,
)
from custom_components.mercury_co_nz.tariff_history import TariffHistory
from custom_components.mercury_co_nz.mercury_api import MercuryAPI
from custom_components.mercury_co_nz.sensor import MercurySensor
from custom_components.mercury_co_nz.config_flow import MercuryOptionsFlow

NZ = ZoneInfo("Pacific/Auckland")
OPTIONS = {
    "enabled": True,
    "night_start": "21:00",
    "night_end": "07:00",
    "start_date": "2026-01-01",
    "time_zone": "Pacific/Auckland",
}


def day_records(day):
    start = datetime.fromisoformat(day).replace(tzinfo=NZ)
    end = start + timedelta(days=1)
    stamp = start.astimezone(UTC)
    rows = []
    while stamp < end:
        rows.append(
            {
                "date": stamp.astimezone(NZ).isoformat(),
                "consumption": 1.0,
                "is_estimated": False,
                "read_type": "actual",
            }
        )
        stamp += timedelta(hours=1)
    return rows


@pytest.mark.parametrize(
    ("day", "expected_night"),
    [("2026-01-02", 10), ("2026-04-05", 11), ("2026-09-27", 9)],
)
def test_day_night_totals_preserve_energy_and_dst(day, expected_night):
    rows = day_records(day)
    series, latest = build_breakdown(
        normalize_hours(rows), Schedule.from_options(OPTIONS)
    )
    assert latest["day"] == 14
    assert latest["night"] == expected_night
    assert latest["hours"] == 14 + expected_night
    for index in range(len(rows)):
        assert sum(series[p][index]["sum"] for p in series) == index + 1
    assert latest["date"] == day


def test_gap_stays_missing_and_incomplete_day_is_not_reported_as_complete():
    rows = day_records("2026-04-04") + day_records("2026-04-05")
    rows = [r for r in rows if r["date"] != "2026-04-05T02:00:00+13:00"]
    series, latest = build_breakdown(
        normalize_hours(rows), Schedule.from_options(OPTIONS)
    )
    assert latest["date"] == "2026-04-04"
    assert len(series["day"]) == 48
    assert all(
        r["start"].isoformat() != "2026-04-04T13:00:00+00:00" for r in series["night"]
    )


def test_spring_placeholder_does_not_overwrite_real_hour():
    rows = day_records("2026-09-27")
    rows.append({"date": "2026-09-27T02:00:00+12:00", "consumption": 0})
    assert sum(r["consumption"] for r in normalize_hours(rows).values()) == 23


@pytest.mark.parametrize(
    "change",
    [
        {"night_start": "21:30"},
        {"night_start": "07:00"},
        {"start_date": "invalid"},
        {"time_zone": "Europe/London"},
        {"night_start": "24:00"},
    ],
)
def test_invalid_schedules_rejected(change):
    with pytest.raises(ValueError):
        Schedule.from_options({**OPTIONS, **change})


def test_disabled_by_default_and_fixed_nzst_is_distinct():
    assert Schedule.from_options({}) is None
    assert Schedule.from_options({**OPTIONS, "enabled": False}) is None
    local = Schedule.from_options(OPTIONS)
    standard = Schedule.from_options({**OPTIONS, "time_zone": "Etc/GMT-12"})
    stamp = datetime.fromisoformat("2026-11-17T21:00:00+13:00")
    assert local.period(stamp) == "night"
    assert standard.period(stamp) == "day"
    assert local.key != standard.key
    assert local.period(datetime.fromisoformat("2025-12-31T23:00:00+13:00")) is None


def test_bad_readings_unknown_not_zero():
    rows = [
        {"date": "2026-11-17T01:00:00+13:00", "consumption": x}
        for x in (None, float("nan"), -1, True, "2")
    ]
    rows += [
        {"date": "2026-11-17T01:00:00", "consumption": 1},
        {"date": "2026-11-17T01:30:00+13:00", "consumption": 1},
    ]
    assert normalize_hours(rows) == {}


def test_plan_rates_preserved_without_guessing_unknown_tariffs():
    api = MercuryAPI.__new__(MercuryAPI)
    current = {
        "displayPlanId": "SAMPLE",
        "isTouPlan": False,
        "charges": {
            "unitRates": [
                {"name": "Day", "rate": "$0.27", "measure": "per kWh"},
                {"name": "Night", "rate": "16", "measure": "c/kWh"},
                {"name": "Special register", "rate": "$0.12", "measure": "per kWh"},
            ],
            "generationRates": [
                {"name": "Export", "rate": "$0.11", "measure": "per kWh"}
            ],
        },
    }
    result = api._normalize_plans_data(SimpleNamespace(current_plan=current))
    assert result["day_rate"] == 0.27
    assert result["night_rate"] == 0.16
    assert len(result["unit_rates"]) == 3
    assert result["generation_rates"][0]["rate"] == 0.11
    assert result["anytime_rate"] is None
    current["charges"]["unitRates"].append({"name": "Day", "rate": "$0.4"})
    assert (
        api._normalize_plans_data(SimpleNamespace(current_plan=current))["day_rate"]
        is None
    )


def make_sensor(kind, *, options=None, primary=True):
    entry = MockConfigEntry(
        domain="mercury_co_nz", data={}, options={"tariff_schedules": options or {}}
    )
    coordinator = MagicMock()
    coordinator.data = {
        "plan_day_rate": 0.27,
        "plan_night_rate": 0.16,
        "icp_second_plan_day_rate": 0.30,
        "icp_second_plan_night_rate": 0.20,
    }
    coordinator.last_update_success = True
    return MercurySensor(
        coordinator,
        kind,
        "Mercury",
        "test@example.com",
        entry,
        service_id="first" if primary else "second",
        is_primary=primary,
    )


def test_current_rate_and_period_wait_for_schedule_and_are_per_meter():
    rate = make_sensor("plan_anytime_rate")
    assert rate.native_value is None
    rate = make_sensor("plan_anytime_rate", options={"second": OPTIONS}, primary=False)
    with patch(
        "custom_components.mercury_co_nz.sensor.dt_util.utcnow",
        return_value=datetime.fromisoformat("2026-11-17T21:00:00+13:00"),
    ):
        assert rate.native_value == 0.20
    with patch(
        "custom_components.mercury_co_nz.sensor.dt_util.utcnow",
        return_value=datetime.fromisoformat("2026-11-17T07:00:00+13:00"),
    ):
        assert rate.native_value == 0.30
    assert make_sensor("tariff_day_usage").native_value is None
    assert (
        make_sensor("plan_anytime_rate", options={"second": OPTIONS}).native_value
        is None
    )


async def test_local_timer_writes_state_without_cloud_call():
    sensor = make_sensor("plan_anytime_rate")
    sensor.async_write_ha_state = MagicMock()
    await sensor._async_tariff_tick(None)
    sensor.async_write_ha_state.assert_called_once()
    sensor.coordinator.async_request_refresh.assert_not_called()


async def test_history_corrections_retry_restart_and_schedule_isolation(hass):
    hass.set_state(CoreState.running)
    history = TariffHistory(hass, "entry", "meter-1")
    history.store = SimpleNamespace(
        key="test_meter_1",
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(),
    )
    rows = day_records("2026-11-15")
    target = "custom_components.mercury_co_nz.tariff_history"
    with (
        patch(target + ".get_instance"),
        patch(target + ".async_add_external_statistics") as add,
    ):
        result = await history.async_update(rows, OPTIONS)
        assert add.call_count == 2
        assert result["tariff_night_usage"] == 10
        old_id = add.call_args_list[0].args[1]["statistic_id"]
        await history.async_update(rows, OPTIONS)
        assert add.call_count == 2
        rows[0]["consumption"] = 3
        await history.async_update(rows, OPTIONS)
        assert add.call_count == 4
        assert add.call_args_list[-1].args[2][-1]["sum"] == 12
        await history.async_update(rows, {**OPTIONS, "night_start": "22:00"})
        assert add.call_args_list[-2].args[1]["statistic_id"] != old_id
        assert add.call_args_list[-1].args[2][-1]["sum"] == 11
        await history.async_update(rows, {})
        assert add.call_count == 6
        # Replay after restart repairs a lost recorder queue, with identical sums.
        history.submitted = None
        await history.async_update(rows, OPTIONS)
        assert add.call_count == 8
        assert add.call_args_list[-1].args[2][-1]["sum"] == 12


async def test_startup_never_waits_for_recorder(hass):
    hass.set_state(CoreState.starting)
    history = TariffHistory(hass, "entry", "meter")
    history.store = SimpleNamespace(
        key="test", async_load=AsyncMock(return_value=None), async_save=AsyncMock()
    )
    with patch(
        "custom_components.mercury_co_nz.tariff_history.get_instance"
    ) as recorder:
        await history.async_update(day_records("2026-11-15"), OPTIONS)
        recorder.assert_not_called()


async def test_options_preserve_other_meter_and_validate(hass):
    entry = MockConfigEntry(
        domain="mercury_co_nz",
        data={},
        options={"tariff_schedules": {"other": OPTIONS}, "unrelated": True},
    )
    entry.add_to_hass(hass)
    flow = MercuryOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    result = await flow.async_step_init({"icp": "", "tariff_meter": "meter"})
    assert result["step_id"] == "tariff"
    result = await flow.async_step_tariff({**OPTIONS, "night_start": "07:00"})
    assert result["errors"]["base"] == "invalid_tariff_schedule"
    result = await flow.async_step_tariff(OPTIONS)
    assert result["data"]["tariff_schedules"] == {"other": OPTIONS, "meter": OPTIONS}
    assert result["data"]["unrelated"] is True


async def test_registers_are_separate_cached_readings_not_day_night_guesses():
    api = MercuryAPI.__new__(MercuryAPI)
    api._client = SimpleNamespace(
        _api_client=SimpleNamespace(
            get_electricity_meter_reads=MagicMock(
                return_value=SimpleNamespace(
                    raw_data={
                        "meterReads": [
                            {
                                "meterNumber": "M1",
                                "registers": [
                                    {
                                        "registerNumber": "1",
                                        "lastReading": "100",
                                        "lastReadType": "Actual",
                                        "lastReadDate": "2026-11-01",
                                    },
                                    {"registerNumber": "2", "lastReading": "200"},
                                ],
                            }
                        ]
                    }
                )
            )
        )
    )
    result = await api._get_meter_registers("customer", "account", "service")
    assert result["meter_register_count"] == 2
    assert result["meter_registers"][1]["reading"] == "200"
    assert "night" not in str(result)
    await api._get_meter_registers("customer", "account", "service")
    api._client._api_client.get_electricity_meter_reads.assert_called_once()


async def test_real_recorder_accepts_backfill_and_corrections(recorder_mock, hass):
    """Use HA's real import/read path, not just a mocked statistics writer."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    hass.set_state(CoreState.running)
    history = TariffHistory(hass, "entry", "meter-1")
    rows = day_records("2026-04-05")
    result = await history.async_update(rows, OPTIONS)
    await recorder_mock.async_block_till_done()
    ids = result["tariff_usage_details"]["statistic_ids"]
    begin = datetime.fromisoformat("2026-04-05T00:00:00+13:00")
    end = datetime.fromisoformat("2026-04-06T00:00:00+12:00")
    saved = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        begin,
        end,
        set(ids.values()),
        "hour",
        None,
        {"sum"},
    )
    assert len(saved[ids["night"]]) == 25
    assert saved[ids["night"]][-1]["sum"] == 11
    assert saved[ids["day"]][-1]["sum"] == 14
    rows[0]["consumption"] = 4
    await history.async_update(rows, OPTIONS)
    await recorder_mock.async_block_till_done()
    saved = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        begin,
        end,
        set(ids.values()),
        "hour",
        None,
        {"sum"},
    )
    assert len(saved[ids["night"]]) == 25
    assert saved[ids["night"]][-1]["sum"] == 14


@pytest.fixture
def recorder_config(monkeypatch):
    # Python 3.14 autospec evaluates HA 2026.10's lazy annotation, whose
    # Recorder import is TYPE_CHECKING-only. Supply the type for test mocking.
    from homeassistant.components.recorder import Recorder, migration
    from homeassistant.helpers import recorder
    from sqlalchemy.orm import Session

    monkeypatch.setattr(migration, "Recorder", Recorder, raising=False)
    monkeypatch.setattr(recorder, "Session", Session, raising=False)
