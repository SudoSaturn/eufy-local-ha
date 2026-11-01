from __future__ import annotations

import base64
import time
import uuid
from typing import Any

import aiohttp
from Crypto.Cipher import AES
from Crypto.PublicKey import ECC
from Crypto.Protocol.DH import key_agreement
from Crypto.Util.Padding import pad

from .const import SERIAL_REGEX

SERVER_PUBLIC_KEY_HEX = (
    "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a"
    "3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"
)


class EufyAuthError(Exception):
    pass


class EufyPassportAuth:

    def __init__(self, session: aiohttp.ClientSession, country: str = "US") -> None:
        self._session = session
        self._country = country.strip().upper()
        self._base_url = "https://security-app.eufylife.com"
        self._client_key = ECC.generate(curve="P-256")
        self._shared_secret = self._compute_shared_secret()

    def _compute_shared_secret(self) -> bytes:
        server_pub = ECC.import_key(bytes.fromhex(SERVER_PUBLIC_KEY_HEX), curve_name="P-256")
        return key_agreement(
            static_priv=self._client_key,
            static_pub=server_pub,
            kdf=lambda x: x,
        )

    def _client_public_key_hex(self) -> str:
        return self._client_key.public_key().export_key(format="raw").hex()

    def _encrypt_password(self, password: str) -> str:
        key = self._shared_secret[:32]
        iv = self._shared_secret[:16]
        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
        ct = cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))
        return base64.b64encode(ct).decode("ascii")

    async def _resolve_domain(self) -> None:
        url = f"https://extend.eufylife.com/domain/{self._country}"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    domain = data.get("data", {}).get("domain")
                    if domain:
                        self._base_url = f"https://{domain}"
        except Exception:
            pass

    async def async_get_device_serials(self, auth_token: str) -> list[str]:
        serials: list[str] = []
        if not auth_token:
            return serials
        headers = {
            "App-Name": "eufy_mega",
            "Os-Type": "android",
            "Country": self._country,
            "Language": "en",
            "Openudid": uuid.uuid4().hex[:16],
            "X-Auth-Token": auth_token,
        }
        for endpoint in ("/v1/app/get_devs_list", "/v2/house/device_list"):
            try:
                async with self._session.post(
                    f"{self._base_url}{endpoint}",
                    json={},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as d_resp:
                    raw_text = await d_resp.read()
                    for match in SERIAL_REGEX.finditer(raw_text):
                        sn = match.group(1).decode("ascii")
                        if sn not in serials:
                            serials.append(sn)
            except Exception:
                pass
        return serials

    async def async_login(
        self,
        email: str,
        password: str,
        verify_code: str | None = None,
        captcha_id: str | None = None,
        captcha_answer: str | None = None,
    ) -> dict[str, Any]:
        await self._resolve_domain()

        payload: dict[str, Any] = {
            "ab": self._country,
            "client_secret_info": {"public_key": self._client_public_key_hex()},
            "enc": 0,
            "email": email,
            "password": self._encrypt_password(password),
            "time_zone": -time.timezone * 1000,
            "transaction": f"{int(time.time() * 1000)}",
        }
        if verify_code:
            payload["verify_code"] = verify_code.strip()
        if captcha_id and captcha_answer:
            payload["captcha_id"] = captcha_id
            payload["answer"] = captcha_answer.strip()

        headers = {
            "App-Name": "eufy_mega",
            "Os-Type": "android",
            "Country": self._country,
            "Language": "en",
            "Openudid": uuid.uuid4().hex[:16],
        }

        async with self._session.post(
            f"{self._base_url}/v2/passport/login_sec",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()
            if data.get("code") == 0:
                auth_token = data.get("data", {}).get("auth_token", "")
                if auth_token:
                    data["data"]["serials"] = await self.async_get_device_serials(auth_token)
            return data
