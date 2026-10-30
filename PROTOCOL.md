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

### Solid-colour frame fields on the E22

Tested by replaying variations of the `0x0206` solid-colour frame above:

- **`a7` is the list of segments to colour**, after a count byte (one such
  group per colour; see below). The E22
  has **20 segments** (its state report gives `a3` = `14`); the 30-entry map
  the integration sends colours all of them and entries past 19 are ignored.
  A map of `0`–`9` coloured only the half nearest the controller, and
  `0, 2, 4 …` every other light.
- **`a3` (u16 LE) picks a built-in effect** for the colour layout. Tested
  on a layout of red on one half and blue on the other:

  | Effect id | Effect |
  | --- | --- |
  | `20000` (`20 4e`) | chase, from the controller end outward |
  | `20001` (`21 4e`) | chase, the other way |
  | `20002` (`22 4e`) | the colours swap places back and forth |
  | `20003` (`23 4e`) | each part fades between the colours |
  | `20004` (`24 4e`) | flash on and off |
  | `20005` (`25 4e`) | breathe, all in sync |
  | `20006` (`26 4e`) | static (what the integration sends) |

- **`a5` is the speed** of the effect: `01` slow, `05` (what the
  integration sends) medium, `0a` fast. `0f` and `14` were rejected, so the
  maximum is somewhere from `0a` to `0e`.
- **`b0`** accepts `00`, `06` and `07` and rejects `01`, `03` and `05`. None
  of the accepted values visibly changed a moving two-colour layout; `07` is
  the value that keeps a solid colour from washing out (see above).
- A rejected `0x0206` is acked (`0x0A06`) with a first byte of `01`, like
  `0x020D`, and the previous scene keeps running.
- **Several colours: one group per colour.** `a6` is a colour count followed
  by that many 5-byte colours, and `a7` holds one group per colour, in the
  same order: a segment count, then that many segment numbers. For example
  red on the half nearest the controller and blue on the other half:

  ```
  a6 0b 02  ff 00 00 00 00  00 00 ff 00 00
  a7 16 0a 00 01 02 03 04 05 06 07 08 09  0a 0a 0b 0c 0d 0e 0f 10 11 12 13
  ```

  Every colour needs its group. When `a7` ran out early the light kept
  reading groups from the bytes after it (the `a8 01 64 a9 05 …` that
  follow), painting whichever of those bytes were valid segment numbers —
  which is how a two-colour palette with a one-group map lit segments 0, 1,
  4, 5 and 7 in the second colour.
- The state report's `a7` reads `03` while a solid colour is showing (`02`
  for an animation, `00` when off).

### What a preset animation frame contains

A `0x020D` preset is a complete description of the animation, not a
reference to one stored in the light. The cloud id in `a3` is only a label:
the light echoes it back in its state report but doesn't use it to draw.
Confirmed on an E22 by replaying "Culture: Spain" (a red/orange-yellow chase)
with only its palette changed — the same chase played in the new colours —
and with a made-up cloud id, which looked identical to the original.

The frame-level fields set by the preset:

- `a4` is the **scene speed** (higher is faster: `08` slow, `28` Spain's,
  `60` fast).
- `a5` is the layer count.
- `a6` sets **how the layers enter**: `00` all start together; `01` and `02`
  the layer behind joins about a second after the front one; `03` it fades
  in. (Tested with a chase in front of a fill.)
- `a8` is `00` in every preset, and `01` was rejected by the light.

A rejected `0x020D` is acked with a first byte of `01`, and the previous
scene keeps running. The last byte of the ack differed between the two
rejections seen (`01 a1 01 01` and `01 a1 01 02`), so it may say what was
wrong.

The meanings below come from replaying single-byte changes on a real E22
and watching the strip — first on Spain's layer, then on one layer with a
single colour (red), which makes the cell structure easy to see. "Not
mapped" bytes were not changed or showed no visible effect.

Each `a9+i` layer blob:

