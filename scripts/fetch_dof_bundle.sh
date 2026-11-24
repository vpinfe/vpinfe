#!/usr/bin/env bash
set -euo pipefail

DOF_REPO="${DOF_REPO:-vpinfe/libdof-python}"
DOF_VERSION="${DOF_VERSION:-latest}"

if [[ "${1:-}" != "" ]]; then
  DOF_TRIPLET="$1"
else
  case "$(uname -s)" in
    Linux*) DOF_TRIPLET="linux-x64" ;;
    Darwin*) DOF_TRIPLET="macos-arm64" ;;
    MINGW*|MSYS*|CYGWIN*) DOF_TRIPLET="win-x64" ;;
    *)
      echo "Unknown OS. Pass triplet explicitly: linux-x64 | macos-arm64 | win-x64"
      exit 1
      ;;
  esac
fi

python3 scripts/fetch_dof_bundle.py \
  --repo "${DOF_REPO}" \
  --version "${DOF_VERSION}" \
  --triplet "${DOF_TRIPLET}" \
  --outdir "third_party/dof"

echo "Installed DOF bundle for ${DOF_TRIPLET} at third_party/dof"
