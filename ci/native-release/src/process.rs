use crate::{Environment, Result};
use std::{
    io::{Read, Write},
    path::Path,
    process::{Command, Stdio},
    sync::{
        Arc, OnceLock,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};
const MAX_OUTPUT: usize = 4 * 1024 * 1024;
static TERMINATED: OnceLock<Arc<AtomicBool>> = OnceLock::new();
pub fn install_termination_handler() -> Result<()> {
    let flag = TERMINATED.get_or_init(|| Arc::new(AtomicBool::new(false)));
    for signal in [signal_hook::consts::SIGTERM, signal_hook::consts::SIGINT] {
        signal_hook::flag::register(signal, flag.clone())
            .map_err(|_| "Termination cleanup unavailable")?;
    }
    Ok(())
}
fn collect(
    mut pipe: impl Read + Send + 'static,
    failed: Arc<AtomicBool>,
) -> mpsc::Receiver<std::io::Result<Vec<u8>>> {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut data = Vec::new();
        let mut buffer = [0u8; 8192];
        let mut overflow = false;
        let result = (|| {
            loop {
                let n = pipe.read(&mut buffer)?;
                if n == 0 {
                    break;
                }
                if data.len() + n <= MAX_OUTPUT {
                    data.extend_from_slice(&buffer[..n]);
                } else {
                    overflow = true;
                    failed.store(true, Ordering::Release);
                    break;
                }
            }
            if overflow {
                Err(std::io::Error::other("Output bound exceeded"))
            } else {
                Ok(data)
            }
        })();
        let _ = tx.send(result);
    });
    rx
}
fn stop_group(child: &mut std::process::Child) {
    #[cfg(unix)]
    {
        if let Ok(pid) = i32::try_from(child.id()) {
            unsafe {
                libc::kill(-pid, libc::SIGKILL);
            }
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}
struct ChildGuard {
    child: std::process::Child,
    #[cfg(windows)]
    job: WindowsJob,
}
impl Drop for ChildGuard {
    fn drop(&mut self) {
        #[cfg(windows)]
        self.job.stop();
        stop_group(&mut self.child);
    }
}
#[cfg(windows)]
struct WindowsJob(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl WindowsJob {
    fn attach(child: &mut std::process::Child) -> Result<Self> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::*;
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err("Process-tree cleanup unavailable");
        }
        let job = Self(handle);
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of_val(&limits) as u32,
            )
        } == 0
            || unsafe { AssignProcessToJobObject(handle, child.as_raw_handle()) } == 0
        {
            return Err("Process-tree cleanup unavailable");
        }
        #[link(name = "ntdll")]
        unsafe extern "system" {
            fn NtResumeProcess(process: windows_sys::Win32::Foundation::HANDLE) -> i32;
        }
        if unsafe { NtResumeProcess(child.as_raw_handle()) } < 0 {
            return Err("Suspended process cannot resume");
        }
        Ok(job)
    }
    fn stop(&self) {
        unsafe {
            windows_sys::Win32::System::JobObjects::TerminateJobObject(self.0, 1);
        }
    }
}
#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        self.stop();
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}
pub fn run(
    program: &Path,
    args: &[String],
    cwd: &Path,
    env: &Environment,
    timeout: Duration,
    mut log: Option<&mut std::fs::File>,
) -> Result<Vec<u8>> {
    let name = program
        .file_name()
        .and_then(|p| p.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if name.starts_with("python")
        || name.starts_with("pip")
        || name.starts_with("pypy")
        || ["py", "py.exe"].contains(&name.as_str())
        || program.extension().is_some_and(|e| e == "py")
    {
        return Err("Interpreters are forbidden in native release tasks");
    }
    let mut command = Command::new(program);
    command
        .args(args)
        .current_dir(cwd)
        .env_clear()
        .envs(env)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(windows_sys::Win32::System::Threading::CREATE_SUSPENDED);
    }
    let child = command
        .spawn()
        .map_err(|_| "Required build tool could not start")?;
    #[cfg(windows)]
    let mut child = child;
    #[cfg(windows)]
    let job = match WindowsJob::attach(&mut child) {
        Ok(job) => job,
        Err(error) => {
            stop_group(&mut child);
            return Err(error);
        }
    };
    let mut guard = ChildGuard {
        child,
        #[cfg(windows)]
        job,
    };
    let failed = Arc::new(AtomicBool::new(false));
    let stdout = collect(
        guard.child.stdout.take().ok_or("Missing child stdout")?,
        failed.clone(),
    );
    let stderr = collect(
        guard.child.stderr.take().ok_or("Missing child stderr")?,
        failed.clone(),
    );
    let start = Instant::now();
    let status = loop {
        if TERMINATED
            .get()
            .is_some_and(|flag| flag.load(Ordering::Acquire))
        {
            return Err("Native release task interrupted");
        }
        if failed.load(Ordering::Acquire) {
            return Err("Build output exceeded its bound");
        }
        match guard.child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {}
            Err(_) => {
                return Err("Could not monitor build command");
            }
        }
        if start.elapsed() > timeout {
            return Err("Build command timed out");
        }
        thread::sleep(Duration::from_millis(25));
    };
    let out = stdout.recv_timeout(Duration::from_secs(2));
    let err = stderr.recv_timeout(Duration::from_secs(2));
    if out.is_err() || err.is_err() {
        return Err("Build output stream did not close");
    }
    let out = out
        .map_err(|_| "Build output unavailable")?
        .map_err(|_| "Build output exceeded its bound")?;
    let err = err
        .map_err(|_| "Build errors unavailable")?
        .map_err(|_| "Build output exceeded its bound")?;
    if let Some(log) = log.as_mut() {
        log.write_all(&out)
            .and_then(|_| log.write_all(&err))
            .map_err(|_| "Private build log write failed")?;
    }
    if !status.success() {
        return Err("Build command failed; inspect private diagnostics");
    }
    Ok(out)
}
