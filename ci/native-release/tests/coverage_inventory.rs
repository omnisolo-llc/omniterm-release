use std::{collections::BTreeSet, fs, path::Path};
#[test]
fn every_legacy_test_maps_to_existing_native_assertions() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let ci = manifest.parent().unwrap();
    let document = fs::read_to_string(manifest.join("MIGRATION.md")).unwrap();
    let rows: Vec<_> = document
        .lines()
        .filter(|l| l.starts_with("| `test_"))
        .collect();
    assert_eq!(rows.len(), 97);
    let mut mapped = BTreeSet::new();
    for row in rows {
        let columns: Vec<_> = row.split('|').collect();
        let legacy = columns[1].trim().trim_matches('`');
        assert!(mapped.insert(legacy.to_owned()), "Duplicate {legacy}");
        let native = columns[2].trim().trim_matches('`');
        for name in native.split("; ") {
            let (file, function) = name.split_once("::").unwrap();
            let code =
                fs::read_to_string(manifest.join("tests").join(format!("{file}.rs"))).unwrap();
            assert!(
                code.contains(&format!("fn {function}(")),
                "Missing native coverage: {legacy} => {name}"
            );
        }
        assert!(!columns[3].trim().is_empty());
    }
    let mut present = 0;
    for entry in fs::read_dir(ci).unwrap() {
        let entry = entry.unwrap();
        let name = entry.file_name().into_string().unwrap();
        if !name.starts_with("test_") || !name.ends_with(".py") {
            continue;
        }
        let code = fs::read_to_string(entry.path()).unwrap();
        for line in code.lines() {
            if let Some(name_part) = line.trim().strip_prefix("def test_") {
                let name_part = name_part.split('(').next().unwrap();
                let key = format!("{name}::test_{name_part}");
                assert!(mapped.contains(&key), "Unmapped legacy test: {key}");
                present += 1;
            }
        }
    }
    // Legacy references may be deleted only by the integration owner after parity review.
    if present > 0 {
        assert_eq!(
            present,
            mapped.len(),
            "Legacy inventory changed; reconcile coverage explicitly"
        );
    }
}
