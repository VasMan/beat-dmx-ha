# Beat DMX

A Home Assistant integration that moves DMX fixtures to the tempo of whatever
[Music Assistant](https://music-assistant.io) is playing.

It follows the Music Assistant player that is playing, looks up the track's tempo (BPM) and drives
Art-Net fixtures on the beat: movement every beat, colour changes every bar, gobo changes every two bars,
dimmer pulses or a strobe whose speed follows the tempo.

## Tempo data

Tempo data is provided by **[GetSongBPM.com](https://getsongbpm.com)**.

Tempo is looked up in this order:

1. a tempo you tapped in for the track earlier (tap tempo button, remembered per track)
2. Deezer, by the track's ISRC
3. the [GetSongBPM API](https://getsongbpm.com/api) (if an API key is configured)
4. Deezer search (median over matching releases; remixes, covers and acoustic versions are ignored)
5. a default tempo (120 BPM unless configured)

## Requirements

- Home Assistant 2025.6 or newer
- The [Music Assistant integration](https://www.home-assistant.io/integrations/music_assistant/)
- Fixtures set up with [ha-artnet-led](https://github.com/Breina/ha-artnet-led) as one `dimmer` light per DMX channel,
  named `<prefix>1` … `<prefix>11` (for example `light.left_beam_ch1` … `light.left_beam_ch11`)

Beat DMX writes to ha-artnet-led's own channels, so both share one Art-Net output. When beat sync stops, every
channel goes back to what its light entity says.

## Fixture profile

Version 0.1 knows one fixture: the **BeamZ Panther 40 LED Spot** in **11-channel mode**
(pan, pan fine, tilt, tilt fine, colour, gobo, shutter, dimmer, pan/tilt speed, function, dim mode).
Pan and tilt are sent as 16-bit values (the fine channels are used).

## Movement zone and calibration

The heads aim inside a **zone**: up to *Zone width* degrees either side of each head's forward direction,
with the beam between the *Lowest* and *Highest beam angle* above level. The zone is the same for every
head, seen from that head. **Movement shapes**: Square, Circle, Figure 8, Sweep, Random. **Coordination**
of every second head: Mirror, Unison (both the same), Opposite (mirrored left/right and up/down) or
Follow (one step behind).

Each head is calibrated once, with **Manual DMX** on (the live card's sliders, or the pan/tilt pad):
aim it straight up and press *It points straight up*; aim it level into the middle of the room and press
*It points level, into the room*. *Flip pan direction* if it turns the wrong way. Until then a floor-standing
head with tilt 128 = straight up and pan 128 = forward is assumed.

## Installation

HACS → Custom repositories → add `https://github.com/VasMan/beat-dmx-ha` (Integration) → install → restart.
Then Settings → Devices & services → Add integration → **Beat DMX**, and enter:

- **Fixtures**: light entity prefixes, comma separated, e.g. `light.left_beam_ch, light.right_beam_ch`
- **GetSongBPM API key** (optional)
- **Default tempo**

## Entities

| Entity | |
|---|---|
| Beat sync (switch) | on/off |
| Tempo (sensor) | current BPM; attribute `source` says where it came from |
| Now following (sensor) | track and player |
| Program (select) | Full show, Movement, Colour & gobo, Pulse, Strobe |
| Step speed (select) | Half, Normal, Double |
| Manual DMX (switch) | hold values set by hand (testing, calibrating) |
| Movement shape, Coordination (selects) | how the heads move, and together |
| Intensity, Tempo override (numbers) | override 0 = automatic |
| Zone width, Lowest / Highest beam angle (numbers) | the movement zone |
| Tap tempo (button) | 4+ taps set the tempo for the track; every tap re-aligns the beat |

## Live DMX card

The integration serves a dashboard card that shows the DMX values going out to each fixture, live:

```yaml
type: custom:beat-dmx-card
fixtures:            # optional: show these first, in this order
  - light.right_beam_ch
  - light.left_beam_ch
layout: stacked      # optional: fixtures under each other instead of side by side
```

No Lovelace resource is needed; the card is loaded automatically. It shows where the tempo was found,
and with Manual DMX on its bars become sliders, the pan/tilt pad can be dragged and each head calibrated.

## Services

- `beat_dmx.set_channel` (fixture, channel 1-11, value 0-255): manual DMX (turns Manual DMX on)
- `beat_dmx.calibrate` (fixture, point: `up`, `level`, `reverse_pan`, `reset`)

## Notes

Option A of tempo following: the tempo comes from a lookup, not from listening to the audio, so the effects run
at the right speed but can drift off the beat. Tap the tempo button on the beat to re-align.
