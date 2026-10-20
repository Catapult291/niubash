//! niubash#451 regression gate: a console-less `niu -c` run must never show
//! a console window — not for niu.exe and not for any external child it
//! spawns. The bug class: a console-subsystem child created by a parent
//! with no console gets a *visible* console auto-allocated by Windows.
//!
//! Two spawn postures are covered:
//! - DETACHED_PROCESS niu (agents/CI/git hooks, the `bash.exe` forwarder):
//!   children take rubash's CREATE_NO_WINDOW path — a *hidden* console is
//!   fine, a visible window fails the gate.
//! - Plain-spawned niu (callers that forward no creation flags): Windows
//!   auto-allocates niu.exe a visible console before main(); the gate only
//!   demands that no such window survives the startup release — a brief
//!   (<poll) flash is the best a CUI binary can do in-process.

#![cfg(windows)]

use std::collections::HashMap;
use std::ffi::c_void;
use std::os::windows::process::CommandExt;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const DETACHED_PROCESS: u32 = 0x0000_0008;
const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
const TH32CS_SNAPPROCESS: u32 = 0x0000_0002;

type Hwnd = *mut c_void;

#[link(name = "user32")]
extern "system" {
    fn EnumWindows(cb: extern "system" fn(Hwnd, isize) -> i32, lparam: isize) -> i32;
    fn GetClassNameW(hwnd: Hwnd, buf: *mut u16, len: i32) -> i32;
    fn IsWindowVisible(hwnd: Hwnd) -> i32;
    fn GetWindowThreadProcessId(hwnd: Hwnd, pid: *mut u32) -> u32;
    fn CreateToolhelp32Snapshot(flags: u32, pid: u32) -> *mut c_void;
    fn Process32FirstW(snap: *mut c_void, pe: *mut ProcessEntry) -> i32;
    fn Process32NextW(snap: *mut c_void, pe: *mut ProcessEntry) -> i32;
    fn CloseHandle(h: *mut c_void) -> i32;
}

#[repr(C)]
struct ProcessEntry {
    size: u32,
    usage: u32,
    pid: u32,
    default_heap: usize,
    module_id: u32,
    threads: u32,
    ppid: u32,
    pri_class_base: i32,
    flags: u32,
    exe_file: [u16; 260],
}

/// PIDs owning at least one visible `ConsoleWindowClass` window.
fn visible_console_window_owners() -> Vec<u32> {
    use std::sync::Mutex;
    static OWNERS: Mutex<Vec<u32>> = Mutex::new(Vec::new());
    extern "system" fn collect(hwnd: Hwnd, _lparam: isize) -> i32 {
        let mut cls = [0u16; 64];
        unsafe {
            if GetClassNameW(hwnd, cls.as_mut_ptr(), cls.len() as i32) == 0 {
                return 1;
            }
            let name =
                String::from_utf16_lossy(&cls[..cls.iter().position(|&c| c == 0).unwrap_or(64)]);
            if name == "ConsoleWindowClass" && IsWindowVisible(hwnd) != 0 {
                let mut pid = 0u32;
                GetWindowThreadProcessId(hwnd, &mut pid);
                OWNERS.lock().unwrap().push(pid);
            }
        }
        1
    }
    OWNERS.lock().unwrap().clear();
    unsafe {
        EnumWindows(collect, 0);
    }
    std::mem::take(&mut *OWNERS.lock().unwrap())
}

/// pid → parent pid for every process in the snapshot.
fn process_tree() -> HashMap<u32, u32> {
    let mut map = HashMap::new();
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap.is_null() || snap == -1isize as *mut c_void {
            return map;
        }
        let mut pe = ProcessEntry {
            size: std::mem::size_of::<ProcessEntry>() as u32,
            usage: 0,
            pid: 0,
            default_heap: 0,
            module_id: 0,
            threads: 0,
            ppid: 0,
            pri_class_base: 0,
            flags: 0,
            exe_file: [0; 260],
        };
        if Process32FirstW(snap, &mut pe) != 0 {
            loop {
                map.insert(pe.pid, pe.ppid);
                if Process32NextW(snap, &mut pe) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snap);
    }
    map
}

/// Is `pid` `root` itself or a descendant of it?
fn in_tree(pid: u32, root: u32, tree: &HashMap<u32, u32>) -> bool {
    let mut cur = pid;
    for _ in 0..64 {
        if cur == root {
            return true;
        }
        match tree.get(&cur) {
            Some(&next) if next != cur && next != 0 => cur = next,
            _ => return false,
        }
    }
    false
}

/// Poll while `child` runs; collect owner pids of visible console windows
/// belonging to `child`'s subtree. `transient_ok` ignores windows that die
/// before the next poll (the auto-allocated niu window released at
/// startup); `transient_ok=false` treats any sighting as a violation.
fn collect_offenders(
    child_pid: u32,
    mut alive: impl FnMut() -> bool,
    transient_ok: bool,
) -> Vec<u32> {
    let deadline = Instant::now() + Duration::from_secs(8);
    let mut persistent: HashMap<u32, Instant> = HashMap::new();
    while alive() && Instant::now() < deadline {
        let owners = visible_console_window_owners();
        if !owners.is_empty() {
            let tree = process_tree();
            for owner in owners {
                if in_tree(owner, child_pid, &tree) {
                    persistent.entry(owner).or_insert_with(Instant::now);
                }
            }
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    let grace = Duration::from_millis(if transient_ok { 500 } else { 0 });
    persistent
        .into_iter()
        .filter(|(_, first_seen)| !transient_ok || first_seen.elapsed() >= grace)
        .map(|(pid, _)| pid)
        .collect()
}

fn spawn_niu(args: &[&str], detached: bool) -> std::process::Child {
    let niu = env!("CARGO_BIN_EXE_niu");
    let mut command = Command::new(niu);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if detached {
        command.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
    }
    command.spawn().expect("spawn niu")
}

#[test]
fn detached_niu_c_spawns_no_visible_console_window() {
    // ~2s of external-command work — long enough for a regression to flash.
    let mut child = spawn_niu(
        &["-c", "C:/Windows/System32/ping.exe -n 3 127.0.0.1 >NUL"],
        true,
    );
    let pid = child.id();
    let offenders = collect_offenders(pid, || child.try_wait().ok().flatten().is_none(), false);
    let status = child.wait().expect("wait niu");
    assert!(status.success(), "niu -c ping exited {status}");
    assert!(
        offenders.is_empty(),
        "visible console windows appeared in the niu tree (owner pids {offenders:?})"
    );
}

#[test]
fn plainly_spawned_niu_c_leaves_no_console_window_behind() {
    // A console-less host makes niu auto-allocate a visible console that the
    // startup release must kill; a host WITH a console shares it (no new
    // window at all). Either way nothing visible may persist.
    let mut child = spawn_niu(
        &["-c", "C:/Windows/System32/ping.exe -n 3 127.0.0.1 >NUL"],
        false,
    );
    let pid = child.id();
    let offenders = collect_offenders(pid, || child.try_wait().ok().flatten().is_none(), true);
    let status = child.wait().expect("wait niu");
    assert!(status.success(), "niu -c ping exited {status}");
    assert!(
        offenders.is_empty(),
        "console windows lingered past the startup release (owner pids {offenders:?})"
    );
}
