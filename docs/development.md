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

## Helm chart

The [Helm chart](deployment.md) in `deployment/` is a thin chart over FOCA's
library chart `foca-common` (see [Helm library
chart](https://foca.readthedocs.io/en/latest/helm-chart/)): its templates
are one-line includes of the library's templates, with the app's settings
and checks in `templates/_helpers.tpl` (`protes.settings`,
`protes.validate`), and templates of its own for Flower. `values.yaml` has
all values of the library chart, and `values.schema.json` is the library's
`app-values.schema.json` with the app's key (`protes`, plus `extraEnv`) and
`flower` added; update both with the library chart. The tests in
`tests/test_chart.py` render the chart with `helm template` and need
[Helm](https://helm.sh/docs/intro/install/) (the version of the `helm` job
of `.github/workflows/checks.yaml`) and the library chart in
`deployment/charts/`; they are skipped if Helm is not installed:

```bash
scripts/foca_common.sh  # TEMPORARY, see below
helm dependency build deployment  # the version locked in `Chart.lock`
helm lint deployment --strict -f deployment/ci/test-values.yaml
pytest tests/test_chart.py
```

TEMPORARY: until `foca-common` 0.2.0 (with the Celery worker and broker) is
released, the chart depends on the library chart of a commit on FOCA's `dev`
branch, recorded as `# FOCA commit: <sha>` in `deployment/Chart.yaml`:
`scripts/foca_common.sh` checks out that commit (only the library chart)
into `.foca-common/` (ignored by Git), which the chart's `file://`
repository points to. To move to another commit, change the line, remove
`.foca-common/`, run the script and `helm dependency update deployment`
(which updates `Chart.lock`), and update the values and the schema. Once
0.2.0 is released, the chart depends on it from
`oci://ghcr.io/elixir-cloud-aai/charts` instead, and the script is removed
([#246](https://github.com/elixir-cloud-aai/proTES/issues/246));
`scripts/check_release_pins.py` fails until then (see [Versioning and
releases](#versioning-and-releases)).

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
  `dev` (full SHA), to deploy a fixed commit. Only commits that are still the
  head of `dev` when their CI run reaches the image job get images.

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

Tags can't be moved or deleted (see the repository settings below), so a
check of the release workflow that fails after the tag is pushed fails
again on re-runs, and the release then needs a new patch version. The image
scan of the release workflow can fail after the tag, e.g., on a
vulnerability published since CI last scanned the image of the commit;
check the code scanning alerts before tagging.

The tag must be annotated and signed (`git tag -s`), with a key that GitHub can
verify (added to the tagger's GitHub account). The [release
workflow](https://github.com/elixir-cloud-aai/proTES/blob/dev/.github/workflows/release.yaml),
triggered by the tag, checks that GitHub verified the signature, that the
tag's commit is on `main` and its `CITATION.cff` has the release version and
a date that is not in the future (UTC), that the dependencies are releases
(see below), and that the [CI
workflow](https://github.com/elixir-cloud-aai/proTES/blob/dev/.github/workflows/checks.yaml)
passed for it (a run for the push of the commit to `dev`; if CI hasn't
finished, failed or was cancelled, e.g., the image job of a commit that a
later one superseded, re-run CI, then the release workflow). It packages
the Helm chart with the release version as `version` and `appVersion` (the
default image tag). In the packaged README, links to the latest docs and to
files on branch `dev` point to the release (`/en/vX.Y.Z/`, `blob/vX.Y.Z/`);
the release fails if any link to a version that moves is left
(`scripts/chart_readme.py`). The README in the repository is unchanged.
After approval in the `release` environment, it builds the image and pushes
it by digest (untagged, with SBOM and provenance attestations) and checks
that the app reports the release version; it then scans exactly that digest
for vulnerabilities, in a job without credentials. After a second approval
in the `release` environment (once the scan passed), it attests the build
provenance of the scanned digest and signs it (see [Verifying images and
charts](operations.md#verifying-images-and-charts)), tags exactly that image
`X.Y.Z` (and `latest`, if it is the highest stable release on `main`),
attests and signs the chart, and creates a GitHub release with the chart
(`.tgz`), its Sigstore bundle (`.tgz.sigstore.json`) and the release notes
(see below). Add curated notes to the
[changelog](changelog.md) in `mkdocs.yml` (`nav`; the docs build fails
otherwise), and refer to pages of the docs by name rather than by relative
links, which don't work in GitHub releases. No packages are published to
PyPI. Tags of alpha, beta and release candidate versions (e.g., `vX.Y.Zrc1`,
also `aN` and `bN`) get chart version `X.Y.Z-rc1` (or `-aN`, `-bN`) and never
update the `latest` image. These and, until 1.0.0, all other releases are
GitHub pre-releases (never marked as the latest release). Digests of failed
runs stay on Docker Hub, untagged and unsigned. Images of commits on `dev`
are published by the CI workflow (environment `dev-images`).

The release workflow derives the version, the chart version, the kind of
release (stable, GitHub pre-release), whether the image is tagged `latest`
and the previous release (for the generated notes) from the tag with
`scripts/release_version.py`, which is tested over a table of tags
(`tests/test_release_version.py`) and checked like the app (ruff, mypy,
coverage); change these rules there, not in the workflow.

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
release tag, or a chart dependency is not a release version or not from an
OCI or HTTPS repository (e.g., the `file://` checkout of a FOCA commit, see
[Helm chart](#helm-chart); `scripts/check_release_pins.py`, tested in
`tests/test_check_release_pins.py`, whose tests also fail for checkouts of
release tags with such pins), so replace them by the released versions
before tagging. This applies to pre-releases of the app, too: they are built
on FOCA releases.

Release notes follow the format agreed for all repositories of ELIXIR
On-Cloud:

1. An intro paragraph saying what the release brings, with a link to the
   upgrade steps (e.g., on the [Upgrading](upgrading.md) page) if there are
   any.
2. Grouped sections, with empty ones left out, in this order:
   `## Breaking changes`, `## Features`, `## Security`, `## Fixes`,
   `## Documentation`, `## Maintenance`. They hold bullets only, and each
   bullet ends with its issue and pull request numbers, e.g., `(#12, #34)`
   (several pairs separated by semicolons, e.g., `(#12, #34; #56, #78)`).
3. `## Commits`: GitHub's generated notes, verbatim (including "New
   Contributors"), without their `## What's Changed` header, as they credit
   the authors.
4. Last, GitHub's generated `**Full Changelog**: ...` line (no compare link
   of our own).

The curated notes in `docs/releases/X.Y.Z.md` (for pre-releases, e.g.,
`X.Y.Zrc1.md`) hold parts 1 and 2, without a title (the release's title is
its tag); the release workflow adds parts 3 and 4 from GitHub's
[generate-notes](https://docs.github.com/en/rest/releases/releases#generate-release-notes-content-for-a-release)
API, for the changes since the previous published release in the history of
the release commit (for stable releases the previous stable release, or the
previous pre-release if there is no stable one, as the generated notes can't
start before the first tag; for alpha, beta and release candidate versions
the previous release of any kind; all changes for the first release).
Generated notes in an unexpected format (without the `**Full Changelog**`
line) are kept as they are, with a warning, as the image is published by
then. Without curated notes, the release gets GitHub's generated notes
alone. Curated notes are published as written, so the release workflow
fails if they contain `TODO`, are not in this format or link to versions
that move (the latest docs, `/en/latest/`, or files on branch `dev`,
`blob/dev/`; link the release's, e.g., `/en/vX.Y.Z/`, `blob/vX.Y.Z/`, as the
published notes don't change) (`scripts/release_notes.py check`, tested in
`tests/test_release_notes.py`, which also checks the notes in
`docs/releases/`); on re-runs, it replaces the notes of an existing release
(also overwriting manual edits). Detailed upgrade steps belong on the
[Upgrading](upgrading.md) page, linked from the intro.

Maintainers: the workflows rely on these repository settings: environment
`dev-images` limited to branch `dev`; environment `release` with a required
reviewer; a tag ruleset for `v*` that restricts who may create them and
prevents moving or deleting them; `main` protected against non-fast-forward
pushes; and the Docker Hub secrets (`DOCKERHUB_LOGIN`, `DOCKERHUB_TOKEN`)
only in the two environments, not as repository secrets. The Docker Hub
organization is the repository variable `DOCKERHUB_ORG` (by default
`elixircloud`; forks set it to the organization they publish to), so that
the scan jobs, which have no secrets, know the image name too, and the
released chart's default image (`protes.image`) is the published one (the
chart's README still names upstream's default). On Read the Docs, the
default version is `stable` (the highest release) from the first release
on, and the default branch is left empty, so that `latest` follows the
repository's default branch, `dev`; an automation rule activates the
versions of tags (`v*`), so that the docs linked from packaged charts
exist.

The CI workflow builds the image of every pull request and scans it with
[Trivy](https://trivy.dev/) (`.github/actions/scan-image`), and a weekly
workflow (`scan.yaml`) scans `latest`. For pushes to `dev` and for releases,
the workflows push the image by digest, untagged, scan exactly that digest
in a job without credentials or signing identity, and only if the scan
passes attest, sign and tag it, so that the published images are the
scanned ones; a digest that fails the scan stays untagged and unsigned. The
scans fail on critical and high vulnerabilities that have a fix, and upload
the findings that have a fix (all severities) to code scanning (Security tab;
not for pull requests from forks, nor in the release workflow); findings
without a fix appear there once a fix is released. The weekly scan also
uploads a full report, findings without a fix included, as the workflow
artifact `trivy-report-release-image` of each run (`trivy-report.json` and
`trivy-report.txt`, a table; Actions tab, "Scan release image"); it is not
uploaded to code scanning. Accepted findings are left out of all results. To
accept a finding (e.g., if it doesn't affect the app), add it to
`.trivyignore.yaml` with the affected package versions (`purls`), the reason
(`statement`) and an expiry date (`expired_at`), after which the scans fail
again. Trivy also scans for secrets (critical and high ones fail the scans
too); accept those under `secrets:` in the same file. Dependabot
updates the actions, but not the Trivy version (`version:` in
`.github/actions/scan-image/action.yml`): bump it by hand. Images of commits
on `dev` are signed and attested like release images, by the CI workflow.
All of this needs no secrets beyond the Docker Hub ones: signing uses the
workflows' OIDC tokens (permission `id-token: write`). Re-running all jobs
of the release workflow builds, scans, signs and tags a new image digest
(the image build is not reproducible); an earlier digest that was published
stays signed. Re-running only the failed jobs reuses the digest already
pushed.

The release workflow installs a fixed Helm version (`version:` of
`azure/setup-helm` in `release.yaml`). Dependabot updates the action, but not
that version: bump it by hand.
