"""Constants for the Eufy Local Light integration."""
from __future__ import annotations

DOMAIN = "eufy_local"
PLATFORMS = ["light"]

CONF_SERIAL_NUMBER = "serial_number"
CONF_USER_ID = "user_id"

CONF_GAMMA = "color_gamma"
CONF_SATURATION = "color_saturation"
CONF_ACHROMATIC_RATIO = "achromatic_ratio"
CONF_WHITE_BALANCE_G = "white_balance_g"
CONF_WHITE_BALANCE_B = "white_balance_b"

DEFAULT_GAMMA = 1.7
DEFAULT_SATURATION = 1.0
DEFAULT_ACHROMATIC_RATIO = 0.12
DEFAULT_WHITE_BALANCE_R = 1.0
DEFAULT_WHITE_BALANCE_G = 0.85
DEFAULT_WHITE_BALANCE_B = 0.55

DEVICE_MANUFACTURER = "Eufy"
DEFAULT_DEVICE_NAME = "Eufy Local Light"

CHAR_WRITE = "8c850002-0302-41c5-b46e-cf057c562025"
CHAR_NOTIFY = "8c850003-0302-41c5-b46e-cf057c562025"

LOCAL_NAME_PREFIX = "T8L10"

SERVICE_SET_BRIGHTNESS = "set_brightness"
