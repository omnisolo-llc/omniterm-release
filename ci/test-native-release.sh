#!/usr/bin/env bash
# Native unit/local-Git/process tests, not an Apple device certificate.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
manifest="$here/native-release/Cargo.toml"
guard="$(mktemp -d)"
trap 'rm -rf -- "$guard"' EXIT
mkdir "$guard/bin"
export OMNI_PYTHON_GUARD_LOG="$guard/python-invocations"
for tool in python python2 python3 python3.12 python3.13 python3.14 pip pip3 pipx pypy pypy3 py; do
  printf '%s\n' '#!/bin/sh' 'printf "%s\n" "$0" >> "$OMNI_PYTHON_GUARD_LOG"' 'exit 97' > "$guard/bin/$tool"
  chmod 700 "$guard/bin/$tool"
done
export PATH="$guard/bin:$PATH"
export CARGO_BUILD_JOBS=1
check() {
  if ! "$@" >>"$guard/bootstrap.log" 2>&1; then
    echo 'Native contract check failed; compiler and test output was confined to private diagnostics.' >&2
    if [[ -n "${DIAGNOSTICS_PUBLIC_KEY:-}" && -n "${RUNNER_TEMP:-}" ]]; then
      touch "$guard/task.log"
      node "$here/seal_diagnostics.cjs" "$guard" "$RUNNER_TEMP/encrypted-diagnostics/diagnostics.sealed" >/dev/null 2>&1 || true
    fi
    exit 1
  fi
}
if ! rustup run 1.95.0 cargo fmt --version >>"$guard/bootstrap.log" 2>&1 \
  || ! rustup run 1.95.0 cargo clippy --version >>"$guard/bootstrap.log" 2>&1; then
  check rustup toolchain install 1.95.0 --profile minimal --component rustfmt,clippy --no-self-update
fi
check cargo +1.95.0 --version
check cargo +1.95.0 fmt --manifest-path "$manifest" --check
check cargo +1.95.0 test --locked --manifest-path "$manifest" -- --test-threads=1
check cargo +1.95.0 clippy --locked --all-targets --manifest-path "$manifest" -- -D warnings
sed -n '/^test result:/p' "$guard/bootstrap.log"
if [[ -e "$OMNI_PYTHON_GUARD_LOG" ]]; then
  echo 'A native test invoked a forbidden Python tool.' >&2
  exit 1
fi
echo 'Native suites passed with Python/pip commands blocked in PATH.'
