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
pytest
```

The tests need neither a database nor TES backends: they use an in-memory
database ([mongomock](https://github.com/mongomock/mongomock)) and fake TES
backends, and have no network access. CI also runs them against MongoDB 7.0;
to do so locally, set `MONGODB_TEST_URI` to a single-node MongoDB without
other data, one per test run (the tests drop its databases `taskStore` and
`protes` before each test, and refuse to start if one of them exists), e.g.:

```bash
docker run -d --rm --name mongodb-test -p 127.0.0.1:27017:27017 mongo:7.0
MONGODB_TEST_URI=mongodb://localhost:27017 pytest
docker stop mongodb-test
```

CI also runs the app with Docker Compose, with a fake TES backend instead of
the TES instances of the config (`tests/smoke/`), and checks that tasks are
relayed to it and tracked until they are complete. To start this setup
locally:

```bash
docker compose -f docker-compose.yaml -f tests/smoke/compose.yaml up --build --wait
```

To try changes in the running app, rebuild it with
`docker compose up --build -d`. `python -m pro_tes.app` runs the app with
Flask's development server instead of Gunicorn (see
[Server](configuration.md#server)); it needs a MongoDB at `db.host` and a
RabbitMQ at `jobs.host` of the config.

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
it with live reloading (Python 3.11 or later):

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
