# Plugin images

Plugins are Python distributions that must be installed in the image of
proTES: nothing is installed at runtime, and the Helm chart keeps the root
file system of the containers read-only. The published images of proTES
have FOCA's built-in plugins only (there is no image with all plugins, as
plugins come from several repositories, in many combinations). To deploy
other plugins, build an image derived from a release image of proTES with
the recipe of this page: it installs exactly the distribution files that you
pinned, reviewed and verified, and nothing else. Then enable the plugins in
the config (see [Plugins](plugins.md)): installed is not enabled.

!!! note "No plugins of foca-plugins published yet"

    The plugins of ELIXIR On-Cloud, e.g., `tes.routing.distance`, are
    developed in the repository foca-plugins and will be published to PyPI
    as single distributions (`foca-plugin-<group>-<subgroup>-<name>`) and
    as release trains (extras of the distribution `foca-plugins`, e.g.,
    `foca-plugins[tes-routing]`); see its
    [documentation](https://foca-plugins.readthedocs.io/en/latest/installing/).
    The names below follow that scheme; the recipe works the same for
    plugins of any repository, and CI tests it with a test plugin (see
    [Testing the recipe](#testing-the-recipe)).

The commands below run in a directory of their own (the build context),
with Docker and [jq](https://jqlang.org/). pip runs inside the proTES image,
so that the plugins are resolved for its Python version, platform and
installed packages.

## Base image

Build on a release image of proTES, by digest (see [Verifying images and
charts](operations.md#verifying-images-and-charts) to check it):

```bash
docker buildx imagetools inspect docker.io/elixircloud/protes:{{ version }}  # shows the digest
export PROTES_IMAGE=docker.io/elixircloud/protes:{{ version }}@sha256:<digest>
```

## Pinning plugins

List the plugins in `plugins.in`, as pip requirements: single distributions
or a release train, e.g.:

```text
foca-plugin-tes-routing-distance==<version>
```

Resolve them against the image, with its installed packages as
constraints: pip lists the distributions that the image lacks (the plugins
and their dependencies), with their exact versions and the hashes of their
wheels, in `plugins.txt` (pip's report replaces tools such as `pip-compile`,
as it resolves inside the image):

```bash
docker run --rm --volume "$PWD/plugins.in:/tmp/plugins.in:ro" "$PROTES_IMAGE" sh -c \
  'pip list --format freeze > /tmp/image.txt \
    && pip install --dry-run --quiet --no-cache-dir --only-binary :all: \
      --constraint /tmp/image.txt --report - --requirement /tmp/plugins.in' \
  | jq -r '.install[] | "\(.metadata.name)==\(.metadata.version) --hash=sha256:\(.download_info.archive_info.hashes.sha256)"' \
  > plugins.txt
```

The constraints keep the packages of the image as they are: resolving
fails if a plugin needs other versions of them (e.g., another FOCA version);
choose plugin versions that work with those of the image. Review
`plugins.txt` and keep it, e.g., in the repository of your deployment: it is
the complete list of what the image adds. If it is empty, the image has all
the plugins already, and there is nothing to build.

## Downloading and verifying

Download the wheels:

```bash
docker run --rm --user "$(id -u):$(id -g)" --volume "$PWD:/work" --workdir /work "$PROTES_IMAGE" \
  pip download --no-cache-dir --no-deps --only-binary :all: --require-hashes \
    --requirement plugins.txt --dest wheels
```

pip checks the hashes of `plugins.txt`. Before the build, also check where
the wheels come from: the plugins of foca-plugins are published with [PEP
740](https://peps.python.org/pep-0740/) attestations and GitHub artifact
attestations of their build provenance (see [Verifying
distributions](https://foca-plugins.readthedocs.io/en/latest/installing/#verifying-distributions)).
For example, check the GitHub artifact attestation of each of their wheels,
with the repository that publishes the plugin:

```bash
gh attestation verify wheels/<wheel> --repo <owner>/<repository>
```

Check other plugins and dependencies as their projects document.

## Building

Copy the files `Dockerfile` and `Dockerfile.dockerignore` of
`examples/plugin-image/` of the proTES repository (of the release that you
build on) to the directory, and build the image:

```bash
docker build --build-arg PROTES_IMAGE="$PROTES_IMAGE" \
  --tag registry.example.org/protes-plugins:{{ version }}-1 .
```

The `Dockerfile`:

```dockerfile
--8<-- "examples/plugin-image/Dockerfile"
```

The build installs the wheels of `wheels/` that `plugins.txt` lists, and
nothing else: without the package index and without further dependencies
(`--no-index --no-deps --require-hashes`). It fails if a hash differs or a
dependency of an installed distribution is missing (`pip check`). The image
runs as user 1000, as that of proTES. Only `plugins.txt` and `wheels/` are
sent to the build (`Dockerfile.dockerignore`), so that other files of the
directory, e.g., `.env` files, never reach it.

Scan the image for known vulnerabilities (e.g., with
[Trivy](https://trivy.dev/): `trivy image <image>`), push it to your
registry and sign it (e.g., with [cosign](https://docs.sigstore.dev/cosign/):
`cosign sign <image>@<digest>`), as the workflows of proTES do for its
images.

## Deploying

Deploy the image and enable the plugins, e.g., in the values file of the
Helm chart (see [Configuration](deployment.md#configuration) of the
chart's README):

```yaml
protes:
  image: registry.example.org/protes-plugins@sha256:<digest>
  tag: ""
  imagePullPolicy: IfNotPresent
plugins:
  available:
    tes.routing.distance:
      distribution: foca-plugin-tes-routing-distance
      version: "==0.1.0"  # as in plugins.txt
  pipeline:
    - id: route
      alternatives:
        - plugin: tes.routing.distance
        - plugin: foca.routing.ordered
```

The app refuses to start if an enabled plugin isn't installed, doesn't match
its pins (`distribution`, `version`), or doesn't support the TES API: a
wrong image fails at once, not with the first task.

Rebuild the image for every release of proTES that you deploy, and resolve
`plugins.in` against the new image again (see [Pinning
plugins](#pinning-plugins)), as its packages change. Plugins declare the
FOCA versions that they support (e.g., `foca>=0.18.0,<0.19`), so resolving
them against an image with another FOCA version fails (see [Pinning
plugins](#pinning-plugins)).

## Local builds

For development, the `Dockerfile` of proTES installs plugins given as pip
requirements in the build argument `PLUGINS` (empty by default), from the
package index and with their dependencies; Docker Compose passes the host's
`PLUGINS`:

```bash
PLUGINS="foca-plugin-tes-routing-distance==<version>" docker compose up --build --wait
```

Enable them in the config overlay (e.g., `config.dev.yaml`). These builds
are **not** pinned by hash nor reviewed: use the recipe above for images
that you deploy. `PLUGINS` is split at spaces (write specifiers without
spaces, e.g., `name>=1.0`) and recorded in the image's history: pass
requirement specifiers only, never credentials (e.g., of a private package
index).

## Testing the recipe

The smoke test of CI (`tests/smoke/plugins.sh`, see
[Development](development.md)) builds the image of the recipe from the app's
image with a test plugin (`tests/smoke/plugin/`, which tags each new task),
pinned by hash, runs proTES with it enabled, and checks that new tasks get
its tag.
