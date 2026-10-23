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
(e.g., with Docker Compose), report `0.0.0.dev0`; the workflows pass the
version to the image build (build argument `SETUPTOOLS_SCM_PRETEND_VERSION`).
Images on Docker Hub (`elixircloud/protes`) are tagged:

- `X.Y.Z`: release `X.Y.Z`; `latest`: the highest stable release (not an
  alpha, beta or release candidate) on `main`;
- `dev`: the latest commit on `dev`; `sha-<commit>`: commit `<commit>` on
  `dev` (full SHA), to deploy a fixed commit.

Before, the CI workflow tagged the image of each commit on `dev` `latest` and
with the date (e.g., `YYYYMMDD`); such date tags are no longer pushed.

Features and fixes are merged into `dev`. Branch `main` only ever moves
forward to commits on `dev` that are released. First, shortly before the
release, set its version and date in the citation metadata, which Zenodo
archives with each release, and merge the change into `dev`; each version,
also each pre-release (e.g., `X.Y.Zrc1`), needs its own such change:

```bash
python scripts/citation.py set X.Y.Z  # date: today (UTC), or --date YYYY-MM-DD
```

Then, to release version `X.Y.Z` from commit `<sha>` on `dev` (with that
change), after CI passed for it:

```bash
git switch --detach <sha>
python scripts/citation.py check X.Y.Z  # as the release workflow does
git push origin <sha>:main  # fails unless it is a fast-forward
git tag -s vX.Y.Z <sha> -m "vX.Y.Z"
git push origin vX.Y.Z
```

The tag must be annotated and signed (`git tag -s`), with a key that GitHub can
verify (added to the tagger's GitHub account). The [release
workflow](https://github.com/elixir-cloud-aai/proTES/blob/dev/.github/workflows/release.yaml),
triggered by the tag, checks that GitHub verified the signature, that the
tag's commit is on `main` and its `CITATION.cff` has the release version and
a date that is not in the future (UTC), that the dependencies are releases
(see below), and that the [CI
workflow](https://github.com/elixir-cloud-aai/proTES/blob/dev/.github/workflows/checks.yaml)
passed for it (a run for the push of the commit to `dev`; if CI hasn't
finished or failed, re-run the release workflow once it passed). It packages
the Helm chart with the release version as `version` and `appVersion` (the
default image tag). In the packaged README, links to the latest docs and to
files on branch `dev` point to the release (`/en/vX.Y.Z/`, `blob/vX.Y.Z/`);
the release fails if any link to a version that moves is left
(`scripts/chart_readme.py`). The README in the repository is unchanged.
After approval in the `release` environment, it builds the image, pushes it
by digest (untagged) and checks that the app reports the release version.
After a second approval in the `release` environment, it tags exactly that
image `X.Y.Z` (and `latest`, if it is the highest stable release on `main`)
and creates a GitHub release with the chart (`.tgz`) and the notes in
`docs/releases/X.Y.Z.md` (for pre-releases, e.g., `X.Y.Zrc1.md`), or, if
there are none, notes generated from the merged pull requests. Curated notes
are published as written, so the release workflow fails if they contain
`TODO`; on re-runs, it replaces the notes of an existing release with them
(also overwriting manual edits). Add curated notes to the
[changelog](changelog.md) in `mkdocs.yml` (`nav`; the docs build fails
otherwise), and refer to pages of the docs by name rather than by relative
links, which don't work in GitHub releases. No packages are published to
PyPI. Tags of alpha, beta and release candidate versions (e.g., `vX.Y.Zrc1`,
also `aN` and `bN`) get chart version `X.Y.Z-rc1` (or `-aN`, `-bN`) and never
update the `latest` image. These and, until 1.0.0, all other releases are
GitHub pre-releases (never marked as the latest release). Re-running all
jobs of the release workflow builds and tags a new image digest (the image
build is not reproducible); re-running only the failed jobs reuses the digest
already pushed. Digests of failed runs stay on Docker Hub, untagged. Images
of commits on `dev` are published by the CI workflow (environment
`dev-images`).

The release workflow derives the version, the chart version, the kind of
release (stable, GitHub pre-release) and whether the image is tagged `latest`
from the tag with `scripts/release_version.py`, which is tested over a table
of tags (`tests/test_release_version.py`) and checked like the app (ruff,
mypy, coverage); change these rules there, not in the workflow.

To test unreleased changes of FOCA, `pyproject.toml` may pin a FOCA commit
(`foca @ git+https://github.com/elixir-cloud-aai/foca@<sha>`), with FOCA's
development image of that commit as the base image (tagged by date, e.g.,
`elixircloud/foca:20261023-py3.13`, pinned by digest, as the tag moves with
later commits of that day). The `Dockerfile` records the commit as
`## FOCA commit: <sha>`, which the tests check (`tests/test_build.py`); the
image build doesn't reinstall FOCA, but fails unless the base image has FOCA
of that commit (`scripts/image_requirements.py`). Dependabot ignores the base
image meanwhile. Such pins are temporary: the release workflow fails while a
dependency (also an optional one) is a direct reference or names a
pre-release or a local version (e.g., `+g<commit>`), the base image has no
release tag, or a chart dependency has another version than a release
(`scripts/check_release_pins.py`, tested in
`tests/test_check_release_pins.py`, whose tests also fail for checkouts of
release tags with such pins), so replace them by the released versions
before tagging. This applies to pre-releases of the app, too: they are built
on FOCA releases.

In curated notes, link all changes of the release as the compare view from
the previous release's tag (e.g.,
`https://github.com/elixir-cloud-aai/proTES/compare/vA.B.C...vX.Y.Z`)
rather than naming a range of pull requests, which goes stale when more pull
requests are merged before the release.

Maintainers: the workflows rely on these repository settings: environment
`dev-images` limited to branch `dev`; environment `release` with a required
reviewer; a tag ruleset for `v*` that restricts who may create them and
prevents moving or deleting them; `main` protected against non-fast-forward
pushes; and the Docker Hub secrets (`DOCKERHUB_LOGIN`, `DOCKERHUB_TOKEN`)
only in the two environments, not as repository secrets. The Docker Hub
organization is the repository variable `DOCKERHUB_ORG` (by default
`elixircloud`; forks set it to the organization they publish to). On Read
the Docs, an automation rule activates the versions of tags (`v*`), so that
the docs linked from packaged charts exist.

The release workflow installs a fixed Helm version (`version:` of
`azure/setup-helm` in `release.yaml`). Dependabot updates the action, but not
that version: bump it by hand.
