import json

from hyperx_battery.config import Config, config_path, data_dir


def test_defaults_match_the_plan():
    c = Config()
    assert c.poll_interval_s == 5 and c.reply_timeout_s == 2 and c.misses_to_off == 2
    assert c.tone_hz == 10 and c.tone_amp == 0.05 and c.idle_pause_min == 15
    assert c.low_battery_thresholds == [20, 10]
    assert c.keepalive_enabled is True
    assert "HyperX" in c.audio_device_match


def test_missing_file_is_created_with_defaults(tmp_path):
    path = tmp_path / "sub" / "config.json"
    c = Config.load(path)
    assert c == Config()
    assert json.loads(path.read_text(encoding="utf-8"))["tone_hz"] == 10


def test_partial_file_keeps_defaults_for_the_rest(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"idle_pause_min": 30, "tone_amp": 0.02}), encoding="utf-8")
    c = Config.load(path)
    assert c.idle_pause_min == 30 and c.tone_amp == 0.02 and c.tone_hz == 10


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"bogus": 1, "tone_hz": 12}), encoding="utf-8")
    c = Config.load(path)
    assert c.tone_hz == 12 and not hasattr(c, "bogus")


def test_bad_types_fall_back_to_defaults():
    c = Config.from_dict({
        "tone_hz": "ten", "keepalive_enabled": "yes", "audio_device_match": "",
        "low_battery_thresholds": "20,10", "misses_to_off": True, "poll_interval_s": None,
    })
    assert c == Config()


def test_out_of_range_values_are_clamped():
    c = Config.from_dict({"tone_amp": 5, "poll_interval_s": 0, "tone_hz": 1000, "misses_to_off": 0})
    assert c.tone_amp == 0.5 and c.poll_interval_s == 1.0 and c.tone_hz == 40.0 and c.misses_to_off == 1


def test_idle_pause_zero_disables_it():
    assert Config.from_dict({"idle_pause_min": 0}).idle_pause_min == 0


def test_int_for_float_option_is_accepted():
    c = Config.from_dict({"idle_pause_min": 20, "poll_interval_s": 3})
    assert c.idle_pause_min == 20.0 and isinstance(c.idle_pause_min, float)


def test_thresholds_are_validated_and_sorted():
    assert Config.from_dict({"low_battery_thresholds": [5, 30, 15]}).low_battery_thresholds == [30, 15, 5]
    assert Config.from_dict({"low_battery_thresholds": [150]}).low_battery_thresholds == [20, 10]
    assert Config.from_dict({"low_battery_thresholds": []}).low_battery_thresholds == []


def test_corrupt_file_is_backed_up_and_defaults_used(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    c = Config.load(path)
    assert c == Config()
    assert (tmp_path / "config.json.bad").read_text(encoding="utf-8") == "{ not json"
    assert json.loads(path.read_text(encoding="utf-8"))          # rewritten with valid JSON


def test_non_object_json_is_treated_as_corrupt(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert Config.load(path) == Config()


def test_save_round_trip(tmp_path):
    path = tmp_path / "config.json"
    c = Config(keepalive_enabled=False, idle_pause_min=5.0)
    c.save(path)
    assert Config.load(path) == c
    assert not list(tmp_path.glob("*.tmp"))


def test_data_dir_uses_appdata_and_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.delenv("HYPERX_BATTERY_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "HyperXBattery"
    monkeypatch.setenv("HYPERX_BATTERY_DATA_DIR", str(tmp_path / "x"))
    assert data_dir() == tmp_path / "x"
    assert config_path() == tmp_path / "x" / "config.json"


def test_audio_switch_defaults_and_validation():
    c = Config()
    assert c.auto_switch_audio is True
    assert c.auto_switch_device_match == "HyperX Cloud Flight"
    assert c.auto_switch_off_confirm_s == 20
    c = Config.from_dict({"auto_switch_audio": False, "auto_switch_off_confirm_s": -5,
                          "auto_switch_device_match": ""})
    assert c.auto_switch_audio is False and c.auto_switch_off_confirm_s == 0
    assert c.auto_switch_device_match == "HyperX Cloud Flight"
    assert Config.from_dict({"auto_switch_off_confirm_s": 99999}).auto_switch_off_confirm_s == 600
