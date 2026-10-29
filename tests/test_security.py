"""Tests of the security configuration and its checks on startup."""

from pathlib import Path
from typing import Any

from foca import get_config
from foca.plugins.backends import BackendListConfig, ServiceEntry
import pydantic
import pytest
import yaml

from pro_tes.app import init_app
from pro_tes.config_models import Tes
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


def test_default_require_admin() -> None:
    """The base config requires admins (FOCA's `require_admin`)."""
    security = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["security"]
    assert security["access_control"]["require_admin"] is True


def test_no_admin_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses to start without a group of the role `admin`."""
    overlay = write_overlay(tmp_path / "overlay.yaml", mapping(admin="user"))
    set_overlays(monkeypatch, overlay)
    with pytest.raises(ValueError, match="No group or service client is mapped"):
        init_app()


def test_custom_require_admin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses `custom.access_control`, naming FOCA's setting.

    `custom.access_control.require_admin` moved to
    `security.access_control.require_admin`.
    """
    overlay = write_overlay(
        tmp_path / "overlay.yaml",
        {"custom": {"access_control": {"require_admin": False}}},
    )
    set_overlays(monkeypatch, overlay)
    # FOCA raises its own error, from the model's (shown in the traceback)
    with pytest.raises(ValueError, match="custom configuration") as exc:
        init_app()
    assert "security.access_control.require_admin" in str(exc.value.__cause__)


