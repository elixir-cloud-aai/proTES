#!/usr/bin/env bash
# Pin plugins for the derived-image recipe (see `docs/plugin-images.md`):
# resolves the pip requirements of `<plugins.in>` inside `<image>` (the
# proTES release image), with the image's installed packages as
# constraints, and writes the distributions that the image lacks, with their
# exact versions and the hashes of their wheels, to `<plugins.txt>`. Fails,
# removing it, if pip can't resolve them (e.g., a plugin needs other
# versions of the image's packages). Further arguments are options of
# `docker run` (e.g., `--env PIP_INDEX_URL=<index>`). Needs Docker and jq.
# Usage: resolve.sh <image> <plugins.in> <plugins.txt> [<docker run option>...]
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <image> <plugins.in> <plugins.txt> [<docker run option>...]" >&2
  exit 2
fi
image="$1" requirements="$2" output="$3"
shift 3
# No stale pins of an earlier run if this one fails
rm -f "${output}"

report="$(docker run --rm --interactive "$@" "${image}" sh -c \
  'cat > /tmp/plugins.in \
    && pip list --format freeze > /tmp/image.txt \
    && pip install --dry-run --quiet --no-cache-dir --only-binary :all: \
      --constraint /tmp/image.txt --report - --requirement /tmp/plugins.in' \
  < "${requirements}")"
pins="$(jq -r '.install[] | "\(.metadata.name)==\(.metadata.version) --hash=sha256:\(.download_info.archive_info.hashes.sha256)"' \
  <<< "${report}")"
printf '%s\n' "${pins}" | sed '/^$/d' > "${output}"
