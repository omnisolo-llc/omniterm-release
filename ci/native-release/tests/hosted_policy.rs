//! Actual files and CLI observations; no workflow is submitted by these tests.
use serde_json::{Value, json};
use std::{
    fs,
    path::Path,
    process::{Command, Output},
};
use tempfile::TempDir;

const RUNNERS: [&str; 6] = [
    "ubuntu-24.04",
    "ubuntu-24.04-arm",
    "windows-2025",
    "windows-11-arm",
    "macos-15-intel",
    "macos-26",
];
fn fixture() -> TempDir {
    let dir = TempDir::new().unwrap();
    fs::create_dir_all(dir.path().join(".github/workflows")).unwrap();
    fs::write(dir.path().join("policy.json"), json!({"schema":1,"repository":"omnisolo-llc/omniterm-release","runners":RUNNERS,"prohibit_actions_storage":true}).to_string()).unwrap();
    dir
}
fn put(root: &Path, relative: &str, text: &str) {
    let path = root.join(relative);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, text).unwrap();
}
fn audit(root: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_omni-release-launcher"))
        .args(["audit-runners", "--workflows"])
        .arg(root.join(".github/workflows"))
        .arg("--policy")
        .arg(root.join("policy.json"))
        .env_remove("GITHUB_ACTIONS")
        .env_remove("GITHUB_REPOSITORY")
        .output()
        .unwrap()
}
fn codes(output: &Output) -> Vec<String> {
    let value: Value = serde_json::from_slice(&output.stdout)
        .expect("audit must return bounded findings, not raw workflow data");
    value["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["code"].as_str().unwrap().to_owned())
        .collect()
}
fn rejected(root: &Path, code: &str) {
    let output = audit(root);
    assert!(!output.status.success(), "unsafe workflow was accepted");
    assert!(
        codes(&output).iter().any(|c| c == code),
        "missing finding {code}: {}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn standard_labels_only() {
    let dir = fixture();
    assert_eq!(RUNNERS.len(), 6);
    for runner in RUNNERS {
        put(
            dir.path(),
            ".github/workflows/check.yml",
            &format!(
                "name: Check\non: workflow_dispatch\njobs:\n  check:\n    runs-on: {runner}\n    steps:\n      - run: echo checked\n"
            ),
        );
        let result = audit(dir.path());
        assert!(
            result.status.success(),
            "valid runner rejected: {runner}; {}",
            String::from_utf8_lossy(&result.stderr)
        );
        assert!(codes(&result).is_empty());
    }
    for runner in [
        "self-hosted",
        "macos-26-large",
        "ubuntu-latest",
        "windows-2022",
        "gpu-4-core",
        "private-diagnostic-value",
    ] {
        put(
            dir.path(),
            ".github/workflows/check.yml",
            &format!("jobs:\n  check:\n    runs-on: {runner}\n"),
        );
        rejected(dir.path(), "runner_not_allowed");
        assert!(
            !String::from_utf8_lossy(&audit(dir.path()).stdout)
                .contains("private-diagnostic-value")
        );
    }
}
#[test]
fn runner_group_and_expression_are_rejected() {
    let dir = fixture();
    for selection in [
        "[self-hosted, ubuntu-24.04]",
        "{group: free, labels: ubuntu-24.04}",
        "'${{ inputs.runner }}'",
        "'${{ fromJSON(inputs.runner) }}'",
    ] {
        put(
            dir.path(),
            ".github/workflows/check.yml",
            &format!("jobs:\n  check:\n    runs-on: {selection}\n"),
        );
        rejected(dir.path(), "runner_not_static");
    }
}
#[test]
fn matrix_and_reusable_edges_are_closed() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  fanout:\n    strategy:\n      matrix:\n        runner: [ubuntu-24.04, windows-2025]\n        exclude:\n          - runner: windows-2025\n        include:\n          - runner: macos-26\n    runs-on: '${{ matrix.runner }}'\n  child:\n    uses: ./.github/workflows/child.yml\n",
    );
    put(
        dir.path(),
        ".github/workflows/child.yml",
        "on: workflow_call\njobs:\n  check:\n    runs-on: ubuntu-24.04-arm\n",
    );
    assert!(audit(dir.path()).status.success());
    put(
        dir.path(),
        ".github/workflows/child.yml",
        "jobs:\n  check:\n    runs-on: paid-64-core\n",
    );
    rejected(dir.path(), "runner_not_allowed");
    put(
        dir.path(),
        ".github/workflows/child.yml",
        "jobs:\n  loop:\n    uses: ./.github/workflows/check.yml\n",
    );
    rejected(dir.path(), "reusable_cycle");
}
#[test]
fn include_cannot_hide_paid_runner_and_unknown_matrix_fails() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  fanout:\n    runs-on: '${{ matrix.runner }}'\n    strategy:\n      matrix:\n        include:\n          - runner: ubuntu-24.04\n          - runner: macos-26-xlarge\n",
    );
    rejected(dir.path(), "runner_not_allowed");
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  fanout:\n    runs-on: '${{ matrix.runner }}'\n    strategy:\n      matrix: '${{ fromJSON(needs.plan.outputs.matrix) }}'\n",
    );
    rejected(dir.path(), "matrix_not_static");
}
#[test]
fn remote_reusable_workflow_cannot_hide_runner_cost() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    uses: private-owner/source/.github/workflows/check.yml@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
    );
    rejected(dir.path(), "reusable_not_local");
}
#[test]
fn artifacts_cache_snapshot_and_unknown_actions_are_rejected() {
    let dir = fixture();
    for action in [
        "actions/upload-artifact",
        "actions/download-artifact",
        "actions/cache",
    ] {
        put(
            dir.path(),
            ".github/workflows/check.yml",
            &format!(
                "jobs:\n  check:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: {action}@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            ),
        );
        rejected(dir.path(), "actions_storage_forbidden");
    }
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n    snapshot: snapshot-name\n",
    );
    rejected(dir.path(), "snapshot_forbidden");
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: unreviewed/action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
    );
    rejected(dir.path(), "action_not_reviewed");
}
#[test]
fn implicit_cache_is_rejected() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/setup-node@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n        with:\n          node-version: '24'\n",
    );
    rejected(dir.path(), "implicit_cache_forbidden");
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/setup-node@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n        with:\n          node-version: '24'\n          package-manager-cache: false\n",
    );
    assert!(audit(dir.path()).status.success());
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/setup-go@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
    );
    rejected(dir.path(), "implicit_cache_forbidden");
}
#[test]
fn local_composite_actions_are_audited_transitively() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: ./ci/local\n",
    );
    put(
        dir.path(),
        "ci/local/action.yml",
        "runs:\n  using: composite\n  steps:\n    - uses: actions/upload-artifact@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
    );
    rejected(dir.path(), "actions_storage_forbidden");
    put(
        dir.path(),
        "ci/local/action.yml",
        "runs:\n  using: composite\n  steps:\n    - uses: ./ci/local\n",
    );
    rejected(dir.path(), "action_cycle");
}
#[test]
fn duplicate_yaml_and_aliases_fail_closed() {
    let dir = fixture();
    for source in [
        "jobs:\n  check:\n    runs-on: paid\n    runs-on: ubuntu-24.04\n",
        "x: &shared ubuntu-24.04\njobs:\n  check:\n    runs-on: *shared\n",
    ] {
        put(dir.path(), ".github/workflows/check.yml", source);
        rejected(dir.path(), "workflow_invalid");
    }
}
#[test]
fn relative_paths_work_in_the_actual_workflow_command() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n",
    );
    for path in [".github/workflows", ".github/workflows/check.yml"] {
        let output = Command::new(env!("CARGO_BIN_EXE_omni-release-launcher"))
            .args([
                "audit-runners",
                "--workflows",
                path,
                "--policy",
                "policy.json",
            ])
            .current_dir(dir.path())
            .env_remove("GITHUB_ACTIONS")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(codes(&output).is_empty());
    }
}
#[test]
fn normal_github_negation_is_not_yaml_indirection() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    if: ${{ !cancelled() }}\n    runs-on: ubuntu-24.04\n",
    );
    assert!(audit(dir.path()).status.success());
}
#[test]
fn unicode_aliases_and_yaml_tags_remain_rejected() {
    let dir = fixture();
    for text in [
        "x: &标签 ubuntu-24.04\njobs:\n  check:\n    runs-on: *标签\n",
        "jobs:\n  check:\n    runs-on: !runner ubuntu-24.04\n",
    ] {
        put(dir.path(), ".github/workflows/check.yml", text);
        rejected(dir.path(), "workflow_invalid");
    }
}
#[test]
fn empty_and_oversized_workflows_do_not_pass() {
    let dir = fixture();
    rejected(dir.path(), "workflow_inventory_empty");
    put(dir.path(), ".github/workflows/check.yml", "jobs: {}\n");
    rejected(dir.path(), "workflow_inventory_empty");
    put(
        dir.path(),
        ".github/workflows/check.yml",
        &"a".repeat(1_048_577),
    );
    rejected(dir.path(), "workflow_invalid");
}
#[test]
fn private_origin_is_rejected() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n",
    );
    let output = Command::new(env!("CARGO_BIN_EXE_omni-release-launcher"))
        .args(["audit-runners", "--workflows"])
        .arg(dir.path().join(".github/workflows"))
        .arg("--policy")
        .arg(dir.path().join("policy.json"))
        .env("GITHUB_ACTIONS", "true")
        .env("GITHUB_REPOSITORY", "ql-owo-lp/omniterm")
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(codes(&output).contains(&"execution_origin_invalid".into()));
}
#[test]
fn policy_file_cannot_widen_free_runner_set() {
    let dir = fixture();
    put(
        dir.path(),
        ".github/workflows/check.yml",
        "jobs:\n  check:\n    runs-on: macos-26-large\n",
    );
    let mut policy: Value =
        serde_json::from_slice(&fs::read(dir.path().join("policy.json")).unwrap()).unwrap();
    policy["runners"]
        .as_array_mut()
        .unwrap()
        .push(json!("macos-26-large"));
    put(dir.path(), "policy.json", &policy.to_string());
    rejected(dir.path(), "policy_invalid");
}
#[test]
fn contracts_use_strict_policy_before_downstream_allocation() {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap();
    let path = repository.join(".github/workflows/contracts.yml");
    let document: Value = serde_yaml::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(document["jobs"]["policy"]["runs-on"], "ubuntu-24.04");
    for (name, job) in document["jobs"].as_object().unwrap() {
        if name != "policy" {
            assert_eq!(job["needs"], "policy", "policy must gate {name}");
        }
    }
    let output = Command::new(env!("CARGO_BIN_EXE_omni-release-launcher"))
        .args(["audit-runners", "--workflows"])
        .arg(path)
        .arg("--policy")
        .arg(repository.join("ci/standard-runner-policy.json"))
        .env_remove("GITHUB_ACTIONS")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(codes(&output).is_empty());
}
#[test]
fn standard_runner_upgrade_preserves_required_check_contexts() {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent().unwrap().parent().unwrap();
    let document: Value = serde_yaml::from_slice(
        &fs::read(repository.join(".github/workflows/contracts.yml")).unwrap(),
    ).unwrap();
    let job = &document["jobs"]["contracts"];
    assert_eq!(job["name"], "contracts (${{ matrix.check }})",
        "changing runner labels must not strand the existing required checks");
    assert_eq!(job["runs-on"], "${{ matrix.runner }}");
    let includes = job["strategy"]["matrix"]["include"].as_array().unwrap();
    let expected = [
        ("ubuntu-24.04", "ubuntu-24.04"),
        ("windows-2022", "windows-2025"),
        ("macos-26", "macos-26"),
    ];
    assert_eq!(includes.len(), expected.len());
    for (check, runner) in expected {
        assert_eq!(includes.iter().filter(|row|
            row["check"] == check && row["runner"] == runner).count(), 1);
    }
    assert_eq!(job["needs"], "policy");
    assert!(job["steps"].as_array().unwrap().iter().any(|step|
        step["run"].as_str().is_some_and(|text| text.contains("bash ci/test-native-release.sh"))));
}

#[cfg(unix)]
#[test]
fn symbolic_workflows_and_actions_cannot_escape_review() {
    use std::os::unix::fs::symlink;
    let dir = fixture();
    let other = fixture();
    put(
        other.path(),
        "outside.yml",
        "jobs:\n  check:\n    runs-on: ubuntu-24.04\n",
    );
    symlink(
        other.path().join("outside.yml"),
        dir.path().join(".github/workflows/link.yml"),
    )
    .unwrap();
    rejected(dir.path(), "workflow_invalid");
}
