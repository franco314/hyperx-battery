from hyperx_battery import protocol
from hyperx_battery.protocol import BatteryReport, MuteReport, PowerReport, parse_report


def test_battery_request_is_20_bytes():
    assert len(protocol.BATTERY_REQUEST) == 20
    assert protocol.BATTERY_REQUEST[:3] == bytes([0x21, 0xFF, 0x05])
    assert not any(protocol.BATTERY_REQUEST[3:])


def test_battery_reply_from_the_real_headset():
    # 10 02 -> 4098 mV, as seen on the real device
    raw = bytes([0x21, 0xFF, 0x05, 0x10, 0x02]) + bytes(15)
    assert parse_report(raw) == BatteryReport(4098)


def test_battery_reply_as_a_list_of_ints():
    assert parse_report([0x21, 0xFF, 0x05, 0x0F, 0xCF] + [0] * 15) == BatteryReport(4047)


def test_absurd_voltage_is_ignored():
    assert parse_report([0x21, 0xFF, 0x05, 0x00, 0x00] + [0] * 15) is None
    assert parse_report([0x21, 0xFF, 0x05, 0xFF, 0xFF] + [0] * 15) is None


def test_mute_events():
    assert parse_report([0x65, 0x04]) == MuteReport(True)
    assert parse_report([0x65, 0x00]) == MuteReport(False)
    assert parse_report([0x65, 0x02, 0, 0]) == MuteReport(False)


def test_power_events():
    assert parse_report([0x64, 0x01]) == PowerReport(True)
    assert parse_report([0x64, 0x03]) == PowerReport(False)
    assert parse_report([0x64, 0x07]) is None


def test_unknown_and_short_reports():
    assert parse_report([]) is None
    assert parse_report([0x21]) is None
    assert parse_report([0x21, 0xFF, 0x09, 1, 2, 3]) is None
    assert parse_report([0x99, 0x99, 0x99]) is None


def test_hex_dump():
    assert protocol.hex_dump(bytes([1, 0xAB])) == "01 ab"
    assert protocol.hex_dump(bytes(100), limit=4).endswith("...")
