"""Constants for Beat DMX."""
DOMAIN = "beat_dmx"

CONF_FIXTURES = "fixtures"
CONF_GETSONGBPM_KEY = "getsongbpm_api_key"
CONF_DEFAULT_BPM = "default_bpm"

DEFAULT_FIXTURES = "light.left_beam_ch, light.right_beam_ch"
DEFAULT_BPM = 120

PROGRAMS = ["Full show", "Movement", "Colour & gobo", "Pulse", "Strobe"]
SPEEDS = {"Half": 0.5, "Normal": 1.0, "Double": 2.0}

# BeamZ Panther 40 LED Spot, 11-channel mode (manual page 28); offsets from the fixture's first channel
CH_PAN, CH_PAN_FINE, CH_TILT, CH_TILT_FINE, CH_COLOUR, CH_GOBO, CH_SHUTTER, CH_DIMMER, \
    CH_PT_SPEED, CH_FUNCTION, CH_DIM_MODE = range(11)
CHANNEL_COUNT = 11
COLOURS = [3, 11, 18, 25, 32, 39, 46, 53]      # white, colour 1-7 (middle of each range)
# what the wheel's slots really look like (user, 2026-10-15); the live card and the 3D view draw with these
COLOUR_WHEEL = [("White", "#ffffff"), ("Red", "#ff2a2a"), ("Light Green", "#86ff7a"), ("Light Blue", "#7fd2ff"),
                ("Green", "#1fd23c"), ("Purple", "#a03cff"), ("Orange", "#ff8a1a"), ("Blue", "#2a4bff")]
GOBOS = [3, 11, 19, 27, 35, 43, 51, 59]        # open, gobo 1-7
SHUTTER_OPEN = 10
STROBE_SLOW, STROBE_FAST = 16, 131
PT_SPEED_FAST = 0
FUNCTION_NONE = 0
DIM_MODE_STANDARD = 0
