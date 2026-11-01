import asyncio
import base64
import getpass
import re
import secrets
import struct
import time
import uuid

import aiohttp
from bleak import BleakClient, BleakScanner
from Crypto.Cipher import AES
from Crypto.Protocol.DH import key_agreement
from Crypto.PublicKey import ECC
from Crypto.Util.Padding import pad

CHAR_WRITE = "8c850002-0302-41c5-b46e-cf057c562025"
CHAR_NOTIFY = "8c850003-0302-41c5-b46e-cf057c562025"
LOCAL_NAME_PREFIXES = ("T8L02", "T8L10")
STATUS_QUERY = bytes.fromhex("a302ffff")
SERIAL_REGEX = re.compile(rb"(T8L[0-9A-Z]{13})")

SERVER_PUBLIC_KEY_HEX = (
    "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a"
    "3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"
)


async def cloud_login(email: str, password: str, country: str = "US") -> tuple[str, list[str]]:
    country = country.strip().upper()
    client_key = ECC.generate(curve="P-256")
    server_pub = ECC.import_key(bytes.fromhex(SERVER_PUBLIC_KEY_HEX), curve_name="P-256")
    shared_secret = key_agreement(static_priv=client_key, static_pub=server_pub, kdf=lambda x: x)

    cipher = AES.new(shared_secret[:32], AES.MODE_CBC, iv=shared_secret[:16])
    enc_password = base64.b64encode(
        cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))
    ).decode("ascii")

    base_url = "https://security-app.eufylife.com"
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://extend.eufylife.com/domain/{country}") as resp:
            if resp.status == 200:
                data = await resp.json()
                domain = data.get("data", {}).get("domain")
                if domain:
                    base_url = f"https://{domain}"

        payload = {
            "ab": country,
            "client_secret_info": {"public_key": client_key.public_key().export_key(format="raw").hex()},
            "enc": 0,
            "email": email,
            "password": enc_password,
            "time_zone": -time.timezone * 1000,
            "transaction": f"{int(time.time() * 1000)}",
        }
        headers = {
            "App-Name": "eufy_mega",
            "Os-Type": "android",
            "Country": country,
            "Language": "en",
            "Openudid": uuid.uuid4().hex[:16],
        }

        async with session.post(f"{base_url}/v2/passport/login_sec", json=payload, headers=headers) as resp:
            res = await resp.json()

        if res.get("code") == 26052:
            otp = input("Enter 2FA code sent to your email/phone: ").strip()
            payload["verify_code"] = otp
            async with session.post(f"{base_url}/v2/passport/login_sec", json=payload, headers=headers) as resp:
                res = await resp.json()

        if res.get("code") != 0:
            raise RuntimeError(f"Login failed: {res}")

        user_id = res["data"]["user_id"]
        auth_token = res["data"].get("auth_token", "")
        print(f"[+] Cloud login succeeded! Retrieved user_id: {user_id}")

        serials: list[str] = []
        if auth_token:
            headers["X-Auth-Token"] = auth_token
            for endpoint in ("/v1/app/get_devs_list", "/v2/house/device_list"):
                try:
                    async with session.post(f"{base_url}{endpoint}", json={}, headers=headers) as d_resp:
                        raw_text = await d_resp.read()
                        for match in SERIAL_REGEX.finditer(raw_text):
                            sn = match.group(1).decode("ascii")
                            if sn not in serials:
                                serials.append(sn)
                except Exception:
                    pass
        if serials:
            print(f"[+] Auto-discovered Serial(s) from Eufy Cloud: {serials}")

        return user_id, serials


