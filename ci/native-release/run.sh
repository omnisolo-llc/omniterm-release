#!/usr/bin/env bash
# Build the small public launcher without exposing release secrets to Cargo.
set -euo pipefail
umask 077
caller="$(pwd -P)"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
base="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
scratch="$(mktemp -d "$base/omni-native-launcher.XXXXXXXX")"
scratch="$(cd "$scratch" && pwd -P)"
trap 'rm -rf -- "$scratch" 2>/dev/null || true' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
mkdir "$scratch/home" "$scratch/cargo" "$scratch/target"
rustup_home="${RUSTUP_HOME:-${HOME:?HOME is required}/.rustup}"
cd "$scratch"
manifest="$here/Cargo.toml"
native_scratch="$scratch"
if [[ "${OS:-}" == Windows_NT ]]; then
  manifest="$(cygpath -m "$manifest")"
  native_scratch="$(cygpath -m "$scratch")"
  rustup_home="$(cygpath -m "$rustup_home")"
fi
failed() {
  echo 'Public native launcher compilation failed; output is confined to private diagnostics.' >&2
  if [[ -n "${DIAGNOSTICS_PUBLIC_KEY:-}" ]]; then
    touch "$scratch/task.log"
    sealed_out="${RUNNER_TEMP:?}/encrypted-diagnostics/diagnostics.sealed"
    if [[ "${OS:-}" == Windows_NT ]]; then
      sealed_out="$(cygpath -m "$sealed_out")"
    fi
    env -i PATH="$PATH" DIAGNOSTICS_PUBLIC_KEY="$DIAGNOSTICS_PUBLIC_KEY" \
      node "$here/../seal_diagnostics.cjs" "$native_scratch" "$sealed_out" \
      >/dev/null 2>&1 || true
  fi
  exit 1
}
env -i PATH="$PATH" HOME="$native_scratch/home" USERPROFILE="$native_scratch/home" RUSTUP_HOME="$rustup_home" \
  SYSTEMROOT="${SYSTEMROOT:-${SystemRoot:-}}" WINDIR="${WINDIR:-}" \
  SYSTEMDRIVE="${SYSTEMDRIVE:-${SystemDrive:-}}" COMSPEC="${COMSPEC:-${ComSpec:-}}" \
  TEMP="$native_scratch" TMP="$native_scratch" \
  CARGO_HOME="$native_scratch/cargo" CARGO_TARGET_DIR="$native_scratch/target" \
  CARGO_BUILD_JOBS=1 rustup toolchain install 1.95.0 --profile minimal --no-self-update \
  >"$scratch/bootstrap.log" 2>&1 || failed
env -i PATH="$PATH" HOME="$native_scratch/home" USERPROFILE="$native_scratch/home" RUSTUP_HOME="$rustup_home" \
  SYSTEMROOT="${SYSTEMROOT:-${SystemRoot:-}}" WINDIR="${WINDIR:-}" \
  SYSTEMDRIVE="${SYSTEMDRIVE:-${SystemDrive:-}}" COMSPEC="${COMSPEC:-${ComSpec:-}}" \
  TEMP="$native_scratch" TMP="$native_scratch" \
  CARGO_HOME="$native_scratch/cargo" CARGO_TARGET_DIR="$native_scratch/target" \
  CARGO_BUILD_JOBS=1 cargo +1.95.0 build --locked --release \
  --bin omni-release-launcher --manifest-path "$manifest" \
  >>"$scratch/bootstrap.log" 2>&1 || failed
# Only this reviewed executable receives the original protected step environment.
# Compilation stays isolated above; caller-relative CLI inputs must retain the
# same meaning they had at the workflow boundary.
cd "$caller"
launcher="$scratch/target/release/omni-release-launcher"
if [[ -f "$launcher.exe" ]]; then
  launcher="$launcher.exe"
fi
"$launcher" "$@"
