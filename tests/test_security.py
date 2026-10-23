"""Tests of the security configuration and its checks on startup."""

from pathlib import Path
from typing import Any

from foca import get_config
import pydantic
import pytest
import yaml

from pro_tes.app import init_app
from pro_tes.config_models import Tes, TesBackend
from pro_tes.ga4gh.tes.backends import CLIENT_ID_ENV, CLIENT_SECRET_ENV
from pro_tes.security import check_backends
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


def test_default_backends() -> None:
    """The default config has no TES backends, and allows `https` only.

    Tasks with credentials in URLs are rejected.
    """
    custom = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["custom"]
    assert custom["tes"]["service_list"] == []
    assert custom["tes"]["url_schemes"] == ["https"]
    assert custom["tes"]["client_credentials"]["issuer"] is None
    # no registry by default (generic); discovered hosts must be public
    assert custom["tes"]["discovery"]["registry_url"] is None
    assert custom["tes"]["discovery"]["allow_private_addresses"] is False
    assert custom["controllers"]["post_task"]["url_credentials"] == "reject"


def backends_overlay(path: Path, *backends: dict[str, Any]) -> Path:
    """Write a config overlay with TES backends.

    Args:
        path: Path of the overlay.
        *backends: TES backends (`custom.tes.service_list`).

    Returns:
        Path of the overlay.
    """
    return write_overlay(
        path,
        {
            "custom": {
                "tes": {
                    "service_list": list(backends),
                    "client_credentials": {"issuer": "https://idp.example.org/"},
                }
            }
        },
    )


TOKEN_BACKEND = {"url": "https://tes1.example.org"}
BASIC_BACKEND = {
    "url": "https://tes2.example.org",
    "auth": "basic",
    "basic": {"username_env": "TES2_USERNAME", "password_env": "TES2_PASSWORD"},
}


def test_backends_credentials_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses to start without the credentials of its backends.

    That is, the client of proTES for backends with tokens, and the Basic
    credentials of backends with `auth: basic`.
    """
    for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV, "TES2_USERNAME", "TES2_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    overlay = backends_overlay(tmp_path / "overlay.yaml", TOKEN_BACKEND, BASIC_BACKEND)
    set_overlays(monkeypatch, overlay)
    with pytest.raises(RuntimeError) as exc_info:
        init_app()
    assert str(exc_info.value).endswith(
        f"{CLIENT_ID_ENV}, {CLIENT_SECRET_ENV}, TES2_PASSWORD, TES2_USERNAME"
    )


def test_backends_logged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """How the app authenticates at its backends is logged.

    With warnings for backends without tokens; the credentials are not.
    """
    monkeypatch.setenv(CLIENT_ID_ENV, "protes")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "client-secret")
    monkeypatch.setenv("TES2_USERNAME", "protes")
    monkeypatch.setenv("TES2_PASSWORD", "basic-secret")
    overlay = backends_overlay(
        tmp_path / "overlay.yaml",
        TOKEN_BACKEND,
        BASIC_BACKEND,
        {"url": "https://tes3.example.org", "auth": "none"},
    )
    set_overlays(monkeypatch, overlay)
    init_app()
    err = capsys.readouterr().err
    assert "access tokens of proTES: https://tes1.example.org" in err
    assert "TES backend https://tes2.example.org: HTTP Basic" in err
    assert "TES backend https://tes3.example.org: no authentication" in err
    assert "client-secret" not in err
    assert "basic-secret" not in err
    assert "No TES backends are configured" not in err
    assert "scheme 'http' are allowed" not in err


def test_no_backends(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without backends, and with `http` allowed, the app warns."""
    overlay = write_overlay(
        tmp_path / "overlay.yaml",
        {"custom": {"tes": {"service_list": [], "url_schemes": ["https", "http"]}}},
    )
    set_overlays(monkeypatch, overlay)
    init_app()
    err = capsys.readouterr().err
    assert "No TES backends are configured" in err
    assert "scheme 'http' are allowed" in err


def test_backends_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses to start with backends with tokens, but no issuer."""
    overlay = write_overlay(
        tmp_path / "overlay.yaml",
        {"custom": {"tes": {"service_list": [TOKEN_BACKEND]}}},
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(ValueError, match="custom configuration") as exc_info:
        init_app()
    assert "need `client_credentials" in str(exc_info.value.__cause__)


def test_basic_backend_unvalidated() -> None:
    """Backends with `auth: basic` but no `basic` are rejected.

    Validation prevents them, unless the config is changed afterwards.
    """
    backend = TesBackend.model_construct(
        url="https://tes.example.org", auth="basic", basic=None
    )
    conf = Tes.model_construct(service_list=[backend], url_schemes=["https"])
    with pytest.raises(RuntimeError, match="without `basic`"):
        check_backends(conf)


def discovery_overlay(path: Path, **discovery: Any) -> Path:
    """Write a config overlay with discovery of backends, and no static ones.

    Args:
        path: Path of the overlay.
        **discovery: Discovery settings, besides the registry URL.

    Returns:
        Path of the overlay.
    """
    return write_overlay(
        path,
        {
            "custom": {
                "tes": {
                    "service_list": [],
                    "discovery": {
                        "registry_url": "https://registry.example.org/api",
                        **discovery,
                    },
                    "client_credentials": {"issuer": "https://idp.example.org/"},
                }
            }
        },
    )


def test_discovery_credentials_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """With discovery, the app refuses to start without proTES' client.

    Discovered backends get proTES' token by default.
    """
    for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    set_overlays(monkeypatch, discovery_overlay(tmp_path / "overlay.yaml"))
    with pytest.raises(RuntimeError, match=CLIENT_SECRET_ENV):
        init_app()


def test_discovery_logged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Discovery is logged, with warnings for its less safe settings.

    The registry is unreachable in the tests, so no backends are discovered.
    """
    overlay = discovery_overlay(
        tmp_path / "overlay.yaml", auth="none", allow_private_addresses=True
    )
    set_overlays(monkeypatch, overlay)
    init_app()
    err = capsys.readouterr().err
    assert "Discovering TES backends at registry https://registry.example.org/api" in (
        err
    )
    assert "Discovered TES backends get no credentials" in err
    assert "may have private addresses" in err
    assert "keeping 0 backends discovered earlier" in err
    assert "No TES backends are configured" not in err
