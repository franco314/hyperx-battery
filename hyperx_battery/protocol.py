"""HID protocol of the HyperX Cloud Flight for PS dongle (pure: no I/O).

Verified on real hardware (VID 0951 / PID 1749):
  * Control collection: usage_page 0xFF90, usage 0x0303.
  * Battery request: 20 bytes ``21 FF 05`` + zeros.
  * Battery reply:   20 bytes ``21 FF 05 HI LO ...``; voltage in mV = (HI << 8) | LO.

NOT verified on this model (kept as best-effort hints, the app degrades
gracefully if they never show up):
  * ``65 04`` = microphone muted, ``65 xx`` (other value) = microphone active.
  * ``64 01`` = headset powered on, ``64 03`` = headset powered off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Union

VID = 0x0951
PID = 0x1749

CONTROL_USAGE_PAGE = 0xFF90
CONTROL_USAGE = 0x0303

BATTERY_REQUEST = bytes([0x21, 0xFF, 0x05]) + bytes(17)
BATTERY_REPLY_PREFIX = (0x21, 0xFF, 0x05)

EVENT_POWER = 0x64
EVENT_MUTE = 0x65
POWER_ON = 0x01
POWER_OFF = 0x03
MUTE_ON = 0x04

# Sanity range for a Li-ion cell voltage reported in mV (anything else is noise).
MIN_VALID_MV = 2500
MAX_VALID_MV = 6000


@dataclass(frozen=True)
class BatteryReport:
    mv: int


@dataclass(frozen=True)
class MuteReport:
    muted: bool


@dataclass(frozen=True)
class PowerReport:
    on: bool


Report = Union[BatteryReport, MuteReport, PowerReport]


def battery_mv(hi: int, lo: int) -> int:
    """Voltage in millivolts from the two reply bytes."""
    return ((hi & 0xFF) << 8) | (lo & 0xFF)


def parse_report(data: Union[bytes, bytearray, Sequence[int]]) -> Optional[Report]:
    """Decode a raw HID report. Returns None for anything not understood."""
    if len(data) < 2:
        return None
    if len(data) >= 5 and tuple(data[:3]) == BATTERY_REPLY_PREFIX:
        mv = battery_mv(data[3], data[4])
        if MIN_VALID_MV <= mv <= MAX_VALID_MV:
            return BatteryReport(mv)
        return None
    if data[0] == EVENT_MUTE:
        return MuteReport(muted=data[1] == MUTE_ON)
    if data[0] == EVENT_POWER:
        if data[1] == POWER_ON:
            return PowerReport(on=True)
        if data[1] == POWER_OFF:
            return PowerReport(on=False)
    return None


def hex_dump(data: Union[bytes, bytearray, Sequence[int]], limit: int = 64) -> str:
    """Space separated hex of a report, trimmed to ``limit`` bytes."""
    shown = bytes(data[:limit])
    text = " ".join(f"{b:02x}" for b in shown)
    return text + (" ..." if len(data) > limit else "")
