from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble import EufyBLEController, EufyBLEError, EufyState
from .const import CONF_SERIAL_NUMBER, DOMAIN

# Models whose controller plays the rainbow sweep (matched on the serial-number
# prefix). The E10 spotlights (T8L20) accept and report the setting too, but it
# changes nothing there and the eufy app doesn't offer it.
POWER_EFFECT_SERIAL_PREFIXES = ("T8L02",)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data[CONF_SERIAL_NUMBER].upper().startswith(POWER_EFFECT_SERIAL_PREFIXES):
        async_add_entities([EufyPowerEffectSwitch(entry)])


class EufyPowerEffectSwitch(SwitchEntity):
    """The rainbow sweep the controller plays when the lights switch on or off.

    A setting stored on the controller. Its value arrives with the light's
    status replies, which the light entity already polls for.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "power_effect"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:looks"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self.controller: EufyBLEController = entry.runtime_data
        serial = entry.data[CONF_SERIAL_NUMBER]
        self._attr_unique_id = f"eufy_local_{serial}_power_effect"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})
        self._attr_is_on = self.controller.state.power_effect

    @property
    def available(self) -> bool:
        return self._attr_is_on is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_subscribe(self._handle_report))

    @callback
    def _handle_report(self, state: EufyState) -> None:
        if state.power_effect is not None and state.power_effect != self._attr_is_on:
            self._attr_is_on = state.power_effect
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        try:
            state = await self.controller.async_set_power_effect(enabled)
        except EufyBLEError as exc:
            raise HomeAssistantError(f"Failed to change the setting: {exc}") from exc
        if state.power_effect is not None:
            self._attr_is_on = state.power_effect
            self.async_write_ha_state()
