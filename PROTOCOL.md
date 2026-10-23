# Eufy T8L10 BLE protocol notes

Reverse engineered from the official Eufy app's BLE traffic for the E10 Lights (T8L10). Field names (`a1`, `a3`, `b0`, ...) are the app's own TLV tags. This is best-effort documentation of one device's firmware (`2.0.4.0`); other models may differ.

## GATT

| Role | UUID |
|-|-|
| Write (commands) | `8c850002-0302-41c5-b46e-cf057c562025` |
| Notify (responses) | `8c850003-0302-41c5-b46e-cf057c562025` |

The light advertises a local name of `T8L10_XXXX`.

## Packet framing

```
ff 09 | <len: u16 LE> | <seq: u16 LE> | <type: u8> | <opcode: u16 BE> | <payload> | <xor checksum: u8>
```

 `len` = payload length + 10.

 `seq` increments per packet (wraps 0xFFFF -> 1).

 `type` = 1 for handshake packets, 2 for control packets.

 `checksum` = XOR of every preceding byte in the packet.

### Encryption

Control packets are AES-128-CBC encrypted:

- The negotiated session key (before negotiation, the *initial key* = `user_id[:16]` as ASCII).
- IV as the 16-character device serial number as ASCII.
- PKCS#7 Padding.
- When a payload is encrypted, the opcode is OR'd with `0x4000`.

### Common payload prefix (`base`)

Most payloads start with a timestamp and the account user ID:

```
a1 04 <unix_time: u32 LE>  a2 28 <user_id: 40 ASCII bytes>
```

A new timestamp is generated for every command, mirroring the app.

## Handshake

| Step | Opcode | Encrypted | Payload |
| --- | --- | --- | --- |
| 1 | `0x0001` | no | `base` |
| 2 | `0x0029` | no | `base` |
| 3 | `0x0003` | no | `base + a3 01 20 a4 02 96 00` |
| 4 | `0x0005` | no | `base + a3 01 20 a4 02 96 00` |
| 5 | `0x0022` | yes (initial key) | `base + a3 04 e0 e3 ff ff` |

The device replies with a notification whose bytes `[7:9]` are `48 22`. Decrypt the body (`raw[9:-1]`) with the initial key and the 16 bytes following the `a1 10` marker are the session key used for all later control packets. A ~150 ms delay between handshake steps usually makes it more reliable.

## Control commands

All use `type = 2` and are encrypted with the session key.

### Power — `0x0201`

```
base + a3 01 <01=on | 00=off>
```

### Brightness — `0x0201`

```
base + a4 01 <0..100>
```

Brightness (`lv`) is a persistent strip setting on `setUpStrip` (`0x0201`), the sibling of the power field. 

Captured tails: `a4 01 21` = 33%, `a4 01 64` = 100%, `a4 01 01` = 1%. The `a8` field of the color frame is not brightness and the firmware ignores it.

### Solid color — `0x0206`

The full frame the app sends for a solid color (after `base`):

```
a3 02 26 4e
a5 01 05
a6 06 01 <R> <G> <B> 00 00      # color
a7 1f 1e 00 01 02 ... 1d          # per-bulb map (30 bulbs)
a8 01 64                          # brightness placeholder (ignored)
a9 05 00 00 00 00 00
aa 01 00
ac 04 00 00 00 00
ae 01 00
b0 01 07                          # 07 = SOLID-COLOR MODE (decisive)
```

Without `b0 = 07` the strip stays in its previous blend/effect mode and averages the color toward white, so it looks washed out.

## Color rendering

Raw sRGB doesnt seem to look right on these LEDs so i adjusted the saturation to remove the `min(R,G,B)` and rescale so the brightest channel is unchanged. 

This stops light colors from washing out to white (kinda).

If the color is close to neutral it is instead sent as a warm white because equal RGB reads as blue.

(With that being said. i am severly colorblind so it might not actually be as accurate as it seems to me)

Brightness is sent separately so the color frame only needs to carry hue and saturating it fully doesnt drop intensity.

## Device-specific values (example)

The reference device used during reverse-engineering:

| Field | Value |
| --- | --- |
| Model | T8L10 (Outdoor String Lights E10) |
| Firmware | 2.0.4.0 |
| AES IV | serial number (16 ASCII chars) |
| Initial AES key | `user_id[:16]` (16 ASCII chars) |

## E22 (Permanent Outdoor Lights, T8L02) additions

The E22 shares the GATT, framing, encryption and handshake above. It was
reverse-engineered on firmware `2.0.4.0` by sniffing the Eufy app's cloud
(MQTT) traffic — the command frames are identical over MQTT and BLE, so the
captures replay over BLE unchanged. Differences from the E10 notes:

### Colour is RGBWW, not RGB

The `0x0206` colour frame's `a6` palette carries **five** channels per colour,
not three: `R, G, B, warm-white, cool-white`. The last two drive dedicated
white LEDs, so neutral/white does not have to be approximated from RGB.

```
a6 06 01 <R> <G> <B> <WarmWhite> <CoolWhite>
```

### Effects use two different opcodes

- **Simple "dynamic" colour** uses `0x0206` with effect id `20000` (vs `20006`
  for a static per-segment DIY pattern), an `a5` speed byte, the five-channel
  `a6` palette, and the `a7` per-segment map. `b0 = 07`.
- **The app's named presets** are sent differently depending on complexity:
  - *Solid presets* (e.g. "Daily: Warm White") use `0x0206` with a low effect
    id (e.g. `45`), the preset's cloud id in `ac`, no `a7`, and `b0 = 00`.
  - *Animations* (all the Holiday/Nature/Moods/Culture scenes) use `0x020D`,
    the multi-layer animation frame: `a3` cloud id (4B LE), `a4` speed (1B),
    `a5` layer count, `a6` execution mode, `a8` mode flag, then one `a9+i`
    layer blob per layer. The E22 renders these directly and acks with
    `0x0A0D`; unlike the floor-lamp family it needs **no** `0x0200` handshake
    first.

Captured preset frames are shipped in
`custom_components/eufy_local_ha/effects.json` as `{opcode, tail}`, where
`tail` is everything after the `a1`/`a2` envelope; the integration prepends a
fresh envelope and replays them.

### Device-specific values (example)

| Field | Value |
| --- | --- |
| Model | T8L02 (Permanent Outdoor Lights E22) |
| Firmware | 2.0.4.0 |
| Segments | 20 (reported in the `0x0204` state frame) |
| AES IV | serial number (16 ASCII chars) |
| Initial AES key | `user_id[:16]` (16 ASCII chars) |

Note: the controller accepts an all-zero user id during the handshake, so
control does not strictly require a real Eufy account id — which also means
anyone in BLE range can control the lights.
