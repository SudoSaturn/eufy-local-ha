from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ACHROMATIC_RATIO,
    CONF_GAMMA,
    CONF_SATURATION,
    CONF_SERIAL_NUMBER,
    CONF_USER_ID,
    CONF_WHITE_BALANCE_B,
    CONF_WHITE_BALANCE_G,
    DEFAULT_ACHROMATIC_RATIO,
    DEFAULT_DEVICE_NAME,
    DEFAULT_GAMMA,
    DEFAULT_SATURATION,
    DEFAULT_WHITE_BALANCE_B,
    DEFAULT_WHITE_BALANCE_G,
    DOMAIN,
)


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if len(user_input[CONF_SERIAL_NUMBER].strip()) != 16:
        errors[CONF_SERIAL_NUMBER] = "invalid_serial"
    if len(user_input[CONF_USER_ID].strip()) < 16:
        errors[CONF_USER_ID] = "invalid_user_id"
    return errors


class EufyLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovered_address = discovery_info.address
        self._discovered_name = discovery_info.name
        self.context["title_placeholders"] = {
            "name": discovery_info.name or DEFAULT_DEVICE_NAME
        }
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                address = user_input[CONF_ADDRESS].strip()
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._discovered_name or DEFAULT_DEVICE_NAME,
                    data={
                        CONF_ADDRESS: address,
                        CONF_SERIAL_NUMBER: user_input[CONF_SERIAL_NUMBER].strip(),
                        CONF_USER_ID: user_input[CONF_USER_ID].strip(),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADDRESS, default=self._discovered_address or ""
                ): str,
                vol.Required(CONF_SERIAL_NUMBER): str,
                vol.Required(CONF_USER_ID): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "EufyLocalOptionsFlow":
        return EufyLocalOptionsFlow(config_entry)


class EufyLocalOptionsFlow(config_entries.OptionsFlow):
    """Color tuning options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self._entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_GAMMA, default=opts.get(CONF_GAMMA, DEFAULT_GAMMA)
                ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3.0)),
                vol.Optional(
                    CONF_SATURATION,
                    default=opts.get(CONF_SATURATION, DEFAULT_SATURATION),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Optional(
                    CONF_ACHROMATIC_RATIO,
                    default=opts.get(
                        CONF_ACHROMATIC_RATIO, DEFAULT_ACHROMATIC_RATIO
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Optional(
                    CONF_WHITE_BALANCE_G,
                    default=opts.get(
                        CONF_WHITE_BALANCE_G, DEFAULT_WHITE_BALANCE_G
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Optional(
                    CONF_WHITE_BALANCE_B,
                    default=opts.get(
                        CONF_WHITE_BALANCE_B, DEFAULT_WHITE_BALANCE_B
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
