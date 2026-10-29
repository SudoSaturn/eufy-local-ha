## E22 (Permanent Outdoor Lights, T8L02)

The E22 is auto-discovered (it advertises as `T8L02_xxxx`) and set up through the same flow as the E10. Beyond power, brightness and colour, the E22 adds:

- **Warm and cool white.** The light is exposed as an RGBWW entity, driving its dedicated white LEDs directly rather than approximating white from RGB.
- **The app's preset effects.** Every pre-canned scene from the Eufy app's gallery (Holiday, Nature, Moods, Culture and the Daily white) appears in Home Assistant's effect list. These were captured off a real E22 and are replayed over Bluetooth; see [`PROTOCOL.md`](PROTOCOL.md) and [`custom_components/eufy_local_ha/effects.json`](custom_components/eufy_local_ha/effects.json).

Not yet supported on the E22: per-segment "DIY" patterns. The frame format is now known (see [`PROTOCOL.md`](PROTOCOL.md)) and fits in a single Bluetooth write, but the integration doesn't expose it yet.

### State readback

Home Assistant shows the light's real power, brightness and running preset, not just what it last sent: the light pushes a state report over Bluetooth after every change, and the integration also asks for the full state every 30 seconds. A preset started from the Eufy app shows up by name in the effect list. Colour is still tracked optimistically, because the light doesn't report its palette. (Verified on the E22; the E10 shares the protocol but is untested.)

To do this the integration keeps its Bluetooth connection open, reconnecting on the next poll if it drops. The light accepts only one Bluetooth connection, so while Home Assistant holds it the Eufy app can only reach the light over Wi-Fi/cloud.
