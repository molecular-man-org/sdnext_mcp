#!/usr/bin/env bash
# Install the pinned command-line tools the checks need (actionlint and
# gitleaks) into one folder, verifying each download against the checksum its
# release published. Works on Linux and macOS, x64 and arm64.
#
# Usage: scripts/ci-tools.sh [folder]
# Default folder: $RUNNER_TEMP/bin in CI, .tools/bin otherwise. In CI the
# folder is added to the PATH of the steps that follow.
#
# Owned by the template: change it there, not in a project.
set -euo pipefail

ACTIONLINT_VERSION=1.7.12
GITLEAKS_VERSION=8.30.1

dest=${1:-${RUNNER_TEMP:-$PWD/.tools}/bin}
mkdir -p "$dest"

os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *)
    echo "unsupported machine: $(uname -m)" >&2
    exit 1
    ;;
esac

expected() {
  case "$1" in
    actionlint_linux_amd64) echo 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 ;;
    actionlint_linux_arm64) echo 325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6 ;;
    actionlint_darwin_amd64) echo 5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644 ;;
    actionlint_darwin_arm64) echo aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f ;;
    gitleaks_linux_amd64) echo 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
    gitleaks_linux_arm64) echo e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080 ;;
    gitleaks_darwin_amd64) echo dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709 ;;
    gitleaks_darwin_arm64) echo b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5 ;;
    *)
      echo "no checksum recorded for $1" >&2
      return 1
      ;;
  esac
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

install_tool() {
  local tool=$1 version=$2 url=$3 want work
  if [ -x "$dest/$tool" ] && "$dest/$tool" version 2>/dev/null | grep -q "$version"; then
    echo "$tool $version is already in $dest"
    return 0
  fi
  want=$(expected "${tool}_${os}_${arch}")
  work=$(mktemp -d)
  curl -fsSL -o "$work/archive.tar.gz" "$url"
  if [ "$(sha256 "$work/archive.tar.gz")" != "$want" ]; then
    echo "checksum mismatch for $url" >&2
    rm -rf "$work"
    return 1
  fi
  tar -xzf "$work/archive.tar.gz" -C "$work" "$tool"
  mv "$work/$tool" "$dest/$tool"
  rm -rf "$work"
  echo "installed $tool $version into $dest"
}

# gitleaks names 64-bit Intel "x64"; actionlint names it "amd64".
gitleaks_arch=$arch
[ "$arch" = amd64 ] && gitleaks_arch=x64

install_tool actionlint "$ACTIONLINT_VERSION" \
  "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_${os}_${arch}.tar.gz"
install_tool gitleaks "$GITLEAKS_VERSION" \
  "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${os}_${gitleaks_arch}.tar.gz"

if [ -n "${GITHUB_PATH:-}" ]; then
  echo "$dest" >>"$GITHUB_PATH"
fi