class StandaloneEufyTester:
    def __init__(self, user_id: str, serial_number: str | None = None) -> None:
        self.sn: bytes | None = serial_number.encode("ascii") if serial_number else None
        self.uid = user_id.ljust(40, "0")[:40]
        self.initial_key = self.uid[:16].encode("ascii")
        self.session_key: bytes | None = None
        self.session_key_ready = asyncio.Event()
        self.seq = 1

    @staticmethod
    def _pad(data: bytes) -> bytes:
        length = 16 - len(data) % 16
        return data + bytes([length]) * length

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        length = data[-1]
        return data[:-length]

    @staticmethod
    def _checksum(data: bytes) -> bytes:
        val = 0
        for b in data:
            val ^= b
        return bytes([val])

    def _base_payload(self) -> bytes:
        return (
            bytes.fromhex("a104")
            + struct.pack("<I", int(time.time()))
            + bytes.fromhex("a228")
            + self.uid.encode("ascii")
        )

    def _packet(self, opcode: int, payload: bytes, *, encrypted: bool = False, type_value: int = 1) -> bytes:
        if encrypted:
            if not self.sn or len(self.sn) != 16:
                raise RuntimeError("16-char Serial Number is required before sending encrypted packets")
            key = self.session_key or self.initial_key
            payload = AES.new(key, AES.MODE_CBC, iv=self.sn).encrypt(self._pad(payload))
            opcode |= 0x4000
        seq = self.seq
        self.seq = 1 if self.seq >= 0xFFFF else self.seq + 1
        pkt = (
            bytes.fromhex("ff09")
            + struct.pack("<H", len(payload) + 10)
            + struct.pack("<H", seq)
            + bytes([type_value])
            + struct.pack(">H", opcode)
            + payload
        )
        return pkt + self._checksum(pkt)

    def _on_notify(self, _sender: int, data: bytearray) -> None:
        raw = bytes(data)
        if len(raw) < 10:
            return
        opcode = struct.unpack(">H", raw[7:9])[0]
        body = raw[9:-1]
        print(f"    [BLE RX] Opcode: 0x{opcode:04X} | Body Hex: {body.hex()}")

        if self.sn is None:
            match = SERIAL_REGEX.search(raw)
            if match:
                detected = match.group(1).decode("ascii")
                print(f"[+] Auto-extracted Serial from BLE reply: {detected}")
                self.sn = detected.encode("ascii")

        if opcode == 0x4822 and self.sn:
            plaintext = self._unpad(
                AES.new(self.initial_key, AES.MODE_CBC, iv=self.sn).decrypt(body)
            )
            marker = plaintext.find(bytes.fromhex("a110"))
            if marker >= 0 and len(plaintext) >= marker + 18:
                self.session_key = plaintext[marker + 2 : marker + 18]
                print(f"[+] Session key successfully negotiated: {self.session_key.hex()}")
                self.session_key_ready.set()

    async def probe_gatt_for_serial(self, client: BleakClient) -> None:
        for service in client.services:
            for char in service.characteristics:
                if "read" in char.properties:
                    try:
                        val = bytes(await client.read_gatt_char(char.uuid))
                        match = SERIAL_REGEX.search(val)
                        if match and not self.sn:
                            detected = match.group(1).decode("ascii")
                            print(f"[+] Auto-extracted Serial from GATT char {char.uuid}: {detected}")
                            self.sn = detected.encode("ascii")
                    except Exception:
                        pass

    async def run_test(self, device) -> None:
        print(f"[*] Connecting to {device.name} ({device.address})...")
        async with BleakClient(device) as client:
            if not self.sn:
                await self.probe_gatt_for_serial(client)

            await client.start_notify(CHAR_NOTIFY, self._on_notify)

            base = self._base_payload()
            capabilities = base + bytes.fromhex("a30120a4029600")
            for opcode, payload in (
                (0x0001, base),
                (0x0029, base),
                (0x0003, capabilities),
                (0x0005, capabilities),
            ):
                print(f"[*] Sending handshake step 0x{opcode:04X}...")
                await client.write_gatt_char(CHAR_WRITE, self._packet(opcode, payload), response=False)
                await asyncio.sleep(0.2)

            if not self.sn:
                sn_input = input("\n[!] Enter 16-char Serial Number (T8L...): ").strip().upper()
                self.sn = sn_input.encode("ascii")

            print(f"[*] Sending encrypted key negotiation (0x0022)...")
            aes_payload = base + bytes.fromhex("a304e0e3ffff")
            await client.write_gatt_char(
                CHAR_WRITE, self._packet(0x0022, aes_payload, encrypted=True), response=False
            )

            await asyncio.wait_for(self.session_key_ready.wait(), timeout=6.0)

            print("\n[!] Handshake complete! Triggering visual changes on the light...")

            print("[*] Turning light ON...")
            pwr_payload = base + bytes.fromhex("a30101")
            await client.write_gatt_char(
                CHAR_WRITE, self._packet(0x0201, pwr_payload, encrypted=True, type_value=2), response=False
            )
            await asyncio.sleep(1.0)

            print("[*] Setting brightness to 100%...")
            bright_payload = base + bytes.fromhex("a40164")
            await client.write_gatt_char(
                CHAR_WRITE, self._packet(0x0201, bright_payload, encrypted=True, type_value=2), response=False
            )
            await asyncio.sleep(1.0)

            print("[*] Setting color to Bright Green...")
            bulbs = bytes([30]) + bytes(range(30))
            color_payload = (
                base
                + bytes.fromhex("a302264e")
                + bytes.fromhex("a50105")
                + bytes.fromhex("a60601") + bytes([0, 255, 0, 0, 0])
                + bytes([0xA7]) + bytes([len(bulbs)]) + bulbs
                + bytes.fromhex("a80164")
                + bytes.fromhex("a9050000000000")
                + bytes.fromhex("aa0100")
                + bytes.fromhex("ac0400000000")
                + bytes.fromhex("ae0100")
                + bytes.fromhex("b00107")
            )
            await client.write_gatt_char(
                CHAR_WRITE, self._packet(0x0206, color_payload, encrypted=True, type_value=2), response=False
            )
            print("[+] Success! Your light should now be turned ON and glowing bright green.")
            await asyncio.sleep(3.0)


