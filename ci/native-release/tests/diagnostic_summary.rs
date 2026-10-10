//! Only identifiers already present in public source may be reported. Fixture
//! log strings exercise a parser; they do not stand in for execution evidence.
use std::{fs, path::Path, process::Command};

#[test]
fn public_contract_summary_does_not_echo_private_output_or_unknown_identifiers() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("public.rs");
    let log = temporary.path().join("output.log");
    fs::write(
        &source,
        "Native public target: diagnostic_summary\nfn known_failure() { }\nfn another_public_case() { }\n",
    )
    .unwrap();
    fs::write(&log, "secret raw output\nthread 'x' panicked at /private/path\n     Running tests/diagnostic_summary.rs (/private/path/PRIVATE_CANARY)\ntest module::known_failure ... FAILED\ntest secret_identifier ... FAILED\nerror[E0007]: PRIVATE_CANARY private compiler text\n##[error]untrusted workflow command\ntest module::another_public_case ... PRIVATE_CANARY raw incomplete suffix\n").unwrap();
    let parser = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("native-failure-summary.awk");
    assert!(
        parser.is_file(),
        "public-only failure summarizer is missing"
    );
    let output = Command::new("awk")
        .arg("-f")
        .arg(&parser)
        .arg(&source)
        .arg(&log)
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        "Native contract last Cargo test target: diagnostic_summary\nNative contract last observed test: another_public_case\nNative contract failed test: known_failure\nNative compiler diagnostic: E0007\n"
    );
    fs::write(&log, "     Running tests/PRIVATE_CANARY.rs (/private/PRIVATE_CANARY)\ntest unknown_PRIVATE_CANARY ... private metadata\n").unwrap();
    let unknown = Command::new("awk")
        .arg("-f")
        .arg(parser)
        .arg(source)
        .arg(log)
        .output()
        .unwrap();
    assert!(unknown.status.success());
    assert!(unknown.stdout.is_empty());
}
#[test]
fn process_boundary_summary_accepts_only_closed_enumerations() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("public.rs");
    let log = temporary.path().join("output.log");
    fs::write(&source, "fn boundary() {}\n").unwrap();
    fs::write(&log, "Native boundary result: orphan timeout\nNative boundary result: timeout success\nNative boundary result: orphan /private/value\nNative boundary result: timeout started secret\n").unwrap();
    let parser = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("native-failure-summary.awk");
    let output = Command::new("awk")
        .arg("-f")
        .arg(parser)
        .arg(source)
        .arg(log)
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        "Native boundary result: orphan timeout\nNative boundary result: timeout success\n"
    );
}
#[test]
fn failure_summary_is_bounded_and_has_no_raw_log_fallback() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("public.rs");
    let log = temporary.path().join("output.log");
    fs::write(
        &source,
        format!(
            "Native public target: diagnostic_summary\n{}",
            (0..100)
                .map(|n| format!("fn case_{n}() {{}}\n"))
                .collect::<String>()
        ),
    )
    .unwrap();
    fs::write(
        &log,
        format!(
            "     Running tests/diagnostic_summary.rs (/private/PRIVATE_CANARY)\n{}",
            (0..100)
                .map(|n| format!("test module::case_{n} ... FAILED\n"))
                .collect::<String>()
        ),
    )
    .unwrap();
    let parser = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("native-failure-summary.awk");
    assert!(parser.is_file());
    let output = Command::new("awk")
        .arg("-f")
        .arg(parser)
        .arg(source)
        .arg(log)
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().lines().count(),
        20
    );
}
