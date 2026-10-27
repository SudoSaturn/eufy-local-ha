from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble import EufyBLEController, EufyState
from .const import CONF_SERIAL_NUMBER, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([EufyLightCountSensor(entry)])


class EufyLightCountSensor(SensorEntity):
    """Number of lights on the string, as the controller reports it.

    The controller probes the string when it powers up, so this can change
    between power cycles. It arrives with every state report and status
    reply (a3), so no polling of its own is needed.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "light_count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:string-lights"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "lights"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self.controller: EufyBLEController = entry.runtime_data
        serial = entry.data[CONF_SERIAL_NUMBER]
        self._attr_unique_id = f"eufy_local_{serial}_light_count"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})
        self._attr_native_value = self.controller.state.segments

    @property
    def available(self) -> bool:
        return self._attr_native_value is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_subscribe(self._handle_report))

    @callback
    def _handle_report(self, state: EufyState) -> None:
        if state.segments is not None and state.segments != self._attr_native_value:
            self._attr_native_value = state.segments
            self.async_write_ha_state()
