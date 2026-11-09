from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .ble import EufyBLEController, EufyBLEError
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
    LOCAL_NAME_PREFIXES,
    extract_serial_from_ble,
    generate_local_user_id,
)
from .eufy_auth import EufyPassportAuth


def _validate(user_input: dict[str, Any], require_serial: bool = True) -> dict[str, str]:
    errors: dict[str, str] = {}
    serial = user_input.get(CONF_SERIAL_NUMBER, "").strip()
    if serial:
        if len(serial) != 16:
            errors[CONF_SERIAL_NUMBER] = "invalid_serial"
    elif require_serial:
        errors[CONF_SERIAL_NUMBER] = "invalid_serial"
    if CONF_USER_ID in user_input and len(user_input[CONF_USER_ID].strip()) < 16:
        errors[CONF_USER_ID] = "invalid_user_id"
    return errors


class EufyLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None
        self._discovered_serial: str | None = None
        self._auth: EufyPassportAuth | None = None
        self._email: str = ""
        self._password: str = ""
        self._pending_address: str = ""
        self._pending_serial: str = ""

    def _get_serial_from_advertisement(self, address: str) -> str | None:
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address.upper() == address.upper():
                sn = extract_serial_from_ble(info.manufacturer_data, info.service_data)
                if sn:
                    return sn
        return None

    def _scan_nearby_ble(self) -> None:
        if self._discovered_address:
            return
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.name and info.name.startswith(LOCAL_NAME_PREFIXES):
                self._discovered_address = info.address
                self._discovered_name = info.name
                self._discovered_serial = extract_serial_from_ble(
                    info.manufacturer_data, info.service_data
                )
                break

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovered_address = discovery_info.address
        self._discovered_name = discovery_info.name
        self._discovered_serial = extract_serial_from_ble(
            discovery_info.manufacturer_data, discovery_info.service_data
        )
        self.context["title_placeholders"] = {
            "name": discovery_info.name or DEFAULT_DEVICE_NAME
        }
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._scan_nearby_ble()
        return self.async_show_menu(
            step_id="user",
            menu_options=["local_pairing", "cloud_login", "manual"],
        )

    async def async_step_local_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input, require_serial=False)
            if not errors:
                address = user_input[CONF_ADDRESS].strip()
                serial = user_input.get(CONF_SERIAL_NUMBER, "").strip().upper()
                if not serial:
                    serial = self._get_serial_from_advertisement(address) or ""

                user_id = generate_local_user_id()

                controller = EufyBLEController(self.hass, address, serial or None, user_id)
                try:
                    await controller.async_ensure_connected()
                    if not serial and controller.serial_number:
                        serial = controller.serial_number
                    await controller.async_disconnect()
                except EufyBLEError:
                    errors["base"] = "cannot_connect"
                else:
                    if not serial or len(serial) != 16:
                        errors[CONF_SERIAL_NUMBER] = "invalid_serial"
                    else:
                        await self.async_set_unique_id(address, raise_on_progress=False)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=self._discovered_name or DEFAULT_DEVICE_NAME,
                            data={
                                CONF_ADDRESS: address,
                                CONF_SERIAL_NUMBER: serial,
                                CONF_USER_ID: user_id,
                            },
                        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADDRESS, default=self._discovered_address or ""
                ): str,
                vol.Optional(
                    CONF_SERIAL_NUMBER, default=self._discovered_serial or ""
                ): str,
            }
        )
        return self.async_show_form(
            step_id="local_pairing", data_schema=schema, errors=errors
        )

    async def async_step_cloud_login(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input, require_serial=False)
            if not errors:
                self._pending_address = user_input[CONF_ADDRESS].strip()
                self._pending_serial = user_input.get(CONF_SERIAL_NUMBER, "").strip().upper()
                if not self._pending_serial:
                    self._pending_serial = (
                        self._get_serial_from_advertisement(self._pending_address) or ""
                    )
                self._email = user_input["email"].strip()
                self._password = user_input["password"]

                session = async_get_clientsession(self.hass)
                self._auth = EufyPassportAuth(session, user_input["country"])
                try:
                    res = await self._auth.async_login(self._email, self._password)
                except Exception:
                    errors["base"] = "cannot_connect"
                else:
                    code = res.get("code")
                    if code == 0:
                        user_id = res["data"]["user_id"]
                        cloud_serials = res.get("data", {}).get("serials", [])
                        if not self._pending_serial and cloud_serials:
                            self._pending_serial = cloud_serials[0]
                        if not self._pending_serial or len(self._pending_serial) != 16:
                            errors[CONF_SERIAL_NUMBER] = "invalid_serial"
                        else:
                            return await self._async_finish_entry(
                                self._pending_address, self._pending_serial, user_id
                            )
                    elif code == 26052:
                        return await self.async_step_2fa()
                    else:
                        errors["base"] = "invalid_auth"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADDRESS, default=self._discovered_address or ""
                ): str,
                vol.Optional(
                    CONF_SERIAL_NUMBER, default=self._discovered_serial or ""
                ): str,
                vol.Required("email"): str,
                vol.Required("password"): str,
                vol.Optional("country", default="US"): str,
            }
        )
        return self.async_show_form(
            step_id="cloud_login", data_schema=schema, errors=errors
        )

    async def async_step_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._auth is not None:
            try:
                res = await self._auth.async_login(
                    self._email,
                    self._password,
                    verify_code=user_input["verify_code"],
                )
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                if res.get("code") == 0:
                    user_id = res["data"]["user_id"]
                    cloud_serials = res.get("data", {}).get("serials", [])
                    if not self._pending_serial and cloud_serials:
                        self._pending_serial = cloud_serials[0]
                    if not self._pending_serial or len(self._pending_serial) != 16:
                        errors[CONF_SERIAL_NUMBER] = "invalid_serial"
                    else:
                        return await self._async_finish_entry(
                            self._pending_address, self._pending_serial, user_id
                        )
                else:
                    errors["base"] = "invalid_auth"

        schema = vol.Schema({vol.Required("verify_code"): str})
        return self.async_show_form(
            step_id="2fa", data_schema=schema, errors=errors
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input, require_serial=True)
            if not errors:
                return await self._async_finish_entry(
                    user_input[CONF_ADDRESS].strip(),
                    user_input[CONF_SERIAL_NUMBER].strip().upper(),
                    user_input[CONF_USER_ID].strip(),
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADDRESS, default=self._discovered_address or ""
                ): str,
                vol.Required(
                    CONF_SERIAL_NUMBER, default=self._discovered_serial or ""
                ): str,
                vol.Required(CONF_USER_ID): str,
            }
        )
        return self.async_show_form(
            step_id="manual", data_schema=schema, errors=errors
        )

    async def _async_finish_entry(
        self, address: str, serial: str, user_id: str
    ) -> FlowResult:
        await self.async_set_unique_id(address, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._discovered_name or DEFAULT_DEVICE_NAME,
            data={
                CONF_ADDRESS: address,
                CONF_SERIAL_NUMBER: serial,
                CONF_USER_ID: user_id,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "EufyLocalOptionsFlow":
        return EufyLocalOptionsFlow(config_entry)