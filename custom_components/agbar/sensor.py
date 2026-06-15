"""Sensor entities for the Agbar (Veolia) Water integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CURRENCY_EURO,
    EntityCategory,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .coordinator import AgbarCoordinator
from .entity import AgbarEntity

PRICE_EUR_M3 = f"{CURRENCY_EURO}/m³"


@dataclass(frozen=True, kw_only=True)
class AgbarSensorDescription(SensorEntityDescription):
    """Describes an Agbar sensor and how to read it from coordinator data."""

    value_fn: Callable[[dict], StateType]
    attrs_fn: Callable[[dict], dict] | None = None


SENSORS: tuple[AgbarSensorDescription, ...] = (
    AgbarSensorDescription(
        key="meter_reading",
        translation_key="meter_reading",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        value_fn=lambda d: d.get("meter_reading_m3"),
        attrs_fn=lambda d: {
            "reading_date": d.get("last_reading_date"),
            "estimated": d.get("last_reading_estimated"),
        },
    ),
    AgbarSensorDescription(
        key="daily_consumption",
        translation_key="daily_consumption",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        value_fn=lambda d: d.get("last_daily_m3"),
        attrs_fn=lambda d: {"reading_date": d.get("last_reading_date")},
    ),
    AgbarSensorDescription(
        key="month_to_date",
        translation_key="month_to_date",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        value_fn=lambda d: d.get("month_to_date_m3"),
    ),
    AgbarSensorDescription(
        key="avg_daily",
        translation_key="avg_daily",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("avg_daily_m3"),
    ),
    AgbarSensorDescription(
        key="max_day",
        translation_key="max_day",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("max_day_m3"),
    ),
    AgbarSensorDescription(
        key="max_flow",
        translation_key="max_flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("max_flow_m3h"),
        attrs_fn=lambda d: {"date": d.get("max_flow_date")},
    ),
    AgbarSensorDescription(
        key="days_since_reading",
        translation_key="days_since_reading",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("days_since_reading"),
    ),
    AgbarSensorDescription(
        key="water_price",
        translation_key="water_price",
        native_unit_of_measurement=PRICE_EUR_M3,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("price_eur_m3"),
        attrs_fn=lambda d: {
            "based_on_period": d.get("price_period"),
            "note": "effective rate of the last closed bill, not a forecast",
        },
    ),
    AgbarSensorDescription(
        key="last_invoice",
        translation_key="last_invoice",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda d: d.get("last_invoice_eur"),
        attrs_fn=lambda d: {
            "status": d.get("last_invoice_status"),
            "issued": d.get("last_invoice_date"),
        },
    ),
    AgbarSensorDescription(
        key="debt",
        translation_key="debt",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda d: d.get("debt_eur"),
        attrs_fn=lambda d: {"unpaid_count": d.get("unpaid_count")},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Agbar sensors from a config entry."""
    coordinator: AgbarCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AgbarSensor(coordinator, entry, description) for description in SENSORS
    )


class AgbarSensor(AgbarEntity, SensorEntity):
    """A single Agbar value backed by the coordinator."""

    entity_description: AgbarSensorDescription

    def __init__(
        self,
        coordinator: AgbarCoordinator,
        entry: ConfigEntry,
        description: AgbarSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{self._contract}_{description.key}"

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
