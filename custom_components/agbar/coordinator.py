"""DataUpdateCoordinator for the Agbar (Veolia) Water integration."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import CURRENCY_EURO, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .api import (
    AgbarApiClient,
    AgbarAuthError,
    AgbarError,
    cost_per_day,
    es_date,
    es_float,
    summarize,
)
from .const import DOMAIN, SCAN_INTERVAL_HOURS

_LOGGER = logging.getLogger(__name__)


class AgbarCoordinator(DataUpdateCoordinator[dict]):
    """Polls the portal and backfills long-term statistics."""

    def __init__(self, hass: HomeAssistant, client: AgbarApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=SCAN_INTERVAL_HOURS),
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            raw = await self.hass.async_add_executor_job(self.client.fetch_all)
        except AgbarAuthError as err:
            # Triggers Home Assistant's reauth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except AgbarError as err:
            raise UpdateFailed(str(err)) from err

        data = summarize(raw)
        self._import_statistics(raw, data.get("contract"))
        self._import_cost_statistics(raw, data.get("contract"))
        return data

    def _import_statistics(self, raw: dict, contract: str | None) -> None:
        """Push the cumulative meter reading into HA statistics.

        ``lectura`` is already a monotonic total, so we use it directly as the
        statistic ``sum``. Home Assistant derives the per-day consumption as the
        delta between consecutive sums — which is exactly what the Energy
        Dashboard's water section shows.
        """
        rows = raw.get("consumos", {}).get("consumos", [])
        if not rows or not contract:
            return

        stats: list[StatisticData] = []
        for row in sorted(rows, key=lambda r: es_date(r.get("fechaConsumo", ""))):
            d = es_date(row.get("fechaConsumo", ""))
            if d == date.min:
                continue
            # External statistics must be hour-aligned; midnight local works.
            start = dt_util.start_of_local_day(datetime(d.year, d.month, d.day))
            total = es_float(row["lectura"])
            stats.append(StatisticData(start=start, state=total, sum=total))

        if not stats:
            return

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"Agbar water {contract}",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:water_{contract}",
            unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        )
        async_add_external_statistics(self.hass, metadata, stats)

    def _import_cost_statistics(self, raw: dict, contract: str | None) -> None:
        """Push daily water cost (€) into HA statistics, distributed from issued
        invoices (variable ∝ usage, fixed evenly). Each period sums to its bill,
        so this matches your real invoices. Attach it in the Energy Dashboard
        water section as the entity tracking total costs.
        """
        costs = cost_per_day(raw)
        if not costs or not contract:
            return

        running = 0.0
        stats: list[StatisticData] = []
        for d in sorted(costs):
            running += costs[d]
            start = dt_util.start_of_local_day(datetime(d.year, d.month, d.day))
            stats.append(StatisticData(start=start, state=round(running, 2), sum=round(running, 2)))

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"Agbar water cost {contract}",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:water_cost_{contract}",
            unit_of_measurement=CURRENCY_EURO,
        )
        async_add_external_statistics(self.hass, metadata, stats)
