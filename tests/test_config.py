"""Minimal sanity tests for configuration loading."""
import pytest

from forwarder.config import ConfigError, load_config

REQUIRED_ENV = {
    "API_ID": "123456",
    "API_HASH": "abcdef0123456789abcdef0123456789",
    "SESSION_STRING": "dummy-session-string",
    "SOURCE_CHANNEL_ID": "-1001111111111",
    "DEST_CHANNEL_ID": "-1002222222222",
}


def _set_env(monkeypatch, overrides=None):
    for key, value in {**REQUIRED_ENV, **(overrides or {})}.items():
        monkeypatch.setenv(key, value)


def _missing_env_path(tmp_path):
    # Point load_config at a .env file that doesn't exist, so it relies
    # purely on the process environment set via monkeypatch above.
    return str(tmp_path / "does-not-exist.env")


def test_load_config_success(monkeypatch, tmp_path):
    _set_env(monkeypatch)
    config = load_config(env_file=_missing_env_path(tmp_path))

    assert config.api_id == 123456
    assert config.source_channel == -1001111111111
    assert config.dest_channel == -1002222222222
    assert config.drop_author is False
    assert config.max_retries == 5


def test_missing_required_var_raises(monkeypatch, tmp_path):
    _set_env(monkeypatch)
    monkeypatch.delenv("API_HASH")

    with pytest.raises(ConfigError):
        load_config(env_file=_missing_env_path(tmp_path))


def test_non_integer_api_id_raises(monkeypatch, tmp_path):
    _set_env(monkeypatch, {"API_ID": "not-a-number"})

    with pytest.raises(ConfigError):
        load_config(env_file=_missing_env_path(tmp_path))


def test_username_channel_gets_at_prefix(monkeypatch, tmp_path):
    _set_env(monkeypatch, {"SOURCE_CHANNEL_ID": "somepublicchannel"})
    config = load_config(env_file=_missing_env_path(tmp_path))

    assert config.source_channel == "@somepublicchannel"


def test_boolean_env_parsing(monkeypatch, tmp_path):
    _set_env(monkeypatch, {"DROP_AUTHOR": "true", "FORWARD_EXISTING_ON_FIRST_RUN": "yes"})
    config = load_config(env_file=_missing_env_path(tmp_path))

    assert config.drop_author is True
    assert config.forward_existing_on_first_run is True