| Offset | Meaning |
| --- | --- |
| 0 | **depth**: lower is in front (see *How layers combine*) |
| 1 | **layer speed**, higher is faster (`05` slow, `18` Spain's, `60` very fast) |
| 2 | end of the layer's stretch of strip, % (0–100) |
| 3 | start of the layer's stretch of strip, % |
| 4 | not mapped (usually `01`; `02` made no visible change) |
| 5 | **scale**: each light of the pattern becomes a group of this many (`03` turned "1 on, 2 off" into "3 on, 6 off"); `01` in almost every preset |
| 6–7 | **how the scene starts.** `00 80`, `01 80` and `00 8b` go straight into the animation; `00 82`, `00 00`, `00 81`, `00 0d`, `01 0d`, `00 83`, `00 85`, `00 86` and `00 01` first play a few seconds of fast rainbow (some also flash a rainbow when switched off). None changes the animation itself |
| 8 | not mapped; `03` lit only part of the strip, `01` made no visible change |
| 9 | **parameter-block format**: `00` → 17 bytes, `01` → 10 bytes, `02` → 12 bytes (holds for all 193 captured layers) |
| 10 | colour count `N` |
| 11.. | `N` × 5-byte colours: `R G B WarmWhite CoolWhite` |
| then | the parameter block |

Parameter block, format `00` (17 bytes, `p0`–`p16`; Spain's is
`00 00 03 00 00 00 00 03 00 00 00 00 00 64 00 00 01`):

| Byte | Meaning |
| --- | --- |
| p0, p1 | **fading**: p0 = `01` with a non-zero p1 makes each step fade on and off instead of switching; p1 = `ff` and `be` looked about the same. The presets pair p0 = `00` with p1 = `00` |
| p2 | **where the motion starts**: `00` from the left (controller end), `01` from the right, `02` from both ends inward, `03` from the centre outward; higher values fall back to `00` |
| p7 | **cell length** in lights: each palette entry fills one cell, and the palette repeats along the strip |
| p12 | **how a cell is lit**: `00`/`01` all on, `03` one third on (with a 3-light cell: 1 on, 2 off), `04` head plus a dim tail (1 on, 1 dim, 1 off) |
| p5 | no visible change (`00`→`01`) |
| p10 | **repeat**: `00` repeats the cell pattern along the strip; `ff` sends a single light along the whole strip |
| p13 | `64` in 111 of 114 layers; `2d` made no obvious difference, so probably not brightness |
| p14 | **brightness of the unlit part of a cell** (`11` turned "1 on, 2 off" into "1 on, 2 dim") |
| p15, p16 | no visible change (`p15` `00`→`01`, `p16` `01`→`02`) |
| others | not mapped |

Parameter block, format `02` (12 bytes): a **breathing** effect. Swept on
one layer with a single colour from the baseline
`03 64 0f 01 00 00 00 ff 00 00 00 01`, which breathes the whole strip
bright and dim:

| Byte | Meaning |
| --- | --- |
| p0 | **waveform**: `03` smooth breathing (`01` looked about the same), `04` a quick rise then a slow fade |
| p1 | `64` in 61 of 62 layers; not tested |
| p2 | **how dim it gets** (likely a %): `0f` and `32` both breathe, `64` stays static at full brightness |
| p3 | `02` (from `01`) widened the brightness range; not mapped further |
| p4 | `01` (from `00`) made the fade slightly more abrupt; not mapped further |
| p5 | **spacing**: number of dark lights between lit ones (`00` none … `04` four) |
| p6 | `02` was **rejected** — the `0x0A0D` ack carried status `01` and the light kept its previous scene |
| p7 | **how many lights breathe at once**: `ff` all together; `01` one light at a time, travelling along an otherwise dim strip |
| p9, p10–p11 | no visible change (`p9` `00`→`02`; `p10 p11` `00 01`→`01 00`) |
| others | not mapped |

Parameter block, format `01` (10 bytes): a **fill**, the usual background
layer of multi-layer scenes. Swept on one layer with the baseline
`64 00 00 00 00 00 00 00 00 01`; with a single colour it is a static solid
fill, so the bytes below were mapped with a two-colour palette (red, blue):

| Byte | Meaning |
| --- | --- |
| p0 | `64` in most layers; `32` made no visible change |
| p1 | **layout**: `00` the palette colours alternate light by light; `01` a static gradient from the first colour (controller end) to the last |
| p2 | no visible change (`01`, `02`) |
| p3 | **animation**: `00` static; `01` the colours swap places back and forth; `02` each light cross-fades between the colours, alternate lights out of step |
| p4, p8–p9 | no visible change (`p4` `00`→`01`; `p8 p9` `00 01`→`01 00`) |
| p7 | `01` lays the colours out in irregular, random-length runs |
| others | not mapped |

The red-to-blue gradient (p1 = `01`) ran through the hues in between
(yellow, green); combined with the cross-fade (p3 = `02`) it showed purple
in the middle instead. Why is not known.

#### How layers combine

Tested with a blue fill layer and a red chase layer (1 on, 2 off):

- **Layer byte 0 is depth; lower is in front.** Wherever a front layer
  lights a light, layers behind it don't show there: a red chase at `00`
  over a blue fill at `01` (or `01` over `02`) shows pure red over blue, and
  a chase behind a full-strip fill is hidden entirely.
- **Layers at the same depth add their colours**: the same red chase over the
  blue fill, both at `00`, shows pink over blue.
- **Layers behind keep running.** A full-strip chase behind a half-strip fill
  shows only on the uncovered half, still moving along as before.
- The order of the `a9+i` blobs in the frame makes no difference, and each
  layer stays within its own start–end range.

Many animations — those with an intro, a delayed layer (`a6` ≠ `00`) and
some cell styles — make the light send a second, identical `0x0204` report
about 1.7 s after the `0x0A0D` ack. What exactly triggers it is not known.

For example Spain's single layer is

```
00 18 64 00 01 01 00 80 00 00 03  ff 00 00 00 00  ff 7d 00 00 00  ff 00 00 00 00
00 00 03 00 00 00 00 03 00 00 00 00 00 64 00 00 01
```

— the whole strip (0–100 %), `00 80` in bytes 6–7, and three colours: red,
orange-yellow, red. With a 3-light cell (p7) it shows 6 red and 3 yellow
per set (the two reds join); with p2 = `03` it starts from the centre. Many scenes share a template and differ only in palette
(e.g. "Christmas Day 3", "Misty Rain" and "Candy Land" are the same
four-layer layout over 0–25/25–50/50–75/75–100 %).

### Reading state back

The light reports its own state over the notify characteristic, encrypted
with the session key like any control frame (opcode has `0x4000` set; decrypt
`raw[9:-1]`).

- **`0x0204` state report** — pushed unprompted after every change, whoever
  made it (also after a no-op write such as re-sending the current
  brightness). The plaintext is bare TLVs, no status byte:

  ```
  a1 01 <power: 01 on, 00 off>
  a2 01 <brightness 0..100>
  a3 01 <segment count>            # 0x14 = 20 on the reference unit
  a4 04 <effect id, u32 LE>        # 0x020D: the cloud id; 0x0206: its a3 id
  a5 01 00                         # unknown
  a6 04 <cloud id, u32 LE>         # the app preset's id; 0 for a plain colour
  a7 01 <02 while on, 00 when off>
  ```

- **`0x0200` status query** — `base + a3 <n> <mask>`, where the mask selects
  fields by bit: bit 0 power (`a1`), 1 brightness (`a2`), 2 segments (`a3`),
  3 effect id (`a4`), 4 `a5`, 5 cloud id (`a6`), 6 a 16-byte `a7` (all zero
  here), 7 `a8` (`02`); a 2-byte mask also returns `a9`, the power on/off
  effect (see below). `a3 02 ff ff` asks for everything. The reply is
  `0x0A00`: a status byte (`00` = ok) followed by the requested TLVs. Field
  widths differ from the report — `a3` segments is 2 bytes and `a4` effect id
  2 bytes here — so read numbers little-endian at whatever length is given.
  `0x0200` with no `a3` mask answers just `04`.

  ```
  00 a1 01 01  a2 01 64  a3 02 14 00  a4 02 2d 00  a5 01 00
     a6 04 3b 75 00 00  a7 10 00..00  a8 01 02  a9 01 00
  ```

After a `0x020D` animation the report can also carry an `a9` field
(12 bytes: `02 00 00 00`, the cloud id as u32 LE, then a u32 that changes
between reports); its meaning is unknown. It is unrelated to the 1-byte `a9`
of the status reply.

### Power on/off effect

By default the controller plays a short rainbow sweep when the lights are
switched on and a rainbow wipe when they are switched off, whatever scene is
showing. The eufy app has a setting to turn this off; it is stored on the
controller.

- **Set it** with `0x0201` (like power and brightness):

  ```
  base + a7 01 <00 = effect on, 01 = effect off>
  ```

- **Read it** as `a9` in the `0x0A00` status reply (`00` on, `01` off). The
  value changes within a second of the command, but the light pushes no
  `0x0204` report for it, so it has to be polled.

Confirmed on an E22, both ways, by watching the lights switch on and off.
When the app changes the setting over the cloud, the status reply shows it
too.

`0x0201` acks unknown tags with status `00` and ignores them, so trying
tags there won't tell you which ones are real.

### Blockout — `0x0201` `a6`

The eufy app's "blockout" holds chosen lights permanently off, whatever
scene or colour is showing. It is another controller setting on `0x0201`:

```
base + a6 03 <mask: u24 LE>
```

Bit *n* holds light *n* + 1 off, counting from the controller end; `00 00
00` clears it. Worked out from a capture of the app blocking out each group
of four lights along a 20-light string in turn:

| Lights held off | `a6` |
| --- | --- |
| 1–4 | `0f 00 00` |
| 5–8 | `f0 00 00` |
| 9–12 | `00 0f 00` |
| 13–16 | `00 f0 00` |
| 17–20 | `00 00 0f` |
| 1, 3, 5, 7, 9 | `55 01 00` |
| 11, 13, 15, 17, 19 | `00 54 05` |

The light acks each with `0x0A01`, status `00`, and reports the mask back
as `a7` in the `0x0A00` status reply: 16 bytes, little-endian, with the same
bit order (`a7 10 0f 00 …` with lights 1–4 held off, `a7 10 00 01 08 00 …`
with lights 9 and 20). The pushed `0x0204` report does not carry it; its
one-byte `a7` is unrelated. A 16-byte mask has room for 128 lights, so the
3-byte `a6` may grow on longer strings; only a 20-light E22 has been tried.

Confirmed on an E22 by watching the string: `0f 00 00` darkened the four
lights nearest the controller, and `00 01 08` lights 9 and 20. The
controller accepts and reports the mask even with no string plugged in.

### Schedules — `0x0207` / `0x0208`

The controller runs scene schedules itself. Adding one in the app (the
Halloween scene, default start and stop times) sent the scene's `0x020D`
frame and then `0x0207`; removing it sent `0x0208`. Both ack with status
`00` (`0x0A07`, `0x0A08`). Fields as captured, with a tentative reading:

```
0x0207: base + a3 02 f3 ff         id (0x0208 removes by it)
             + a4 01 01            enabled?
             + a5 02 0a 1d         start date: month, day (29 Oct)
             + a6 02 0a 1d         end date: month, day
             + a8 02 12 00         start time: hour, minute (18:00)
             + a9 02 06 00         stop time: hour, minute (06:00)
             + aa 04 04 29 00 00   scene cloud id (10500)
             + b3 04 00 00 00 00   unknown
             + b4 04 00 00 00 00   unknown
             + b5 01 00            unknown
             + b6 01 50            brightness (80%)

0x0208: base + a3 02 f3 ff         id
```

Added at 20:03, inside its 18:00–06:00 window, the schedule started the
scene at once (the status reply showed it on at 80%, with cloud id 10500
in `a6`), and removing it turned the lights off. So the controller must keep
its own clock; nothing seen over Bluetooth sets it.

The scene's cloud id (10500) is not the one the Halloween preset carries
when played directly (10475, see `effects.json`), although the app sent
the same scene's frames just before; the scheduler may use its own copy.

Not seen over Bluetooth at all: the app's device information page, its
installation-space settings and its firmware check, which presumably go
over the cloud.

The palette itself is not reported, so a colour set by `0x0206` can only be
tracked optimistically. Each command is also acked with `0x0A01`/`0x0A06`/
`0x0A0D` carrying a status byte.

### Wi-Fi network — `0x0032`

The eufy app sets the controller's Wi-Fi network over Bluetooth, after the
usual handshake. Captured from the app on an iPhone (Apple's Bluetooth
logging profile, then a sysdiagnose's `bluetoothd-hci-*.pklg`), changing the
network to a WPA2 one:

```
type 2, encrypted with the session key
base + a3 <n> <SSID>
     + a4 01 00
     + a5 01 00
     + a6 <n> <password>
```

The SSID and password are plain bytes, not padded or terminated. `a4` and
`a5` were both `00`; they are probably the security type and a hidden-network
flag, but a capture of another kind of network would be needed to tell.

The light answers with `0x0832` carrying a status byte, `00` when it accepts
the network. The reply comes 7 to 11 seconds after the write, not
immediately (measured on an E22: about 7 s re-sending the network it was
on, about 11 s moving it to a different one).
What a non-zero status means is not yet known.

While its Wi-Fi screen is open the app also sends `0x0026` with no fields
(type 1, encrypted) every 10 seconds, which the light acks with `0x0826`,
status `00`: a keepalive.

The app's `0x0022` key request carries `a3 04 00 00 00 00` rather than
`e0 e3 ff ff`; the light hands out a session key either way.

**This exposes the Wi-Fi password to anyone listening.** The initial key is
the first 16 characters of the user id, which the handshake sends in clear
text in `a2`, and the session key comes back encrypted under it. Anyone who
records a setup can decrypt the password from the recording.

### Split frames

A command too long for one write is sent as several frames that share one
sequence number. Each has `0x8000` set in its opcode (so an encrypted
`0x0033` goes out as `0xC033`) and starts its payload with one byte, the
fragment's number in the high nibble and the total in the low one (`0x12`,
then `0x22`). The rest of each payload is a slice of the one AES-CBC
ciphertext: join the slices, then decrypt. The app cut after 129
ciphertext bytes, giving a 140-byte first frame.

### Onboarding — first setup of a new light

Captured from the app adding a factory-new E22 to an account. After the
usual handshake (`0x0001`, `0x0029`, `0x0003`, `0x0005`, `0x0022`; the new
light took the account's user id like any other), the app sent:

| Opcode | Payload after `base` | Reply | What it seems to be |
| --- | --- | --- | --- |
| `0x0023` (type 1, encrypted) | `a3 10 <serial>` | `0x0823`, `00` | Confirm which light this is |
| `0x0300` (type 2, encrypted) | none | `0x0B00`, `00`, then `a1` 32 hex chars, `a2` 32 hex chars, `a3` 10 digits | Identity values for registering the light with the cloud |
| `0x0030` (type 1, encrypted) | none | `0x0830`, `00` | Start setup |
| `0x020A` (type 2, encrypted) | `a3 <POSIX TZ>` `a4 <IANA zone>` | `0x0A0A`, `00` | Time zone, e.g. `GMT0BST,M3.5.0/1,M10.5.0` and `Europe/London` |
| `0x0032` | see above | `0x0832`, `00` after ~7 s | Wi-Fi network |
| `0x0033` (type 2, encrypted, split) | see below | `0x0833`, `00`, `a1 04 00 00 00 00` after ~11 s | Cloud registration |

`0x0300`'s values differ per light and look like secrets, so they are not
reproduced here.

`0x0033` carries:

```
a3 <API base URL>      https://aiot-light-api-eu.eufylife.com
a4 <POSIX TZ>          GMT0BST,M3.5.0/1,M10.5.0
a5 <country>           GB
a6 <app>               eufy_life
a7 <model>             T8L02
a8 <IANA zone>         Europe/London
aa <device name>       the light's name in the app
```

No MQTT broker, certificate or account token is sent over Bluetooth, so the
light must get those from the API at `a3`.

### Repair — re-joining Wi-Fi and the cloud

When an already-registered light is offline, the app (with Bluetooth on)
says its Wi-Fi is not configured. That judgement comes from the cloud: no
Bluetooth query for it was seen. Its fix is the tail of onboarding: `0x0032`
with the network, then `0x0033` with the same fields as above (including the
light's name), sent 37 ms after the light's `0x0832`. The light answered
`0x0833` 11 seconds later and was back online.

The order matters. Sending `0x0033` first and `0x0032` after left the light
offline, and no `0x0833` ever came; the reply seems to arrive only once the
light has reached the API.

### Pointing the light at another API

The light acts on `0x0033` at once. Sent with `a3` set to
`https://<a LAN address>`, it opened a TLS connection to that address within
a second, nine times about 2.5 s apart, then gave up. Each time it finished
the TLS handshake with a self-signed certificate and then closed the
connection without sending a request, so it seems to check the server
certificate after the handshake. With an `http://` address it made no
connection at all. Neither attempt returned a `0x0833`, and restoring the
eufy URL took the repair sequence above.

Whether it would accept a certificate from a public CA for a hostname
under one's own control (rather than only eufy's) is untested.

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
