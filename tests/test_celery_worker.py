"""Tests of the Celery worker entry point."""

from foca.models.config import Config
import pytest

from pro_tes import celery_worker
from pro_tes.config_models import ClientCredentials, CustomConfig, Tes, TesBackend
from pro_tes.ga4gh.tes.backends import CLIENT_ID_ENV, CLIENT_SECRET_ENV


@pytest.fixture
def custom(monkeypatch: pytest.MonkeyPatch) -> CustomConfig:
    """Custom configuration of the worker, restored after the test."""
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
        service_list=[TesBackend(url="https://tes.example.org")],
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
