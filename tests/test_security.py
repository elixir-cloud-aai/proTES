"""Tests of the security configuration and its checks on startup."""

from pathlib import Path
from typing import Any

from foca import get_config
import pydantic
import pytest
import yaml

from pro_tes.app import init_app
from tests.conftest import ADMIN_GROUP, USER_GROUP, set_overlays

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "pro_tes" / "config.yaml"
DEV_CONFIG = ROOT / "config.dev.yaml"


def write_overlay(path: Path, config: dict[str, Any]) -> Path:
    """Write a config overlay.

    Args:
        path: Path of the overlay.
        config: Config of the overlay.

    Returns:
        Path of the overlay.
    """
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def mapping(**roles: str) -> dict[str, Any]:
    """Get a config overlay mapping the groups of the test overlay to roles.

    Args:
        **roles: Roles of the groups `user` (`USER_GROUP`) and `admin`
            (`ADMIN_GROUP`).

    Returns:
        Config overlay.
    """
    groups = {"user": USER_GROUP, "admin": ADMIN_GROUP}
    return {
        "security": {
            "access_control": {
                "group_mapping": {groups[key]: role for key, role in roles.items()}
            }
        }
    }


def test_default_config() -> None:
    """The default config trusts no issuer and maps no groups (generic)."""
    security = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["security"]
    assert security["auth"]["issuers"] == []
    assert "audience" not in security["auth"]
    assert "userinfo_claims" not in security["auth"]
    assert "claim_groups" not in security["access_control"]
    assert "group_mapping" not in security["access_control"]


def test_no_issuers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses to start without trusted issuers."""
    overlay = write_overlay(
        tmp_path / "overlay.yaml", {"security": {"auth": {"issuers": []}}}
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(pydantic.ValidationError, match="requires trusted token"):
        init_app()


def test_no_audience(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses to start without audience, unless it isn't checked."""
    overlay = write_overlay(
        tmp_path / "overlay.yaml", {"security": {"auth": {"audience": None}}}
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(pydantic.ValidationError, match="require an audience"):
        init_app()
    opt_out = write_overlay(
        tmp_path / "opt-out.yaml", {"security": {"auth": {"verify_audience": False}}}
    )
    set_overlays(monkeypatch, overlay, opt_out)
    init_app()


def test_no_admin_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses to start without a group of the role `admin`."""
    overlay = write_overlay(tmp_path / "overlay.yaml", mapping(admin="user"))
    set_overlays(monkeypatch, overlay)
    with pytest.raises(RuntimeError, match="No group is mapped to the role 'admin'"):
        init_app()


def test_admin_optional(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without groups of the roles, the app warns, if admins are optional."""
    overlay = write_overlay(
        tmp_path / "overlay.yaml",
        {
            **mapping(user="guest", admin="guest"),
            "custom": {"access_control": {"require_admin": False}},
        },
    )
    set_overlays(monkeypatch, overlay)
    init_app()
    # FOCA configures logging to stderr
    err = capsys.readouterr().err
    assert "No group is mapped to the role 'user'" in err
    assert "No group is mapped to the role 'admin'" in err


def test_groups_logged(app_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """The groups of the roles are logged."""
    init_app()
    err = capsys.readouterr().err
    assert f"Groups mapped to the role 'user': {USER_GROUP}" in err
    assert f"Groups mapped to the role 'admin': {ADMIN_GROUP}" in err


@pytest.mark.parametrize(
    "access_control",
    [
        {"enabled": False, "enforce": False, "policies": None},
        {"enforce": False},
    ],
)
def test_access_control_not_enforced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    access_control: dict[str, object],
) -> None:
    """The app refuses to start without enforced access control.

    Otherwise, users could access the tasks of others.
    """
    overlay = write_overlay(
        tmp_path / "overlay.yaml", {"security": {"access_control": access_control}}
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(RuntimeError, match="must be enabled and enforced"):
        init_app()


@pytest.mark.parametrize(
    "security",
    [
        {"auth": {"service_clients": [{"client_id": "portal"}]}},
        {
            "auth": {"service_clients": [{"client_id": "portal"}]},
            "access_control": {"client_mapping": {"portal": "user"}},
        },
        {
            "auth": {
                "service_clients": [{"client_id": "portal", "on_behalf_of": True}],
                "on_behalf_of_header": "X-On-Behalf-Of",
            }
        },
    ],
    ids=["clients", "mapping", "on behalf of"],
)
def test_service_clients(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    security: dict[str, object],
) -> None:
    """The app refuses to start with service clients (not supported yet).

    Tasks are owned by users, but service clients are enforced as clients.
    """
    overlay = write_overlay(tmp_path / "overlay.yaml", {"security": security})
    set_overlays(monkeypatch, overlay)
    with pytest.raises(RuntimeError, match="Service clients"):
        init_app()


def test_dev_config(monkeypatch: pytest.MonkeyPatch, app_env: None) -> None:
    """The config overlay for Docker Compose is valid, without admin groups."""
    monkeypatch.setenv("FOCA_CONFIG_OVERLAY", str(DEV_CONFIG))
    app = init_app()
    with app.app.app_context():
        conf = get_config().security
    assert conf.auth.verify_audience is False
    assert conf.access_control.group_mapping == {}


def test_no_claim_groups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Group mappings need the claim of the groups."""
    overlay = write_overlay(
        tmp_path / "overlay.yaml",
        {"security": {"access_control": {"claim_groups": None}}},
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(RuntimeError, match="No group is mapped to the role 'admin'"):
        init_app()
    assert "but not `claim_groups`" in capsys.readouterr().err
