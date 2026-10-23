# Development

See [Contributing](contributing.md) for how to contribute; this page
describes the development setup, the checks, the documentation and the
versions.

To set up a development environment (e.g., in a virtual environment) and run
the checks that CI runs:

```bash
pip install -e ".[dev]"  # the app, with the dev dependencies
pre-commit install  # run the checks on every commit
pre-commit run --all-files  # ruff, mypy, semgrep, ast-grep, actionlint
pre-commit run --all-files --hook-stage manual gitleaks-all  # secrets
coverage run -m pytest  # the tests, with coverage
coverage report  # fails below 100%
```

Coverage of `pro_tes` and `scripts` is kept at 100%, which is enforced:
`coverage report` (and CI) fails below 100% (`fail_under` in
`pyproject.toml`), and so do Codecov's checks of the whole code and of the
changes of a pull request (`codecov.yml`). Add tests that fail without your
change.

The tests in `tests/` mirror the package (`pro_tes/`). They need neither a
database nor TES backends: they use an in-memory database
([mongomock](https://github.com/mongomock/mongomock)) and fake TES backends,
and have no network access (fixture `no_network` in `tests/conftest.py`
blocks all connections except to the test database, and fails tests that try
with `Network access blocked in tests`). CI also runs them against MongoDB
7.0; to do so locally, set `MONGODB_TEST_URI` to a standalone MongoDB (not a
replica set) without other data, one per test run (the tests drop its
databases `taskStore` and `protes` before each test, and refuse to start if
one of them exists), e.g.:

```bash
docker run -d --rm --name mongodb-test -p 127.0.0.1:27017:27017 mongo:7.0
until docker exec mongodb-test mongosh --quiet --eval 'db.adminCommand(ping).ok'; do sleep 1; done
MONGODB_TEST_URI=mongodb://localhost:27017 pytest
docker stop mongodb-test
```

CI also runs the app with Docker Compose, with a fake TES backend instead of
the TES instances of the config (`tests/smoke/`), which is also the identity
provider (it issues access tokens at
`http://fake-tes:8080/oidc/token?sub=<user>`, inside the Compose network),
and checks that tasks are relayed to it and tracked until they are complete,
and access control. To run it locally (with curl and jq):

```bash
export COMPOSE_FILE=docker-compose.yaml:tests/smoke/compose.yaml
docker compose up -d --build --wait
tests/smoke/check.sh
docker compose down
```

To try changes in the running app, rebuild it with
`docker compose up --build -d`. `python -m pro_tes.app` runs the app with
Flask's development server instead of Gunicorn (see
[Server](configuration.md#server)); it needs a MongoDB at `db.host` and a
RabbitMQ at `jobs.host` of the config, and a config overlay with the
security settings (e.g., `FOCA_CONFIG_OVERLAY=config.dev.yaml`; see [Access
control](access-control.md#deployment-settings)), as do the Celery workers.

Run `pre-commit` in the environment with the dev dependencies: the mypy and
ast-grep hooks use the installed packages. The first run sets up the other
hooks, which takes a few minutes.

The checks are configured in
[`.pre-commit-config.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/.pre-commit-config.yaml)
and
[`pyproject.toml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/pyproject.toml).
Project-specific rules are in `.semgrep/` (security) and `.ast-grep/`
(anti-patterns such as HTTP status codes as strings).
[actionlint](https://github.com/rhysd/actionlint) checks the workflows in
`.github/workflows/`, and their shell scripts with
[ShellCheck](https://www.shellcheck.net/) if it is installed (as on
GitHub-hosted runners, so CI may find more than a local run without it). To
suppress a false positive, add a comment with the reason to the line:
`# noqa: <code>` (ruff), `# type: ignore[<code>]` (mypy),
`# nosemgrep: <rule-id>`, `# ast-grep-ignore: <rule-id>` or
`# gitleaks:allow`.

## Documentation

This documentation is built with [MkDocs](https://www.mkdocs.org/) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/), from
`docs/` and
[`mkdocs.yml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/mkdocs.yml),
by Read the Docs (configured in
[`.readthedocs.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/.readthedocs.yaml))
and in CI; warnings, e.g., of broken links, fail the build. The [API
reference](reference.md) is generated from the docstrings with
[mkdocstrings](https://mkdocstrings.github.io/). To build it locally, or serve
it with live reloading:

```bash
pip install -e ".[docs]"
mkdocs build --strict  # into `site/`, ignored by Git
mkdocs serve
```

Parts of the docs are kept elsewhere, so that they are written only once: the
introduction, the known deployments and the quick start of the home and
getting started pages are sections of `README.md` (between
`--8<-- [start:<name>]` and `--8<-- [end:<name>]` comments), and the
[deployment page](deployment.md) is the README of the Helm chart
(`deployment/README.md`), which `docs/hooks.py` includes, with its links to
pages of the docs and to files of the repository adapted. Links in the
chart's README must be absolute (to `https://protes.readthedocs.io/en/latest/`
and to files on branch `dev` on GitHub), as they must also work in the
packaged chart.

## Versioning and releases

The project adopts the [semantic versioning](https://semver.org/) scheme for
versioning. Currently the service is in beta stage, so the API may change
without further notice.

The version is only stored in Git tags (`vX.Y.Z`): the package version is
derived from them (via
[setuptools-scm](https://setuptools-scm.readthedocs.io/)), and the service
info reports it. Builds without Git metadata, such as local Docker builds
(e.g., with Docker Compose), report `0.0.0.dev0`, unless the version is
passed as build argument `SETUPTOOLS_SCM_PRETEND_VERSION`, as the CI
workflow does for the images it publishes.

To test unreleased changes of FOCA, `pyproject.toml` may pin a FOCA commit
(`foca @ git+https://github.com/elixir-cloud-aai/foca@<sha>`), with FOCA's
development image of that commit as the base image (tagged by date, e.g.,
`elixircloud/foca:20261023-py3.13`, pinned by digest, as the tag moves with
later commits of that day). The `Dockerfile` records the commit as
`## FOCA commit: <sha>`, which the tests check (`tests/test_build.py`); the
image build doesn't reinstall FOCA, but fails unless the base image has FOCA
of that commit (`scripts/image_requirements.py`). Dependabot ignores the base
image meanwhile. Such pins are temporary: `scripts/check_release_pins.py`
fails while a dependency (also an optional one) is a direct reference or
names a pre-release, the base image has no release tag, or a chart
dependency has another version than a release. It runs for each pushed
release tag (`.github/workflows/release-pins.yaml`; check its result before
publishing the GitHub release), and in the tests of checkouts of release tags
(`tests/test_check_release_pins.py`), so replace the pins by the released
versions before tagging. This applies to pre-releases of the app, too: they
are built on FOCA releases.

Zenodo archives each GitHub release with the citation metadata
(`CITATION.cff`) of the tagged commit, so they must have the release version.
Shortly before the release, set its version and date and merge the change
into `dev`; each version, also each pre-release (e.g., `X.Y.Zrc1`), needs its
own such change. Before tagging the release commit, check it:

```bash
python scripts/citation.py set X.Y.Z  # date: today (UTC), or --date YYYY-MM-DD
python scripts/citation.py check X.Y.Z  # on the release commit
```
