import json

import pytest

from camoufox_reverse_mcp.camou_config import (
    OTHER_CHUNK_SIZE,
    WINDOWS_CHUNK_SIZE,
    _default_chunk_size,
    merge_camou_config_env,
)


def _pack(config, chunk_size):
    payload = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    return {
        f"CAMOU_CONFIG_{offset // chunk_size + 1}": payload[
            offset : offset + chunk_size
        ]
        for offset in range(0, len(payload), chunk_size)
    }


def _decode(env):
    chunks = sorted(
        (
            (int(key.removeprefix("CAMOU_CONFIG_")), value)
            for key, value in env.items()
            if key.startswith("CAMOU_CONFIG_")
            and key.removeprefix("CAMOU_CONFIG_").isdigit()
        ),
        key=lambda item: item[0],
    )
    return json.loads("".join(value for _, value in chunks))


def test_merge_rebuilds_windows_chunks_and_preserves_fingerprint_config():
    original = {
        "navigator.userAgent": "Mozilla/5.0 " + "x" * 5000,
        "locale:language": "zh-CN",
        "screen.width": 1920,
    }
    env = {**_pack(original, 2047), "UNCHANGED": "yes"}
    trace = {"enabled": True, "logDir": "C:/trace", "objects": ["Window"]}

    result = merge_camou_config_env(
        env, {"propertyTrace": trace}, chunk_size=2047
    )

    decoded = _decode(result)
    assert decoded == {**original, "propertyTrace": trace}
    assert "CAMOU_CONFIG" not in result
    assert result["UNCHANGED"] == "yes"
    assert all(
        len(value) <= 2047
        for key, value in result.items()
        if key.startswith("CAMOU_CONFIG_")
    )


def test_merge_orders_more_than_nine_chunks_numerically():
    original = {"payload": "0123456789" * 20}
    env = _pack(original, 11)

    result = merge_camou_config_env(env, {"propertyTrace": {}}, chunk_size=11)

    assert _decode(result) == {**original, "propertyTrace": {}}


def test_merge_escapes_unicode_without_splitting_utf8():
    original = {"label": "中文环境" * 20}
    env = _pack(original, 17)

    result = merge_camou_config_env(
        env, {"propertyTrace": {"enabled": True}}, chunk_size=17
    )

    assert _decode(result) == {
        **original,
        "propertyTrace": {"enabled": True},
    }
    assert all(value.isascii() for value in result.values())


def test_merge_removes_plain_config_and_stale_chunks():
    original = {"payload": "x" * 100}
    env = {**_pack(original, 10), "CAMOU_CONFIG": json.dumps({"wrong": True})}

    result = merge_camou_config_env(env, {"propertyTrace": {}}, chunk_size=1000)

    assert set(result) == {"CAMOU_CONFIG_1"}
    assert _decode(result) == {**original, "propertyTrace": {}}


def test_merge_does_not_fall_back_when_visible_chunks_are_invalid():
    env = {
        "CAMOU_CONFIG_1": "not-json",
        "CAMOU_CONFIG": json.dumps({"navigator.platform": "Win32"}),
    }
    original = dict(env)

    with pytest.raises(ValueError, match="CAMOU_CONFIG chunks"):
        merge_camou_config_env(
            env, {"propertyTrace": {"enabled": True}}, chunk_size=2047
        )

    assert env == original


def test_merge_ignores_and_removes_stale_chunks_after_first_gap():
    env = {
        "CAMOU_CONFIG_1": json.dumps({"navigator.platform": "Win32"}),
        "CAMOU_CONFIG_3": "stale",
    }

    result = merge_camou_config_env(
        env, {"propertyTrace": {"enabled": True}}, chunk_size=2047
    )

    assert set(result) == {"CAMOU_CONFIG_1"}
    assert _decode(result) == {
        "navigator.platform": "Win32",
        "propertyTrace": {"enabled": True},
    }


def test_merge_ignores_noncanonical_chunk_suffix_and_uses_plain_config():
    env = {
        "CAMOU_CONFIG_01": "stale",
        "CAMOU_CONFIG": json.dumps({"navigator.platform": "Win32"}),
    }

    result = merge_camou_config_env(env, {"propertyTrace": {}}, chunk_size=2047)

    assert "CAMOU_CONFIG_01" not in result
    assert _decode(result) == {
        "navigator.platform": "Win32",
        "propertyTrace": {},
    }


def test_merge_rejects_invalid_chunks_without_mutating_input():
    env = {"CAMOU_CONFIG_1": '{"a":'}
    original = dict(env)

    with pytest.raises(ValueError, match="valid JSON"):
        merge_camou_config_env(env, {"propertyTrace": {}})

    assert env == original


def test_merge_rejects_non_string_chunk():
    with pytest.raises(ValueError, match="CAMOU_CONFIG_1 must be a string"):
        merge_camou_config_env(
            {"CAMOU_CONFIG_1": 123},
            {"propertyTrace": {}},
        )


def test_merge_rejects_invalid_plain_config():
    with pytest.raises(ValueError, match="CAMOU_CONFIG does not contain valid JSON"):
        merge_camou_config_env(
            {"CAMOU_CONFIG": "not-json"},
            {"propertyTrace": {}},
        )


def test_merge_creates_config_when_camoufox_did_not_supply_one():
    result = merge_camou_config_env(
        {"UNCHANGED": "yes"},
        {"propertyTrace": {"enabled": True}},
        chunk_size=2047,
    )

    assert result["UNCHANGED"] == "yes"
    assert _decode(result) == {"propertyTrace": {"enabled": True}}


def test_default_chunk_size_matches_supported_platform_limits():
    assert _default_chunk_size("nt") == WINDOWS_CHUNK_SIZE == 2047
    assert _default_chunk_size("posix") == OTHER_CHUNK_SIZE == 32767


def test_merge_accepts_real_camoufox_windows_chunks(monkeypatch):
    from camoufox import utils as camoufox_utils

    monkeypatch.setattr(camoufox_utils, "OS_NAME", "win")
    original = {
        "navigator.userAgent": "Mozilla/5.0 " + "x" * 9000,
        "locale:language": "zh-CN",
    }
    env = camoufox_utils.get_env_vars(original, "windows")

    result = merge_camou_config_env(
        env,
        {"propertyTrace": {"enabled": True}},
        chunk_size=2047,
    )

    assert len([key for key in env if key.startswith("CAMOU_CONFIG_")]) > 1
    assert _decode(result) == {
        **original,
        "propertyTrace": {"enabled": True},
    }
