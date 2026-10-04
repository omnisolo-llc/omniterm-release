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
        "fn known_failure() { }\nfn another_public_case() { }\n",
    )
    .unwrap();
    fs::write(&log, "secret raw output\nthread 'x' panicked at /private/path\ntest module::known_failure ... FAILED\ntest secret_identifier ... FAILED\nerror[E0007]: private compiler text\n##[error]untrusted workflow command\n").unwrap();
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
        .arg(parser)
        .arg(source)
        .arg(log)
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        "Native contract failed test: known_failure\nNative compiler diagnostic: E0007\n"
    );
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
        (0..100)
            .map(|n| format!("fn case_{n}() {{}}\n"))
            .collect::<String>(),
    )
    .unwrap();
    fs::write(
        &log,
        (0..100)
            .map(|n| format!("test module::case_{n} ... FAILED\n"))
            .collect::<String>(),
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
