from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble import EufyBLEController, EufyBLEError
from .const import CONF_SERIAL_NUMBER, DEVICE_MANUFACTURER, DOMAIN

SCAN_INTERVAL = timedelta(seconds=30)
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([EufyLocalLight(entry)], update_before_add=True)


class EufyLocalLight(LightEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_should_poll = True
    _attr_is_on = False
    _attr_brightness = 255
    _attr_rgb_color = (255, 255, 255)

    def __init__(self, entry: ConfigEntry) -> None:
        self.controller: EufyBLEController = entry.runtime_data
        serial = entry.data[CONF_SERIAL_NUMBER]
        self._attr_unique_id = f"eufy_local_{serial}"
        self._attr_available = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            connections={(CONNECTION_BLUETOOTH, entry.data[CONF_ADDRESS])},
            name=entry.title,
            manufacturer=DEVICE_MANUFACTURER,
            serial_number=serial,
            sw_version="Local encrypted BLE",
        )

    async def async_update(self) -> None:
        self._attr_available = (
            self.controller.connected
            or await self.controller.async_device_visible()
        )

    async def async_turn_on(self, **kwargs) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        brightness_percent = None
        color = None

        if brightness is not None:
            brightness = int(brightness)
            brightness_percent = round(brightness * 100 / 255)
        if rgb is not None:
            color = tuple(int(channel) for channel in rgb)

        try:
            await self.controller.async_set_state(
                is_on=True,
                brightness=brightness_percent,
                color=color,
            )
        except EufyBLEError as exc:
            self._attr_available = False
            raise HomeAssistantError(f"Failed to control light: {exc}") from exc

        self._attr_is_on = True
        self._attr_available = True
        if brightness is not None:
            self._attr_brightness = brightness
        if color is not None:
            self._attr_rgb_color = color

    async def async_turn_off(self, **kwargs) -> None:
        try:
            await self.controller.async_set_state(is_on=False)
        except EufyBLEError as exc:
            self._attr_available = False
            raise HomeAssistantError(f"Failed to control light: {exc}") from exc

        self._attr_is_on = False
        self._attr_available = True
