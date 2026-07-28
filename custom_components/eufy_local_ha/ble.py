from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from Crypto.Cipher import AES
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    CHAR_NOTIFY,
    CHAR_WRITE,
    DEFAULT_ACHROMATIC_RATIO,
    DEFAULT_GAMMA,
    DEFAULT_SATURATION,
    DEFAULT_WHITE_BALANCE_B,
    DEFAULT_WHITE_BALANCE_G,
    DEFAULT_WHITE_BALANCE_R,
)

_LOGGER = logging.getLogger(__name__)
HANDSHAKE_STEP_DELAY = 0.15
POWER_TO_COLOR_DELAY = 0.5


class EufyBLEError(Exception):
    """BLE transport or protocol error."""


class EufyBLEController:

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        serial_number: str,
        user_id: str,
        *,
        gamma: float = DEFAULT_GAMMA,
        saturation: float = DEFAULT_SATURATION,
        achromatic_ratio: float = DEFAULT_ACHROMATIC_RATIO,
        white_balance: tuple[float, float, float] | None = None,
    ) -> None:
        self.hass = hass
        self.mac = address
        self.sn = serial_number.encode("ascii")
        self.uid = user_id
        self.initial_key = user_id[:16].encode("ascii")
        self.gamma = gamma
        self.saturation = max(0.0, min(1.0, saturation))
        self.achromatic_ratio = achromatic_ratio
        self.white_balance = white_balance or (
            DEFAULT_WHITE_BALANCE_R,
            DEFAULT_WHITE_BALANCE_G,
            DEFAULT_WHITE_BALANCE_B,
        )
        self.session_key: bytes | None = None
        self.session_key_ready = asyncio.Event()
        self.client: BleakClient | None = None
        self.seq = 1
        self.current_color: tuple[int, int, int] = (255, 255, 255)
        self.current_brightness = 100
        self.command_lock = asyncio.Lock()
        self.connect_lock = asyncio.Lock()

        if len(self.sn) != 16:
            raise EufyBLEError(
                "Serial number must be exactly 16 characters"
            )
        if len(self.initial_key) != 16:
            raise EufyBLEError(
                "User ID must be at least 16 characters"
            )

    @property
    def connected(self) -> bool:
        return bool(
            self.client
            and self.client.is_connected
            and self.session_key is not None
        )

    @staticmethod
    def _pad(data: bytes) -> bytes:
        length = 16 - len(data) % 16
        return data + bytes([length]) * length

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        if not data:
            raise EufyBLEError("Empty payload")
        length = data[-1]
        if not 1 <= length <= 16 or data[-length:] != bytes([length]) * length:
            raise EufyBLEError("Invalid PKCS#7 padding")
        return data[:-length]

    @staticmethod
    def _checksum(data: bytes) -> bytes:
        value = 0
        for byte in data:
            value ^= byte
        return bytes([value])

    def _next_seq(self) -> int:
        value = self.seq
        self.seq = 1 if self.seq >= 0xFFFF else self.seq + 1
        return value

    def _base_payload(self) -> bytes:
        return (
            bytes.fromhex("a104")
            + struct.pack("<I", int(time.time()))
            + bytes.fromhex("a228")
            + self.uid.encode("ascii")
        )

    def _packet(
        self,
        opcode: int,
        payload: bytes,
        *,
        encrypted: bool = False,
        type_value: int = 1,
    ) -> bytes:
        if encrypted:
            key = self.session_key or self.initial_key
            payload = AES.new(key, AES.MODE_CBC, iv=self.sn).encrypt(self._pad(payload))
            opcode |= 0x4000

        packet = (
            bytes.fromhex("ff09")
            + struct.pack("<H", len(payload) + 10)
            + struct.pack("<H", self._next_seq())
            + bytes([type_value])
            + struct.pack(">H", opcode)
            + payload
        )
        return packet + self._checksum(packet)

    def _on_disconnect(self, _client: BleakClient) -> None:
        self.session_key = None
        self.session_key_ready.clear()

    def _notification(self, _sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        if len(raw) < 10 or raw[7:9] != bytes.fromhex("4822"):
            return
        try:
            plaintext = self._unpad(
                AES.new(self.initial_key, AES.MODE_CBC, iv=self.sn).decrypt(raw[9:-1])
            )
            marker = plaintext.find(bytes.fromhex("a110"))
            if marker < 0 or len(plaintext) < marker + 18:
                raise EufyBLEError("Session-key marker missing")
            self.session_key = plaintext[marker + 2 : marker + 18]
            self.session_key_ready.set()
        except Exception:
            _LOGGER.exception("Cant to decode the session key")

    async def _write(self, packet: bytes) -> None:
        if self.client is None or not self.client.is_connected:
            raise EufyBLEError("BLE is unavailable")
        await self.client.write_gatt_char(CHAR_WRITE, packet, response=False)

    async def _disconnect_unlocked(self) -> None:
        client, self.client = self.client, None
        self.session_key = None
        self.session_key_ready.clear()
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def async_disconnect(self) -> None:
        async with self.connect_lock:
            await self._disconnect_unlocked()

    async def async_device_visible(self) -> bool:
        return bluetooth.async_ble_device_from_address(
            self.hass, self.mac, connectable=True
        ) is not None

    async def async_ensure_connected(self) -> None:
        if self.connected:
            return

        async with self.connect_lock:
            if self.connected:
                return

            await self._disconnect_unlocked()
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.mac, connectable=True
            )
            if device is None:
                raise EufyBLEError(
                    "Light not found or connectable"
                )

            self.seq = 1
            self.session_key_ready.clear()
            try:
                self.client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    name=self.mac,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=4,
                    use_services_cache=True,
                    timeout=20.0,
                )
                await self.client.start_notify(CHAR_NOTIFY, self._notification)

                base = self._base_payload()
                capabilities = base + bytes.fromhex("a30120a4029600")
                for opcode, payload in (
                    (0x0001, base),
                    (0x0029, base),
                    (0x0003, capabilities),
                    (0x0005, capabilities),
                ):
                    await self._write(self._packet(opcode, payload))
                    await asyncio.sleep(HANDSHAKE_STEP_DELAY)

                aes_payload = base + bytes.fromhex("a304e0e3ffff")
                await self._write(
                    self._packet(0x0022, aes_payload, encrypted=True)
                )
                await asyncio.wait_for(self.session_key_ready.wait(), timeout=6.0)
                if self.session_key is None:
                    raise EufyBLEError("Negotiation returned no key")
            except Exception as exc:
                await self._disconnect_unlocked()
                raise EufyBLEError(f"Unable to establish session: {exc}") from exc

    async def _send_power(self, is_on: bool, base_payload: bytes) -> None:
        payload = base_payload + bytes.fromhex("a301") + (
            bytes([0x01]) if is_on else bytes([0x00])
        )
        await self._write(
            self._packet(0x0201, payload, encrypted=True, type_value=2)
        )

    async def _send_brightness(self, brightness: int, base_payload: bytes) -> None:
        payload = base_payload + bytes.fromhex("a401") + bytes([brightness])
        await self._write(
            self._packet(0x0201, payload, encrypted=True, type_value=2)
        )

    def _correct_color(self, color: tuple[int, int, int]) -> tuple[int, int, int]:
        peak = max(color)
        if peak == 0:
            return (0, 0, 0)
        floor = min(color)
        chroma = peak - floor

        if chroma <= self.achromatic_ratio * peak:
            wb_r, wb_g, wb_b = self.white_balance
            return (
                min(255, round(peak * wb_r)),
                min(255, round(peak * wb_g)),
                min(255, round(peak * wb_b)),
            )

        cut = floor * self.saturation
        scale = peak / (peak - cut) if peak > cut else 1.0
        saturated = [min(peak, (channel - cut) * scale) for channel in color]
        corrected = [
            min(255, max(0, round(255 * (channel / 255) ** self.gamma)))
            for channel in saturated
        ]
        return (corrected[0], corrected[1], corrected[2])

    async def _send_color(
        self,
        color: tuple[int, int, int],
        base_payload: bytes,
    ) -> None:
        red, green, blue = self._correct_color(color)
        bulbs = bytes([30]) + bytes(range(30))
        payload = (
            base_payload
            + bytes.fromhex("a302264e")
            + bytes.fromhex("a50105")
            + bytes.fromhex("a60601") + bytes([red, green, blue, 0, 0])
            + bytes([0xA7]) + bytes([len(bulbs)]) + bulbs
            + bytes.fromhex("a80164")
            + bytes.fromhex("a9050000000000")
            + bytes.fromhex("aa0100")
            + bytes.fromhex("ac0400000000")
            + bytes.fromhex("ae0100")
            + bytes.fromhex("b00107")
        )
        await self._write(
            self._packet(0x0206, payload, encrypted=True, type_value=2)
        )

    async def async_set_state(
        self,
        *,
        is_on: bool,
        brightness: int | None = None,
        color: tuple[int, int, int] | None = None,
    ) -> None:
        if brightness is not None and not 0 <= brightness <= 100:
            raise EufyBLEError("Brightness must be between 0 and 100")
        if color is not None and any(not 0 <= channel <= 255 for channel in color):
            raise EufyBLEError("RGB channels must be between 0 and 255")

        if color is not None:
            self.current_color = color
        if brightness is not None:
            self.current_brightness = brightness

        async with self.command_lock:
            for attempt in range(2):
                try:
                    await self.async_ensure_connected()
                    await self._send_power(is_on, self._base_payload())
                    if is_on and brightness is not None:
                        await asyncio.sleep(HANDSHAKE_STEP_DELAY)
                        await self._send_brightness(
                            brightness, self._base_payload()
                        )
                    if is_on and color is not None:
                        await asyncio.sleep(POWER_TO_COLOR_DELAY)
                        await self._send_color(color, self._base_payload())
                    return
                except EufyBLEError:
                    await self.async_disconnect()
                    if attempt == 1:
                        raise
                except Exception as exc:
                    await self.async_disconnect()
                    if attempt == 1:
                        raise EufyBLEError(str(exc)) from exc
