from __future__ import annotations

from datetime import timedelta
import json
import logging
from pathlib import Path

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_RGBWW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble import EufyBLEController, EufyBLEError, EufyFrameTooLarge, EufyState
from .const import CONF_SERIAL_NUMBER, DEVICE_MANUFACTURER, DOMAIN

SCAN_INTERVAL = timedelta(seconds=30)
_LOGGER = logging.getLogger(__name__)

# Models with dedicated warm/cool white LEDs, driven as RGBWW (matched on the
# serial-number prefix). Everything else keeps the original RGB mode and the
# gamma/white-balance correction in EufyBLEController.
RGBWW_SERIAL_PREFIXES = ("T8L02", "T8L20")

# The eufy app's own named presets, captured off a real E22 over its cloud link
# and replayed verbatim over Bluetooth. Each value is {opcode, tail}: the opcode
# (0x0206 solid or 0x020D animation) and the frame body after the a1/a2 envelope.
try:
    PRESETS: dict[str, dict[str, str]] = json.loads(
        (Path(__file__).parent / "effects.json").read_text()
    )
except (OSError, ValueError):
    _LOGGER.exception("Could not load effects.json; effects disabled")
    PRESETS = {}


def _cloud_id(preset: dict[str, str]) -> int | None:
    """The eufy app's id for a preset, as the light reports it back in a6.

    Animations (0x020D) carry it in a3, solid presets (0x0206) in ac.
    """
    tail = bytes.fromhex(preset["tail"])
    tag = 0xA3 if preset["opcode"].lower() == "020d" else 0xAC
    index = 0
    while index + 2 <= len(tail):
        length = tail[index + 1]
        if tail[index] == tag:
            return int.from_bytes(tail[index + 2 : index + 2 + length], "little")
        index += 2 + length
    return None


# Reverse index, so the preset the light says it is running can be shown by
# name even when it was started from the eufy app.
PRESETS_BY_CLOUD_ID: dict[int, str] = {}
for _name, _preset in PRESETS.items():
    try:
        _id = _cloud_id(_preset)
    except (KeyError, ValueError):
        continue
    if _id:
        PRESETS_BY_CLOUD_ID[_id] = _name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([EufyLocalLight(entry)], update_before_add=True)


class EufyLocalLight(LightEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:string-lights"
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = sorted(PRESETS)
    _attr_effect = None
    _attr_should_poll = True
    _attr_is_on = False
    _attr_brightness = 255

    def __init__(self, entry: ConfigEntry) -> None:
        self.controller: EufyBLEController = entry.runtime_data
        serial = entry.data[CONF_SERIAL_NUMBER]
        # The E22 (T8L02) and E10 spotlights (T8L20) have warm- and cool-white
        # LEDs, so are RGBWW (r, g, b, cold_white, warm_white). Others are RGB.
        if serial.upper().startswith(RGBWW_SERIAL_PREFIXES):
            self._attr_supported_color_modes = {ColorMode.RGBWW}
            self._attr_color_mode = ColorMode.RGBWW
            self._attr_rgbww_color = (255, 255, 255, 0, 0)
        else:
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
            self._attr_rgb_color = (255, 255, 255)
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

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_subscribe(self._handle_report))

    @callback
    def _handle_report(self, state: EufyState) -> None:
        self._apply_state(state)
        self.async_write_ha_state()

    def _apply_state(self, state: EufyState) -> None:
        # Power, brightness and the running preset are read back from the light.
        # Colour stays optimistic: the light doesn't report a usable palette.
        self._attr_available = True
        if state.is_on is not None:
            self._attr_is_on = state.is_on
        if state.brightness is not None:
            self._attr_brightness = round(state.brightness * 255 / 100)
        if state.cloud_id is not None:
            self._attr_effect = PRESETS_BY_CLOUD_ID.get(state.cloud_id)

    async def async_update(self) -> None:
        try:
            self._apply_state(await self.controller.async_request_status())
        except EufyBLEError as exc:
            _LOGGER.debug("Could not read the light's state: %s", exc)
            self._attr_available = await self.controller.async_device_visible()

    async def async_turn_on(self, **kwargs) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        effect = kwargs.get(ATTR_EFFECT)
        brightness_percent = None
        color = None

        if brightness is not None:
            brightness = int(brightness)
            brightness_percent = round(brightness * 100 / 255)
        if self._attr_color_mode == ColorMode.RGBWW:
            rgbww = kwargs.get(ATTR_RGBWW_COLOR)
            if rgbww is not None:
                # (r, g, b, cold_white, warm_white) -> the controller's 5 channels.
                color = tuple(int(channel) for channel in rgbww)
        else:
            rgb = kwargs.get(ATTR_RGB_COLOR)
            if rgb is not None:
                color = tuple(int(channel) for channel in rgb)

        # A named preset drives its own pattern across the strip, so it replaces
        # a solid-colour write rather than running alongside one.
        if effect is not None and effect in PRESETS:
            preset = PRESETS[effect]
            try:
                await self.controller.async_set_preset(
                    int(preset["opcode"], 16),
                    bytes.fromhex(preset["tail"]),
                    brightness=brightness_percent,
                )
            except EufyFrameTooLarge as exc:
                # Refused before anything was sent; the light is fine.
                raise HomeAssistantError(f"Can't play {effect}: {exc}") from exc
            except EufyBLEError as exc:
                self._attr_available = False
                raise HomeAssistantError(f"Failed to set effect: {exc}") from exc
            self._attr_is_on = True
            self._attr_available = True
            self._attr_effect = effect
            if brightness is not None:
                self._attr_brightness = brightness
            return

        self._attr_effect = None
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
            if self._attr_color_mode == ColorMode.RGBWW:
                self._attr_rgbww_color = color
            else:
                self._attr_rgb_color = color

    async def async_turn_off(self, **kwargs) -> None:
        try:
            await self.controller.async_set_state(is_on=False)
        except EufyBLEError as exc:
            self._attr_available = False
            raise HomeAssistantError(f"Failed to control light: {exc}") from exc

        self._attr_is_on = False
        self._attr_available = True
