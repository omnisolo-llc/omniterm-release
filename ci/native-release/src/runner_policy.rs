//! Fail-closed static cost policy. This audits declared orchestration, not arbitrary
//! shell behavior. Reviewed changes and the independent execution-origin check
//! remain required. No workflow is fetched, executed, or submitted by this module.
use crate::Result;
use serde_json::{Map, Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Read,
    path::{Component, Path, PathBuf},
};

pub const REPOSITORY: &str = "omnisolo-llc/omniterm-release";
pub const RUNNERS: [&str; 6] = [
    "ubuntu-24.04",
    "ubuntu-24.04-arm",
    "windows-2025",
    "windows-11-arm",
    "macos-15-intel",
    "macos-26",
];
const MAX_FILE: u64 = 1_048_576;
const MAX_DOCUMENTS: usize = 128;
const MAX_CELLS: usize = 256;
type Cell = BTreeMap<String, Value>;

fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}
fn simple(value: &Value) -> bool {
    value.is_boolean()
        || value.is_number()
        || value
            .as_str()
            .is_some_and(|s| s.len() <= 256 && !s.contains("${{"))
}
fn disabled(value: Option<&Value>) -> bool {
    value.is_some_and(|v| v == false || v.as_str() == Some("false"))
}
fn absent_or_empty(value: Option<&Value>) -> bool {
    value.is_none_or(|v| v.is_null() || v.as_str() == Some(""))
}
fn policy_valid(policy: &Value) -> bool {
    let Some(map) = policy.as_object() else {
        return false;
    };
    let Some(labels) = map.get("runners").and_then(Value::as_array) else {
        return false;
    };
    map.len() == 4
        && map.get("schema") == Some(&json!(1))
        && map.get("repository").and_then(Value::as_str) == Some(REPOSITORY)
        && map.get("prohibit_actions_storage") == Some(&Value::Bool(true))
        && labels.len() == RUNNERS.len()
        && labels
            .iter()
            .filter_map(Value::as_str)
            .collect::<BTreeSet<_>>()
            == RUNNERS.into_iter().collect()
}
fn regular_bytes(path: &Path, max: u64) -> Result<Vec<u8>> {
    let meta = fs::symlink_metadata(path).map_err(|_| "Policy input unavailable")?;
    if !meta.file_type().is_file() || meta.len() == 0 || meta.len() > max {
        return Err("Policy input invalid");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.nlink() != 1 {
            return Err("Policy input is aliased");
        }
    }
    let file = crate::guards::open_regular(path, max)?;
    let mut bytes = Vec::new();
    file.take(max + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Policy input unreadable")?;
    if bytes.len() as u64 != meta.len() || bytes.len() as u64 > max {
        return Err("Policy input changed");
    }
    Ok(bytes)
}
fn yaml(path: &Path) -> Result<Value> {
    let raw = regular_bytes(path, MAX_FILE)?;
    // Refuse YAML anchors/aliases/tags before deserialization to bound expansion.
    // A literal shell string with this token syntax must be rewritten explicitly.
    for (i, c) in raw.iter().enumerate() {
        if b"&*".contains(c)
            && (i == 0 || raw[i - 1].is_ascii_whitespace() || b":,[{".contains(&raw[i - 1]))
            && raw
                .get(i + 1)
                .is_some_and(|b| b.is_ascii_alphanumeric() || *b == b'_' || !b.is_ascii())
        {
            return Err("Unsupported YAML indirection");
        }
    }
    let parsed: serde_yaml::Value =
        serde_yaml::from_slice(&raw).map_err(|_| "Invalid workflow YAML")?;
    fn bounded(value: &serde_yaml::Value, depth: usize, count: &mut usize) -> bool {
        *count += 1;
        if depth > 48 || *count > 65_536 {
            return false;
        }
        match value {
            serde_yaml::Value::Tagged(_) => false,
            serde_yaml::Value::Mapping(map) => map.iter().all(|(k, v)| {
                k.as_str().is_some_and(|s| s != "<<") && bounded(v, depth + 1, count)
            }),
            serde_yaml::Value::Sequence(values) => {
                values.iter().all(|v| bounded(v, depth + 1, count))
            }
            _ => true,
        }
    }
    if !bounded(&parsed, 0, &mut 0) {
        return Err("Workflow structure exceeds policy");
    }
    serde_json::to_value(parsed).map_err(|_| "Invalid workflow values")
}
fn local_path(root: &Path, value: &str) -> Result<PathBuf> {
    let relative = value.strip_prefix("./").unwrap_or(value);
    let path = Path::new(relative);
    if relative.is_empty()
        || relative.contains(['\\', '%'])
        || path.is_absolute()
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err("Non-local workflow path");
    }
    let mut result = root.to_owned();
    for component in path.components() {
        result.push(component);
        if !fs::symlink_metadata(&result).is_ok_and(|m| !m.file_type().is_symlink()) {
            return Err("Missing or symbolic workflow path");
        }
    }
    Ok(result)
}
fn cell(value: &Value) -> Result<Cell> {
    let map = value.as_object().ok_or("Non-static matrix row")?;
    if map.is_empty() || map.len() > 16 || map.iter().any(|(k, v)| !identifier(k) || !simple(v)) {
        return Err("Non-static matrix row");
    }
    Ok(map.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
}
fn rows(value: Option<&Value>) -> Result<Vec<Cell>> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    let values = value.as_array().ok_or("Non-static matrix rows")?;
    if values.len() > MAX_CELLS {
        return Err("Matrix exceeds bound");
    }
    values.iter().map(cell).collect()
}
fn matrix(job: &Value) -> Result<Vec<Cell>> {
    let Some(strategy) = job.get("strategy") else {
        return Ok(vec![Cell::new()]);
    };
    let Some(value) = strategy.get("matrix") else {
        return Ok(vec![Cell::new()]);
    };
    let map = value.as_object().ok_or("Non-static matrix")?;
    if map.len() > 10 {
        return Err("Matrix exceeds bound");
    }
    let mut result = vec![Cell::new()];
    let mut axes = BTreeSet::new();
    for (name, value) in map {
        if matches!(name.as_str(), "include" | "exclude") {
            continue;
        }
        if !identifier(name) {
            return Err("Invalid matrix axis");
        }
        axes.insert(name.clone());
        let values = value.as_array().ok_or("Non-static matrix axis")?;
        if values.is_empty()
            || result.len().saturating_mul(values.len()) > MAX_CELLS
            || values.iter().any(|v| !simple(v))
        {
            return Err("Matrix exceeds bound");
        }
        result = result
            .into_iter()
            .flat_map(|row| {
                values.iter().map(move |value| {
                    let mut next = row.clone();
                    next.insert(name.clone(), value.clone());
                    next
                })
            })
            .collect();
    }
    if axes.is_empty() {
        result.clear()
    }
    let excluded = rows(map.get("exclude"))?;
    result.retain(|row| {
        !excluded
            .iter()
            .any(|ex| ex.iter().all(|(k, v)| row.get(k) == Some(v)))
    });
    let mut additions = Vec::new();
    for include in rows(map.get("include"))? {
        let mut matched = false;
        for row in &mut result {
            if include
                .iter()
                .all(|(k, v)| !axes.contains(k) || row.get(k) == Some(v))
            {
                row.extend(include.clone());
                matched = true
            }
        }
        if !matched {
            additions.push(include)
        }
    }
    result.extend(additions);
    if result.is_empty() || result.len() > MAX_CELLS {
        return Err("Empty or oversized matrix");
    }
    Ok(result)
}

