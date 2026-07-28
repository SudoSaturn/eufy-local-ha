from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, ServiceCall

from .ble import EufyBLEController
from .const import (
    CONF_ACHROMATIC_RATIO,
    CONF_GAMMA,
    CONF_SATURATION,
    CONF_SERIAL_NUMBER,
    CONF_USER_ID,
    CONF_WHITE_BALANCE_B,
    CONF_WHITE_BALANCE_G,
    DEFAULT_ACHROMATIC_RATIO,
    DEFAULT_GAMMA,
    DEFAULT_SATURATION,
    DEFAULT_WHITE_BALANCE_B,
    DEFAULT_WHITE_BALANCE_G,
    DEFAULT_WHITE_BALANCE_R,
    DOMAIN,
    PLATFORMS,
    SERVICE_SET_BRIGHTNESS,
)

SERVICE_SCHEMA = vol.Schema(
    {vol.Required("brightness"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100))}
)


def _build_controller(hass: HomeAssistant, entry: ConfigEntry) -> EufyBLEController:
    options = entry.options
    return EufyBLEController(
        hass,
        entry.data[CONF_ADDRESS],
        entry.data[CONF_SERIAL_NUMBER],
        entry.data[CONF_USER_ID],
        gamma=options.get(CONF_GAMMA, DEFAULT_GAMMA),
        saturation=options.get(CONF_SATURATION, DEFAULT_SATURATION),
        achromatic_ratio=options.get(CONF_ACHROMATIC_RATIO, DEFAULT_ACHROMATIC_RATIO),
        white_balance=(
            DEFAULT_WHITE_BALANCE_R,
            options.get(CONF_WHITE_BALANCE_G, DEFAULT_WHITE_BALANCE_G),
            options.get(CONF_WHITE_BALANCE_B, DEFAULT_WHITE_BALANCE_B),
        ),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data = _build_controller(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_SET_BRIGHTNESS):

        async def async_set_brightness(call: ServiceCall) -> None:
            for cfg in hass.config_entries.async_entries(DOMAIN):
                controller = getattr(cfg, "runtime_data", None)
                if controller is not None:
                    await controller.async_set_state(
                        is_on=True, brightness=call.data["brightness"]
                    )

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_BRIGHTNESS,
            async_set_brightness,
            schema=SERVICE_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_disconnect()
    return unloaded
