#!/usr/bin/env bash
# Check out the library chart foca-common from the FOCA commit that the Helm
# chart pins (`# FOCA commit: <sha>` in `deployment/Chart.yaml`) into
# `.foca-common/` (ignored by Git), for `helm dependency build deployment`.
#
# TEMPORARY: until foca-common 0.2.0 is released (#246); then the chart
# depends on the release in `oci://ghcr.io/elixir-cloud-aai/charts`, and this
# script is removed.
#
# Usage: `scripts/foca_common.sh`; fails if the checkout exists with another
# commit or local changes (remove `.foca-common/` to check it out again).

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
chart="${root}/deployment/Chart.yaml"
dir="${root}/.foca-common"
url="https://github.com/elixir-cloud-aai/foca.git"

commit="$(sed -n 's/^[[:space:]]*# FOCA commit: \([0-9a-f]\{40\}\)[[:space:]]*$/\1/p' "${chart}")"
if [[ ! "${commit}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "error: ${chart} has no (single) line '# FOCA commit: <full SHA>'" >&2
    exit 1
fi

if [[ ! -e "${dir}" ]]; then
    git init --quiet "${dir}"
    git -C "${dir}" sparse-checkout set charts/foca-common
    git -C "${dir}" fetch --quiet --depth 1 --filter=blob:none "${url}" "${commit}"
    git -C "${dir}" -c advice.detachedHead=false checkout --quiet FETCH_HEAD
fi

actual="$(git -C "${dir}" rev-parse HEAD 2>/dev/null || true)"
if [[ "${actual}" != "${commit}" ]]; then
    echo "error: ${dir} has commit '${actual}', not ${commit}: remove it and run this script again" >&2
    exit 1
fi
if [[ -n "$(git -C "${dir}" status --porcelain)" ]]; then
    echo "error: ${dir} has local changes: remove it and run this script again" >&2
    exit 1
fi
echo "foca-common of FOCA commit ${commit} in ${dir}/charts/foca-common"
