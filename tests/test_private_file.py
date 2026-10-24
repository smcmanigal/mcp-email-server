"""Tests for owner-only atomic file writes (config and OAuth2 token caches)."""

import os
from unittest.mock import patch

import pytest

from mcp_email_server.config import Settings
from mcp_email_server.utils.private_file import write_private_text


def _mode(path):
    return path.stat().st_mode & 0o777


def test_new_file_is_owner_only(tmp_path):
    target = tmp_path / "sub" / "secret.json"
    old_umask = os.umask(0o002)
    try:
        write_private_text(target, "data")
    finally:
        os.umask(old_umask)

    assert target.read_text() == "data"
    assert _mode(target) == 0o600


def test_existing_world_readable_file_is_tightened(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("old")
    target.chmod(0o664)

    write_private_text(target, "new")

    assert target.read_text() == "new"
    assert _mode(target) == 0o600


def test_symlinked_file_is_written_through_the_link(tmp_path):
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    real = shared_dir / "google_token_cache.json"
    real.write_text("old")
    link = tmp_path / "google_token_cache.json"
    link.symlink_to(real)

    write_private_text(link, "new")

    assert link.is_symlink()
    assert real.read_text() == "new"
    assert _mode(real) == 0o600
    assert sorted(p.name for p in shared_dir.iterdir()) == ["google_token_cache.json"]


def test_failed_write_keeps_old_content_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("old")

    with patch("mcp_email_server.utils.private_file.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            write_private_text(target, "new")

    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]


def test_settings_store_writes_owner_only_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setitem(Settings.model_config, "toml_file", config_path)

    Settings().store()

    assert config_path.exists()
    assert _mode(config_path) == 0o600
