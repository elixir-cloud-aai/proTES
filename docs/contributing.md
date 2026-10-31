# Contributing

This project is a community effort and lives off your contributions, be it
bug reports, feature requests, discussions, documentation or code. It follows
the [contributor guide of ELIXIR
On-Cloud](https://elixir-cloud-aai.github.io/contribute/), which also
describes the project's values and what it offers regular contributors; this
page sums up how it applies to this repository, an app built with
[FOCA](https://foca.readthedocs.io/).

Please follow the [code of
conduct](https://github.com/elixir-cloud-aai/.github/blob/main/CODE_OF_CONDUCT.md)
in all interactions with the community. To report a violation, email
[cloud-service@elixir-europe.org](mailto:cloud-service@elixir-europe.org).

Report vulnerabilities privately, not in issues, as described in the [security
policy](https://github.com/elixir-cloud-aai/.github/blob/main/SECURITY.md).

## Getting started

1. Find an issue in the [issue
   tracker](https://github.com/elixir-cloud-aai/protes/issues), or
   open one (see [Issues](#issues)).
2. Join the ELIXIR On-Cloud chat and introduce yourself in the open source
   community channel: your skills and experience, your GitHub username and
   the issues you are interested in (see the
   [workflow](https://elixir-cloud-aai.github.io/contribute/workflow/); send
   a direct message to a project lead instead if you don't want to share
   these publicly). Once you are a member of the GitHub organization, you can
   assign issues to yourself.
3. Set up a development environment (see [Development](development.md)) and
   start coding.

One-time contributors can skip the second step and open pull requests from
forks; some checks may need a maintainer's approval to run for them.

## Issues

Use the issue tracker to propose features, report bugs and discuss existing
issues; use the chat for questions and informal discussions. Write issues
concisely, but with enough detail and context (links, logs, screenshots) for
others to start working on them; bug reports need steps to reproduce. The
maintainers add labels and milestones.

**Always work on an issue:** before you change code, make sure there is an
issue for it and that the maintainers agree with the approach, so that no work
is in vain.

## Branches and pull requests

The project follows a variant of the GitHub flow: all changes reach the
development branch `dev` through pull requests, which are reviewed and
tested; `main` only holds releases: it only moves forward to released
commits (see [Versioning and
releases](development.md#versioning-and-releases)).

1. Create a feature branch from an up-to-date `dev`, named after the type and
   topic of the change, e.g., `fix/<topic>` or `docs/<topic>`.
2. Commit your changes, push the branch and open a pull request against `dev`
   (not `main`) that closes the issue (`Closes #<issue>`) and says what
   changes, why, and how you tested it.
3. Get your pull request reviewed: once it is ready for review, automated
   reviewers (Sourcery, GitHub Copilot) review every pull request, and a
   maintainer also reviews external contributions. Address the comments by
   pushing further commits or by replying why you disagree (there may be
   several rounds).
4. Once the checks pass and all conversations are resolved, the maintainers
   squash-merge the pull request into `dev`.

Keep pull requests focused: if a change can't be summarized in a title of 50
characters, split it into several pull requests. Feature branches are unstable:
don't build on other people's branches without talking to them first.

## Commit messages and pull request titles

Pull request titles, which become the commit messages on `dev`, follow the
[Conventional Commits](https://www.conventionalcommits.org/) specification:

```text
<type>[(<scope>)][!]: <description>
```

- The whole title line has at most 50 characters.
- The description starts with a verb in the imperative mood (e.g., `add`,
  `fix`) and has no trailing period.
- `!` (and a `BREAKING CHANGE:` footer in the body) marks breaking changes.
- The type names the predominant reason for the change:

| Type | Changes |
| --- | --- |
| `feat` | new functionality |
| `fix` | bug fixes |
| `perf` | performance improvements |
| `refactor` | code changes that neither add features nor fix bugs |
| `style` | formatting, docstrings and type hints of the code |
| `build` | build system, packaging and dependencies |
| `ci` | CI and release workflows |
| `docs` | documentation |
| `test` | tests |
| `chore` | other maintenance, e.g., `.gitignore` |
| `revert` | reverts of earlier commits |

Use `feat`, `fix`, `perf`, `refactor` and `style` only for changes of what is
deployed: the app (`pro_tes/`) and its Helm chart (`deployment/`).
Commit messages on feature branches may follow the same format, which makes the
history easier to review.

## Citation metadata

proTES is cited with the metadata in
[`CITATION.cff`](https://github.com/elixir-cloud-aai/protes/blob/dev/CITATION.cff)
([Citation File Format](https://citation-file-format.github.io/)), which
GitHub ("Cite this repository") and Zenodo use. Its `doi` is the Zenodo
concept DOI, which stays the same for all versions (see
[Citation](index.md#citation)). Its authors are the people who committed at
least 100 lines (lines added) to `main`, the release branch (to `dev` before
the first release), not counting bots, vendored or
generated files and the reformatting commits in `.git-blame-ignore-revs`.
They are ordered by lines, then by commits; equal contributors are listed
alphabetically by family name and marked with an asterisk (*). Affiliations
come from public ORCID records only. To add or correct your entry (e.g.,
your name, [ORCID iD](https://orcid.org/) or affiliation), open a pull
request that changes only your entry; don't add e-mail addresses. CI
validates the file (`cffconvert --validate`). Before each release, `python
scripts/citation.py set X.Y.Z` sets its `version` and `date-released`, and
the release workflow fails if they don't match the release (`python
scripts/citation.py check X.Y.Z`; see [Versioning and
releases](development.md#versioning-and-releases)).

## Python guidelines

The app targets one Python version, that of the base image in the
`Dockerfile` (3.13): it is the minimum of `requires-python` in
`pyproject.toml`, the target of ruff and mypy, and the version of CI and Read
the Docs (`tests/test_build.py` checks that they match).
Most of the following is checked by pre-commit and CI (see
[Development](development.md) for how to run the checks locally); the rest is
checked in reviews.

- **Packaging:** the package, its dependencies and the configuration of the
  tools are in `pyproject.toml`; runtime dependencies in
  `project.dependencies`, those for development and the documentation in the
  extras `dev` and `docs`. Don't add `setup.py` or requirements files.
- **Formatting and linting:** [ruff](https://docs.astral.sh/ruff/) formats
  the code (`ruff format`, compatible with Black, 88 characters per line) and
  lints it (`ruff check`) with the rule set shared by FOCA and FOCA apps:
  pycodestyle, Pyflakes, isort, flake8-bugbear, flake8-comprehensions,
  flake8-simplify, Pylint, Ruff's own rules, pyupgrade, bandit (security),
  logging format and pydocstyle, also for the tests (`pyproject.toml`, where
  each ignored rule says why). Prefer explicit checks to suppressions
  (`# noqa`, `# type: ignore`); justify the rare exceptions in the comment.
- **Docstrings:** Google-style docstrings at all levels: packages, modules,
  classes, methods and functions, also private ones and tests, with a
  one-line summary and, where applicable, the sections `Args`, `Returns` (or
  `Yields`), `Raises` and, for classes, `Attributes`; consider `Examples` and
  `Note`. Ruff's pydocstyle rules (Google convention) check those of public
  objects and their `Args`; reviews check the others. The [API
  reference](reference.md) is generated from them.
- **Type hints:** complete type hints for all functions and methods (also of
  the tests), including `*args` and `**kwargs`, and generic types with their
  parameters (e.g., `dict[str, Any]`, not `dict`); add hints to module-level
  variables and to local variables whose type isn't obvious.
  [mypy](https://mypy.readthedocs.io/) checks the hints, and that functions
  and generic types have them (`disallow_untyped_defs`,
  `disallow_any_generics`); reviews check those of variables.
- **Tests:** [pytest](https://docs.pytest.org/), with coverage measured by
  [coverage](https://coverage.readthedocs.io/): keep it at 100% of
  `pro_tes` and `scripts`, which is enforced: `coverage report` (and
  CI) fails below 100% (`fail_under` in `pyproject.toml`), and so do
  Codecov's checks of the whole code and of the changes of a pull request
  (`codecov.yml`). Add tests that fail without your change.
- **Further checks:** [ast-grep](https://ast-grep.github.io/) (rules in
  `.ast-grep/`, with tests), [semgrep](https://semgrep.dev/) (security rules
  in `.semgrep/`), [gitleaks](https://github.com/gitleaks/gitleaks) (secrets)
  and [actionlint](https://github.com/rhysd/actionlint) (workflows), and
  pre-commit's checks of YAML, whitespace, merge conflicts and large files.

Suppress a finding only if it is a false positive or can't be avoided, on its
line, with a comment with the reason (see [Development](development.md)).

## Documentation

Update the documentation with every change that affects it: the pages in
`docs/` (built with MkDocs, see [Documentation](development.md#documentation)),
the README of the Helm chart and the docstrings. Describe user-visible
changes in the pull request, and in the notes of the next release if they
exist already (`docs/releases/`, see [Versioning and
releases](development.md#versioning-and-releases)).
