#!/usr/bin/env bash

yarn --pure-lockfile install

source scripts/version

if [[ $COMMIT_BRANCH == "main" ]]; then
  VERSION="latest"
else
  VERSION=$COMMIT_BRANCH
fi

echo "CI_BUILD_TAG: ${CI_BUILD_TAG}"
echo "GIT_TAG: ${GIT_TAG}"
echo "TAG_VERSION: ${TAG_VERSION}"

echo "CI Build Args"
echo "COMMIT: ${COMMIT}"
echo "COMMIT_BRANCH: ${COMMIT_BRANCH}"
echo "VERSION: ${VERSION}"

if [ -n "$GIT_TAG" ]; then
  COMMIT=$COMMIT COMMIT_BRANCH=$COMMIT_BRANCH VERSION=$CI_BUILD_TAG ./scripts/build-pkg.sh ${1} "true"
else
  COMMIT=$COMMIT COMMIT_BRANCH=$COMMIT_BRANCH VERSION=$VERSION ./scripts/build-pkg.sh ${1} "true"
fi

EXIT_CODE=$?

if [ -n "$GIT_TAG" ]; then
  export PKG_NAME=${1}-${CI_BUILD_TAG}
  export PKG_TARBALL=${PKG_NAME}.tar.gz
else
  export PKG_NAME=${1}-${VERSION} # harvester-release-harvester-v1.5 or harvester-latest
  export PKG_TARBALL=${PKG_NAME}.tar.gz # harvester-release-harvester-v1.5.tar.gz or harvester-latest.tar.gz
fi

echo "CI Build Artifacts"
echo "Package Directory: ${PKG_NAME}"
echo "Package Tarball: ${PKG_TARBALL}"

ENV_OUTPUT="${GITHUB_OUTPUT:-"temp-env"}"
echo "PKG_TARBALL=${PKG_TARBALL}" >> "$ENV_OUTPUT"
echo "PKG_NAME=${PKG_NAME}" >> "$ENV_OUTPUT"

exit $EXIT_CODE
