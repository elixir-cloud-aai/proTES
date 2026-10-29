"""Tests of the Celery worker entry point."""

from foca.models.config import Config
import pytest

from pro_tes import celery_worker
from pro_tes.config_models import ClientCredentials, CustomConfig, Tes
from pro_tes.ga4gh.tes.backends import CLIENT_ID_ENV, CLIENT_SECRET_ENV


@pytest.fixture
def custom(monkeypatch: pytest.MonkeyPatch) -> CustomConfig:
    """Custom configuration of the worker, restored after the test.

    Returns:
        A copy of the custom configuration, used by the worker.
    """
    conf = celery_worker.celery.conf.foca
    original = conf.model_extra["custom"]
    copy = original.model_copy(deep=True)
    monkeypatch.setitem(conf.model_extra, "custom", copy)
    return copy


def test_worker_backends_checked(
    custom: CustomConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker doesn't start without the credentials of its backends."""
    custom.tes = Tes(
        backends=[{"type": "ga4gh-tes", "url": "https://tes.example.org"}],  # type: ignore[arg-type]
        client_credentials=ClientCredentials(issuer="https://idp.example.org/"),
    )
    monkeypatch.delenv(CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(CLIENT_SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match=CLIENT_ID_ENV):
        celery_worker.check_worker_backends()
    monkeypatch.setenv(CLIENT_ID_ENV, "protes")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "secret")
    celery_worker.check_worker_backends()


def test_worker_without_custom_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker doesn't start without the custom configuration."""
    monkeypatch.setitem(celery_worker.celery.conf, "foca", Config())
    with pytest.raises(SystemExit, match="custom configuration"):
        celery_worker.check_worker_backends()


def test_worker_registry_needs_public_url(
    custom: CustomConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker doesn't start with registries, but without a public URL."""
    custom.tes = Tes(
        backends=[  # type: ignore[arg-type]
            {
                "type": "ga4gh-service-registry",
                "url": "https://registry.example.org",
                "auth": "none",
            }
        ]
    )
    server = celery_worker.celery.conf.foca.server
    monkeypatch.setattr(server, "public_url", None)
    with pytest.raises(SystemExit, match=r"server\.public_url"):
        celery_worker.check_worker_backends()
    monkeypatch.setattr(server, "public_url", "https://protes.example.org")
    celery_worker.check_worker_backends()
