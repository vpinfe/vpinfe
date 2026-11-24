#!/usr/bin/env bash
set -euo pipefail

LIBDMDUTIL_REPO="${LIBDMDUTIL_REPO:-vpinfe/libdmdutil-python}"
LIBDMDUTIL_VERSION="${LIBDMDUTIL_VERSION:-latest}"

if [[ "${1:-}" != "" ]]; then
  LIBDMDUTIL_TRIPLET="$1"
else
  case "$(uname -s)" in
    Linux*) LIBDMDUTIL_TRIPLET="linux-x64" ;;
    Darwin*) LIBDMDUTIL_TRIPLET="macos-arm64" ;;
    MINGW*|MSYS*|CYGWIN*) LIBDMDUTIL_TRIPLET="win-x64" ;;
    *)
      echo "Unknown OS. Pass triplet explicitly: linux-x64 | macos-arm64 | win-x64"
      exit 1
      ;;
  esac
fi

python3 scripts/fetch_libdmdutil_bundle.py \
  --repo "${LIBDMDUTIL_REPO}" \
  --version "${LIBDMDUTIL_VERSION}" \
  --triplet "${LIBDMDUTIL_TRIPLET}" \
  --outdir "third_party/libdmdutil"

echo "Installed libdmdutil bundle for ${LIBDMDUTIL_TRIPLET} at third_party/libdmdutil"