def test_admin_optional(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without groups of the roles, the app warns, if admins are optional."""
    config = mapping(user="guest", admin="guest")
    config["security"]["access_control"]["require_admin"] = False
    overlay = write_overlay(tmp_path / "overlay.yaml", config)
    set_overlays(monkeypatch, overlay)
    init_app()
    # FOCA configures logging to stderr
    err = capsys.readouterr().err
    assert "No group is mapped to the role 'user'" in err
    assert "No group or service client is mapped to the role 'admin'" in err


def test_groups_logged(app_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """The groups of the roles are logged."""
    init_app()
    err = capsys.readouterr().err
    assert f"Groups mapped to the role 'user': {USER_GROUP}" in err
    assert f"Groups mapped to the role 'admin': {ADMIN_GROUP}" in err


@pytest.mark.parametrize(
    "access_control",
    [
        # FOCA refuses `require_admin` without access control itself
        {"enabled": False, "enforce": False, "policies": None, "require_admin": False},
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
) -> None:
    """Group mappings need the claim of the groups (FOCA's startup check)."""
    overlay = write_overlay(
        tmp_path / "overlay.yaml",
        {"security": {"access_control": {"claim_groups": None}}},
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(ValueError, match="claim_groups"):
        init_app()


def test_default_backends() -> None:
    """The default config has no TES backends, and allows `https` only.

    Tasks with credentials in URLs are rejected.
    """
    custom = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["custom"]
    # no backends and no registry by default (generic)
    assert custom["tes"]["backends"] == []
    assert custom["tes"]["url_schemes"] == ["https"]
    assert custom["tes"]["client_credentials"]["issuer"] is None
    assert custom["controllers"]["post_task"]["url_credentials"] == "reject"


def backends_overlay(path: Path, *backends: dict[str, Any], **server: Any) -> Path:
    """Write a config overlay with TES backends.

    Args:
        path: Path of the overlay.
        *backends: Entries of the backend list (`custom.tes.backends`).
        **server: Server settings, if any (e.g., `public_url`).

    Returns:
        Path of the overlay.
    """
    config: dict[str, Any] = {
        "custom": {
            "tes": {
                "backends": list(backends),
                "client_credentials": {"issuer": "https://idp.example.org/"},
            }
        }
    }
    if server:
        config["server"] = server
    return write_overlay(path, config)


TOKEN_BACKEND = {"type": "ga4gh-tes", "url": "https://tes1.example.org"}
BASIC_BACKEND = {
    "type": "ga4gh-tes",
    "url": "https://tes2.example.org",
    "auth": "basic",
    "basic": {"username_env": "TES2_USERNAME", "password_env": "TES2_PASSWORD"},
}
REGISTRY = {
    "type": "ga4gh-service-registry",
    "url": "https://registry.example.org/api",
}
PUBLIC_URL = "https://protes.example.org/"


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
        {"type": "ga4gh-tes", "url": "https://tes3.example.org", "auth": "none"},
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
        {"custom": {"tes": {"backends": [], "url_schemes": ["https", "http"]}}},
    )
    set_overlays(monkeypatch, overlay)
    init_app()
    err = capsys.readouterr().err
    assert "No TES backends are configured" in err
    assert "scheme 'http' are allowed for the identity provider" in err


def test_http_backend_logged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Entries that allow `http` are named in a warning."""
    overlay = backends_overlay(
        tmp_path / "overlay.yaml",
        {
            "type": "ga4gh-tes",
            "url": "http://tes.example.org",
            "url_schemes": ["http"],
            "auth": "none",
        },
    )
    set_overlays(monkeypatch, overlay)
    init_app()
    err = capsys.readouterr().err
    assert "allowed (`url_schemes`) for http://tes.example.org" in err


@pytest.mark.parametrize(
    "tes, match",
    [
        ({"backends": [TOKEN_BACKEND]}, "need `client_credentials"),
        ({"service_list": []}, "`custom.tes.service_list` removed"),
        ({"discovery": {}}, "`custom.tes.discovery` removed"),
    ],
)
def test_backends_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    tes: dict[str, Any],
    match: str,
) -> None:
    """The app refuses to start with invalid or removed backend settings.

    E.g., backends with tokens, but no issuer; or the keys replaced by the
    backend list.
    """
    overlay = write_overlay(tmp_path / "overlay.yaml", {"custom": {"tes": tes}})
    set_overlays(monkeypatch, overlay)
    with pytest.raises(ValueError, match="custom configuration") as exc_info:
        init_app()
    assert match in str(exc_info.value.__cause__)


def test_basic_backend_unvalidated() -> None:
    """Backends with `auth: basic` but no `basic` are rejected.

    Validation prevents them, unless the config is changed afterwards.
    """
    entry = ServiceEntry.model_construct(
        type="ga4gh-tes",
        url="https://tes.example.org",
        url_schemes=["https"],
        auth="basic",
        basic=None,
    )
    conf = Tes.model_construct(
        backends=BackendListConfig.model_construct([entry]), url_schemes=["https"]
    )
    with pytest.raises(RuntimeError, match="without `basic`"):
        check_backends(conf)


def test_registry_public_url_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """With registries, the app refuses to start without its public URL.

    So that it never discovers, and forwards tasks to, itself.
    """
    monkeypatch.setenv(CLIENT_ID_ENV, "protes")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "client-secret")
    set_overlays(monkeypatch, backends_overlay(tmp_path / "overlay.yaml", REGISTRY))
    with pytest.raises(RuntimeError, match=r"`server\.public_url` is not set"):
        init_app()


def test_registry_credentials_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """With registries, the app refuses to start without proTES' client.

    Discovered backends get proTES' token by default.
    """
    for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    overlay = backends_overlay(
        tmp_path / "overlay.yaml", REGISTRY, public_url=PUBLIC_URL
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(RuntimeError, match=CLIENT_SECRET_ENV):
        init_app()


def test_registry_logged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Registries are logged, with warnings for their less safe settings."""
    overlay = backends_overlay(
        tmp_path / "overlay.yaml",
        {
            **REGISTRY,
            "auth": "none",
            "allow_private_addresses": True,
            "registry_allow_private_addresses": True,
        },
        public_url=PUBLIC_URL,
    )
    set_overlays(monkeypatch, overlay)
    init_app()
    err = capsys.readouterr().err
    assert "Discovering TES backends at registry https://registry.example.org/api" in (
        err
    )
    assert "discovered at registry https://registry.example.org/api get no" in err
    assert "may have private addresses" in err
    assert "Registry https://registry.example.org/api may have a private address" in (
        err
    )
    assert "No TES backends are configured" not in err


def test_backend_at_public_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """The app refuses to start with configured backends below its public URL.

    FOCA never uses them (so that proTES never forwards tasks to itself), so
    they would be dropped silently otherwise.
    """
    backend = {
        "type": "ga4gh-tes",
        "url": "https://protes.example.org/funnel",
        "auth": "none",
    }
    overlay = backends_overlay(
        tmp_path / "overlay.yaml", backend, public_url=PUBLIC_URL
    )
    set_overlays(monkeypatch, overlay)
    with pytest.raises(RuntimeError, match=r"protes\.example\.org/funnel"):
        init_app()
    other = backends_overlay(
        tmp_path / "other.yaml",
        backend,
        public_url="https://protes.example.org/tes/",
    )
    set_overlays(monkeypatch, other)
    init_app()


def test_backend_at_public_url_unchecked() -> None:
    """Only configured backends are checked against the public URL, if set.

    Registries at or below it are allowed (they are read, not forwarded to).
    """
    tes = {"type": "ga4gh-tes", "url": "https://protes.example.org/tes"}
    registry = {
        "type": "ga4gh-service-registry",
        "url": "https://protes.example.org/registry",
    }
    conf = Tes(backends=[{**tes, "auth": "none"}])  # type: ignore[arg-type]
    check_backends(conf, public_url=None)
    with pytest.raises(RuntimeError, match="at or below the public URL"):
        check_backends(conf, public_url="https://protes.example.org")
    conf = Tes(backends=[{**registry, "auth": "none"}])  # type: ignore[arg-type]
    check_backends(conf, public_url="https://protes.example.org")
