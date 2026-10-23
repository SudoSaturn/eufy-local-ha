# Eufy Local Light for Home Assistant

Local control of Eufy string lights as a native Home Assistant light entity: power, brightness, RGB, warm/cool white, and the app's own preset effects over an encrypted Bluetooth Low Energy (BLE) session.

> This is an unofficial community integration built by reverse-engineering the Eufy app's BLE traffic. It is not affiliated with or endorsed by Anker/Eufy. It was developed and tested against the Eufy Outdoor String Lights E10 (model T8L10) and the Eufy Permanent Outdoor Lights E22 (model T8L02). Other lights may work but are untested.

## E22 (Permanent Outdoor Lights, T8L02)

The E22 is auto-discovered (it advertises as `T8L02_xxxx`) and set up through the same flow as the E10. Beyond power, brightness and colour, the E22 adds:

- **Warm and cool white.** The light is exposed as an RGBWW entity, driving its dedicated white LEDs directly rather than approximating white from RGB.
- **The app's preset effects.** Every pre-canned scene from the Eufy app's gallery (Holiday, Nature, Moods, Culture and the Daily white) appears in Home Assistant's effect list. These were captured off a real E22 and are replayed over Bluetooth; see [`PROTOCOL.md`](PROTOCOL.md) and [`custom_components/eufy_local_ha/effects.json`](custom_components/eufy_local_ha/effects.json).

Not yet supported on the E22: per-segment "DIY" patterns (the frame exceeds one BLE write and would need fragmentation).

### State readback

Home Assistant shows the light's real power, brightness and running preset, not just what it last sent: the light pushes a state report over Bluetooth after every change, and the integration also asks for the full state every 30 seconds. A preset started from the Eufy app shows up by name in the effect list. Colour is still tracked optimistically, because the light doesn't report its palette. (Verified on the E22; the E10 shares the protocol but is untested.)

To do this the integration keeps its Bluetooth connection open, reconnecting on the next poll if it drops. The light accepts only one Bluetooth connection, so while Home Assistant holds it the Eufy app can only reach the light over Wi-Fi/cloud.

### But first!

![silly goobers](eufyCommunityScreenshot.png)

And i took that personally.

## Requirements

- Home Assistant :)
- Your light's Bluetooth MAC, serial number, and Eufy user ID (see [Configuration](#configuration))

## Installation

### HACS

1. In HACS, open the three-dot menu and choose Custom repositories.
2. Add `https://github.com/sudosaturn/eufy-local-ha` with category Integration.
3. Install Eufy Local Light, then restart Home Assistant.

### Manual

Copy `custom_components/eufy_local` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Add the integration via Settings > Devices & services > Add integration > Eufy Local Light (it should also be auto discovered over Bluetooth). You will be asked for three values:

| Field | What it is | Where to find it |
| --- | --- | --- |
| Bluetooth MAC | The light's BLE address | should be auto-filled on discovery depending of what model you have but you could also use a BLE scanner like nRF Connect. |
| Serial number | 16-character device serial | On the device label and in the Eufy app device info. |
| User ID | Your Eufy account user ID (hex) | The account that paired the light. |

### Finding your User ID

- Read it from the Eufy Security account API (for example via the community [`eufy-security-ws`](https://github.com/bropat/eufy-security-ws) project), or
- Capture the app's HTTPS or BLE handshake traffic once and read the `user_id` field.

To learn more about how i reversed it and where these are used you can read [PROTOCOL.md](PROTOCOL.md).

## Options: color tuning

Raw sRGB doesnt seem to look right on these LEDs so i adjusted the saturation to to stops light colors from washing out to white (kinda).

(With that being said. i am severly colorblind so it might not actually be as accurate as it seems to me)

you can tune these from Settings > Devices & services > Eufy Local Light > Configure:

| Option | Default | Effect |
| --- | --- | --- |
| Gamma | 1.7 | Higher deepens mixed colors |
| Saturation | 1.0 | 1.0 is fully vivid; lower keeps more pastel |
| Neutral threshold | 0.12 | How close to gray before a color is treated as white |
| White balance, green | 0.85 | Lower if whites look green |
| White balance, blue | 0.55 | Lower if whites look blue; raise if amber |

### If you're having issues and disconnections make sure that you don't have the Eufy app connected! it allows one bluetooth connection at a time and you'd have to re-pair!.


If you're just here to get your lights working this probably won't be your cup of tea but feel free to see [PROTOCOL.md](PROTOCOL.md) for complete breakdown :)

License: [GPL](LICENSE)