#[derive(Default)]
struct Audit {
    findings: BTreeSet<(String, String, &'static str)>,
    seen: BTreeSet<PathBuf>,
    active: BTreeSet<PathBuf>,
    action_active: BTreeSet<PathBuf>,
    documents: usize,
}
impl Audit {
    fn finding(&mut self, workflow: &str, job: &str, code: &'static str) {
        self.findings.insert((
            if identifier(workflow) {
                workflow
            } else {
                "invalid"
            }
            .into(),
            if identifier(job) { job } else { "invalid" }.into(),
            code,
        ));
    }
    fn action(&mut self, root: &Path, workflow: &str, job: &str, step: &Value) {
        let Some(uses) = step.get("uses") else { return };
        let Some(uses) = uses.as_str() else {
            self.finding(workflow, job, "action_not_reviewed");
            return;
        };
        if uses.starts_with("./") {
            let Ok(dir) = local_path(root, uses) else {
                self.finding(workflow, job, "action_not_local");
                return;
            };
            let path = dir.join(if dir.join("action.yml").exists() {
                "action.yml"
            } else {
                "action.yaml"
            });
            if !self.action_active.insert(path.clone()) {
                self.finding(workflow, job, "action_cycle");
                return;
            }
            self.documents += 1;
            if self.documents > MAX_DOCUMENTS {
                self.finding(workflow, job, "document_limit");
                self.action_active.remove(&path);
                return;
            }
            match yaml(&path) {
                Ok(doc) if doc["runs"]["using"] == "composite" => {
                    self.steps(root, workflow, job, doc["runs"].get("steps"))
                }
                _ => self.finding(workflow, job, "action_not_reviewed"),
            }
            self.action_active.remove(&path);
            return;
        }
        let Some((name, pin)) = uses.split_once('@') else {
            self.finding(workflow, job, "action_not_reviewed");
            return;
        };
        if matches!(
            name,
            "actions/cache"
                | "actions/cache/restore"
                | "actions/cache/save"
                | "actions/upload-artifact"
                | "actions/download-artifact"
        ) {
            self.finding(workflow, job, "actions_storage_forbidden");
            return;
        }
        if !crate::input::valid_sha(pin)
            || !matches!(
                name,
                "actions/checkout"
                    | "actions/setup-node"
                    | "actions/setup-python"
                    | "actions/setup-java"
                    | "actions/setup-go"
                    | "actions/setup-dotnet"
            )
        {
            self.finding(workflow, job, "action_not_reviewed");
            return;
        }
        let options = step.get("with");
        let get = |key: &str| options.and_then(|v| v.get(key));
        match name {
            "actions/setup-node" => {
                if !disabled(get("package-manager-cache")) {
                    self.finding(workflow, job, "implicit_cache_forbidden")
                }
                if !absent_or_empty(get("cache")) {
                    self.finding(workflow, job, "actions_storage_forbidden")
                }
            }
            "actions/setup-go" | "actions/setup-dotnet" if !disabled(get("cache")) => {
                self.finding(workflow, job, "implicit_cache_forbidden")
            }
            "actions/setup-java" | "actions/setup-python" if !absent_or_empty(get("cache")) => {
                self.finding(workflow, job, "actions_storage_forbidden")
            }
            "actions/checkout" if get("lfs").is_some() && !disabled(get("lfs")) => {
                self.finding(workflow, job, "lfs_forbidden")
            }
            _ => {}
        }
    }
    fn steps(&mut self, root: &Path, workflow: &str, job: &str, steps: Option<&Value>) {
        let Some(steps) = steps else { return };
        let Some(steps) = steps.as_array() else {
            self.finding(workflow, job, "workflow_invalid");
            return;
        };
        if steps.len() > 256 {
            self.finding(workflow, job, "document_limit");
            return;
        }
        for step in steps {
            if !step.is_object() {
                self.finding(workflow, job, "workflow_invalid");
                continue;
            }
            if step.get("snapshot").is_some() {
                self.finding(workflow, job, "snapshot_forbidden")
            }
            self.action(root, workflow, job, step);
        }
    }
    fn workflow(&mut self, root: &Path, path: &Path) {
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("invalid");
        if self.active.contains(path) {
            self.finding(name, "workflow", "reusable_cycle");
            return;
        }
        if !self.seen.insert(path.to_owned()) {
            return;
        }
        self.documents += 1;
        if self.documents > MAX_DOCUMENTS {
            self.finding(name, "workflow", "document_limit");
            return;
        }
        self.active.insert(path.to_owned());
        match yaml(path) {
            Ok(document) => {
                if document.get("snapshot").is_some() {
                    self.finding(name, "workflow", "snapshot_forbidden")
                }
                if let Some(jobs) = document
                    .get("jobs")
                    .and_then(Value::as_object)
                    .filter(|m| !m.is_empty() && m.len() <= 256)
                {
                    self.jobs(root, name, jobs);
                } else {
                    self.finding(name, "workflow", "workflow_inventory_empty")
                }
            }
            Err(_) => self.finding(name, "workflow", "workflow_invalid"),
        }
        self.active.remove(path);
    }
    fn jobs(&mut self, root: &Path, name: &str, jobs: &Map<String, Value>) {
        for (id, job) in jobs {
            if !identifier(id) || !job.is_object() {
                self.finding(name, id, "workflow_invalid");
                continue;
            }
            if job.get("snapshot").is_some() {
                self.finding(name, id, "snapshot_forbidden")
            }
            if let Some(uses) = job.get("uses") {
                if job.get("runs-on").is_some() {
                    self.finding(name, id, "workflow_invalid")
                }
                if let Some(local) = uses
                    .as_str()
                    .filter(|s| s.starts_with("./.github/workflows/"))
                {
                    match local_path(root, local) {
                        Ok(path) => self.workflow(root, &path),
                        Err(_) => self.finding(name, id, "reusable_not_local"),
                    }
                } else {
                    self.finding(name, id, "reusable_not_local")
                }
                continue;
            }
            let rows = match matrix(job) {
                Ok(rows) => rows,
                Err(_) => {
                    self.finding(name, id, "matrix_not_static");
                    continue;
                }
            };
            let Some(selection) = job.get("runs-on").and_then(Value::as_str) else {
                self.finding(name, id, "runner_not_static");
                continue;
            };
            for row in rows {
                let label = if let Some(key) = selection
                    .strip_prefix("${{ matrix.")
                    .and_then(|s| s.strip_suffix(" }}"))
                {
                    if !identifier(key) {
                        None
                    } else {
                        row.get(key).and_then(Value::as_str)
                    }
                } else if selection.contains("${{") {
                    None
                } else {
                    Some(selection)
                };
                match label {
                    None => self.finding(name, id, "runner_not_static"),
                    Some(label) if !RUNNERS.contains(&label) => {
                        self.finding(name, id, "runner_not_allowed")
                    }
                    _ => {}
                }
            }
            self.steps(root, name, id, job.get("steps"));
        }
    }
    fn report(self) -> Value {
        json!({"schema":1,"repository":REPOSITORY,"runners":RUNNERS,"findings":self.findings.into_iter().map(|(workflow,job,code)|json!({"workflow":workflow,"job":job,"code":code})).collect::<Vec<_>>()})
    }
}

/// root is .github/workflows or a specific file within it. Local reusable
/// workflows and composite actions are resolved only beneath its repository.
pub fn audit_workflows(root: &Path, policy: &Value) -> Result<Value> {
    let mut audit = Audit::default();
    if !policy_valid(policy) {
        audit.finding("policy", "policy", "policy_invalid");
        return Ok(audit.report());
    }
    let meta = fs::symlink_metadata(root).map_err(|_| "Workflow root unavailable")?;
    if meta.file_type().is_symlink() {
        audit.finding("workflow", "workflow", "workflow_invalid");
        return Ok(audit.report());
    }
    let directory = if meta.is_dir() {
        root
    } else {
        root.parent().ok_or("Workflow parent missing")?
    };
    if directory.file_name().and_then(|s| s.to_str()) != Some("workflows")
        || directory
            .parent()
            .and_then(Path::file_name)
            .and_then(|s| s.to_str())
            != Some(".github")
    {
        return Err("Expected repository workflow directory");
    }
    let repo = directory
        .parent()
        .and_then(Path::parent)
        .ok_or("Workflow repository missing")?;
    let repo = if repo.as_os_str().is_empty() {
        Path::new(".")
    } else {
        repo
    };
    let repo = fs::canonicalize(repo).map_err(|_| "Workflow repository unavailable")?;
    let dir = local_path(&repo, ".github/workflows")?;
    let mut paths = if meta.is_dir() {
        fs::read_dir(&dir)
            .map_err(|_| "Workflow enumeration unavailable")?
            .map(|entry| {
                entry
                    .map(|v| v.path())
                    .map_err(|_| "Workflow enumeration failed")
            })
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .filter(|p| matches!(p.extension().and_then(|s| s.to_str()), Some("yml" | "yaml")))
            .collect::<Vec<_>>()
    } else {
        vec![dir.join(root.file_name().ok_or("Workflow filename missing")?)]
    };
    paths.sort();
    if paths.is_empty() {
        audit.finding("workflow", "workflow", "workflow_inventory_empty")
    }
    if paths.len() > MAX_DOCUMENTS {
        audit.finding("workflow", "workflow", "document_limit");
        return Ok(audit.report());
    }
    for path in paths {
        audit.workflow(&repo, &path)
    }
    Ok(audit.report())
}
fn valid_origin() -> bool {
    if std::env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return true;
    }
    if std::env::var("GITHUB_REPOSITORY").as_deref() != Ok(REPOSITORY) {
        return false;
    }
    let Ok(path) = std::env::var("GITHUB_EVENT_PATH") else {
        return false;
    };
    let Ok(bytes) = regular_bytes(Path::new(&path), MAX_FILE) else {
        return false;
    };
    let Ok(event) = crate::json::parse(&bytes) else {
        return false;
    };
    event["repository"]["full_name"] == REPOSITORY && event["repository"]["private"] == false
}
/// CLI returns a structured report even when invalid input is denied. --report
/// is for migration inventory only and is never used by a gating job.
pub fn cli(args: &[String]) -> Result<()> {
    let mut workflow = None;
    let mut policy = None;
    let mut report_only = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--workflows" if workflow.is_none() => {
                i += 1;
                workflow = args.get(i).map(PathBuf::from)
            }
            "--policy" if policy.is_none() => {
                i += 1;
                policy = args.get(i).map(PathBuf::from)
            }
            "--report" if !report_only => report_only = true,
            _ => return Err("Invalid runner policy arguments"),
        }
        i += 1;
    }
    let workflow = workflow.ok_or("Workflow path is required")?;
    let policy = policy.ok_or("Runner policy is required")?;
    let result = if !valid_origin() {
        let mut audit = Audit::default();
        audit.finding("workflow", "workflow", "execution_origin_invalid");
        audit.report()
    } else {
        match regular_bytes(&policy, 16_384).and_then(|b| crate::json::parse(&b)) {
            Ok(value) => audit_workflows(&workflow, &value)?,
            Err(_) => {
                let mut audit = Audit::default();
                audit.finding("policy", "policy", "policy_invalid");
                audit.report()
            }
        }
    };
    let passed = result["findings"].as_array().is_some_and(Vec::is_empty);
    println!("{}", result);
    if passed || report_only {
        Ok(())
    } else {
        Err("Standard runner policy rejected this graph")
    }
}
