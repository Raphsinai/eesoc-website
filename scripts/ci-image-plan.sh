#!/usr/bin/env bash
# Decide whether a checked release image already exists, can reuse a previous
# immutable image, or must be built. Called after Buildx and GHCR login.
set -euo pipefail

: "${RELEASE_SHA:?}" "${PREVIOUS_SHA:?}" "${BUILD_CONTEXT:?}" "${IMAGE_NAME:?}" "${OWNER_LC:?}" "${GITHUB_OUTPUT:?}"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ && "$PREVIOUS_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo '::error::Release and baseline must be full commit SHAs'
  exit 1
}
git merge-base --is-ancestor "$PREVIOUS_SHA" "$RELEASE_SHA" || {
  echo '::error::Staged image source is not an ancestor of the release'
  exit 1
}

repository="ghcr.io/${OWNER_LC}/eesoc-${IMAGE_NAME}"
target="${repository}:${RELEASE_SHA}"
source="${repository}:${PREVIOUS_SHA}"
mode=build

# Duplicate CI rerun: never rebuild an immutable SHA tag already in the registry.
if docker buildx imagetools inspect "$target" >/dev/null 2>&1; then
  mode=exists
  source="$target"
elif git diff --quiet "$PREVIOUS_SHA" "$RELEASE_SHA" -- "$BUILD_CONTEXT" .github/workflows/deploy.yml; then
  # The entire Docker build context and CI build specification are unchanged
  # from the staging image. Retag it; no duplicate dependency install or build.
  # A missing prior image is a cache miss: do the normal checked build.
  if docker buildx imagetools inspect "$source" >/dev/null 2>&1; then
    mode=reuse
  fi
else
  status=$?
  [ "$status" -eq 1 ] || { echo '::error::Unable to compare Docker build contexts'; exit "$status"; }
fi

{
  echo "mode=$mode"
  echo "source=$source"
  echo "target=$target"
  echo "latest=${repository}:latest"
} >> "$GITHUB_OUTPUT"
echo "Image ${IMAGE_NAME}: $mode (source $source)"
