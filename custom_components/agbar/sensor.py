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
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AgbarCoordinator

VOLUME_FLOW_M3H = "m³/h"


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
    AgbarSensorDescription(
        key="max_flow",
        translation_key="max_flow",
        native_unit_of_measurement=VOLUME_FLOW_M3H,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("max_flow_m3h"),
        attrs_fn=lambda d: {"date": d.get("max_flow_date")},
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


class AgbarSensor(CoordinatorEntity[AgbarCoordinator], SensorEntity):
    """A single Agbar value backed by the coordinator."""

    entity_description: AgbarSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AgbarCoordinator,
        entry: ConfigEntry,
        description: AgbarSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        contract = coordinator.data.get("contract") or entry.entry_id
        self._attr_unique_id = f"{contract}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract)},
            name=f"Agbar {contract}",
            manufacturer=MANUFACTURER,
            model="Water meter",
        )

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
