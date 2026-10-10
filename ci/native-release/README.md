# Native public release launcher

Standalone Rust 1.95.0 launcher with a committed lockfile and a thin Bash wrapper.

## Commands

- `bash ci/native-release/run.sh validate-request`
- `bash ci/native-release/run.sh resolve`
- `bash ci/native-release/run.sh run`
- `bash ci/native-release/run.sh agent-source`
- `bash ci/native-release/run.sh agent-run`
- `bash ci/native-release/run.sh verify-agent-handoff --directory PATH --version VERSION --platform PLATFORM`
- `bash ci/native-release/run.sh verify-source --root PATH --sha SHA`
- `bash ci/native-release/run.sh verify-ios`
- `bash ci/native-release/run.sh windows-sdk`
- `bash ci/native-release/run.sh retain-artifacts`
- `bash ci/native-release/run.sh publish-candidates --directory PATH --plan PATH`

## Verification

Run `bash ci/test-native-release.sh` to execute the launcher unit and contract tests, formatting check, and clippy lints.