async def main() -> None:
    print("[*] Scanning for T8L02 / T8L10 BLE devices (5 seconds)...")
    discovered = await BleakScanner.discover(timeout=5.0, return_adv=True)
    target_device = None
    detected_serial = None

    for dev, adv in discovered.values():
        name = dev.name or adv.local_name or ""
        if name.startswith(LOCAL_NAME_PREFIXES):
            target_device = dev
            print(f"[+] Found Eufy Light: {name} | Address: {dev.address}")
            for raw in list(adv.manufacturer_data.values()) + list(adv.service_data.values()):
                m = SERIAL_REGEX.search(raw)
                if m:
                    detected_serial = m.group(1).decode("ascii")
                    print(f"[+] Auto-detected Serial from advertisement: {detected_serial}")
            break

    if not target_device:
        print("[-] No T8L02/T8L10 light found in BLE range.")
        return

    print("\nSelect test mode:")
    print("  1) 100% Local Pairing Mode (generates random 40-char user_id — put light in pairing mode first!)")
    print("  2) Eufy Cloud Login (fetches your account's user_id & serial)")
    print("  3) Manual user_id")
    choice = input("Choice [1/2/3]: ").strip()

    if choice == "1":
        user_id = secrets.token_hex(20)
        print(f"[+] Generated local 40-char user_id: {user_id}")
    elif choice == "2":
        email = input("Eufy Email: ").strip()
        password = getpass.getpass("Eufy Password: ")
        country = input("Country code [US]: ").strip() or "US"
        user_id, cloud_serials = await cloud_login(email, password, country)
        if not detected_serial and cloud_serials:
            detected_serial = cloud_serials[0]
    else:
        user_id = input("Enter user_id: ").strip()

    tester = StandaloneEufyTester(user_id=user_id, serial_number=detected_serial)
    await tester.run_test(target_device)


if __name__ == "__main__":
    asyncio.run(main())