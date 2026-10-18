//! Forensic probe for the Windows console input layer (niubash#341).
//!
//! Ignored by default — run explicitly with
//! `cargo test --test keyevent_record_probe -- --ignored --nocapture`.
//!
//! What it establishes, by dumping the raw `INPUT_RECORD` stream a child
//! process reads through `ReadConsoleInputW` under a ConPTY:
//!
//! * a fed `\r` byte synthesizes exactly `KEY_EVENT` records with
//!   `wVirtualKeyCode=VK_RETURN`, `UnicodeChar=0x0D`, no control-key state
//!   — press and release pairs, no LF anywhere. This is the record shape a
//!   real Windows Terminal paste delivers (WT synthesizes one key event per
//!   pasted character, every line break a lone bare CR, an empty line two
//!   consecutive CRs), so the bare-CR feeds of the #341 regression tests
//!   are record-faithful.
//! * a fed `\r\n` byte pair synthesizes the ConPTY text-feed shape #252
//!   modeled: the bare CR **plus** a `VK_CONTROL` press, an
//!   `Enter + CONTROL` record (the LF partner surfacing as Ctrl+J), and a
//!   `VK_CONTROL` release. crossterm maps `VK_CONTROL` to no event at all
//!   (parse.rs returns `None` for `VK_SHIFT | VK_CONTROL | VK_MENU`), which
//!   is why the structural probe finds the LF partner on CRLF feeds — and
//!   never on a real WT paste.
//! * win32-input-mode VT sequences (`CSI Vk;Sc;Uc;Ks;Cs;Rc _`, the encoding
//!   WT uses for physical keyboards when `?9001h` is negotiated) are NOT
//!   decoded by this host's conhost from the input pipe — writing them
//!   synthesizes no records, so a test channel cannot play "WT keyboard"
//!   that way and falls back to the byte feeds above.
//!
//! Method note: conhost forwards the client's initial cursor-position
//! request as `ESC[6n` and blocks the client until the terminal answers —
//! the probe harness replies `\x1b[1;1R`, exactly like the interactive
//! test driver's `TerminalResponder`.

#![cfg(windows)]
#![allow(dead_code)]

use std::io::{Read, Write};
use std::process::Command;
use std::time::Duration;

use portable_pty::{CommandBuilder, PtySize};

/// A self-contained probe child: raw `ReadConsoleInputW` dump with
/// microsecond timestamps, one `REC ...` line per record. Built with
/// `rustc` at test time (no external crates), so the forensic tool stays
/// dependency-free.
const PROBE_SRC: &str = r#"
use std::io::Write;
use std::time::Instant;

const ENABLE_PROCESSED_INPUT: u32 = 0x0001;
const ENABLE_LINE_INPUT: u32 = 0x0002;
const ENABLE_ECHO_INPUT: u32 = 0x0004;
const ENABLE_WINDOW_INPUT: u32 = 0x0008;
const ENABLE_VIRTUAL_TERMINAL_INPUT: u32 = 0x0200;

#[link(name = "kernel32")]
extern "system" {
    fn GetStdHandle(n: u32) -> isize;
    fn GetConsoleMode(h: isize, mode: *mut u32) -> i32;
    fn SetConsoleMode(h: isize, mode: u32) -> i32;
    fn ReadConsoleInputW(h: isize, records: *mut InputRecord, len: u32, read: *mut u32) -> i32;
    fn GetNumberOfConsoleInputEvents(h: isize, n: *mut u32) -> i32;
    fn WaitForSingleObject(h: isize, ms: u32) -> u32;
}

#[repr(C)]
#[derive(Clone, Copy)]
union CharU {
    unicode_char: u16,
    ascii_char: u8,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct KeyEventRecord {
    down: i32,
    repeat: u16,
    vk: u16,
    scan: u16,
    u: CharU,
    state: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
union EventU {
    key: KeyEventRecord,
    pad: [u8; 16],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct InputRecord {
    kind: u16,
    pad: u16,
    event: EventU,
}

const KEY_EVENT: u16 = 0x0001;
const MOUSE_EVENT: u16 = 0x0002;
const WINDOW_BUFFER_SIZE_EVENT: u16 = 0x0004;
const MENU_EVENT: u16 = 0x0008;
const FOCUS_EVENT: u16 = 0x0010;

fn main() {
    let start = Instant::now();
    let input = unsafe { GetStdHandle((-10i32) as u32) } as isize; // STD_INPUT_HANDLE
    let mut mode: u32 = 0;
    unsafe { GetConsoleMode(input, &mut mode) };
    println!("PROBE mode={mode:#010x}");
    unsafe {
        SetConsoleMode(
            input,
            (mode | ENABLE_WINDOW_INPUT | ENABLE_VIRTUAL_TERMINAL_INPUT)
                & !ENABLE_PROCESSED_INPUT
                & !ENABLE_LINE_INPUT
                & !ENABLE_ECHO_INPUT,
        )
    };
    println!("PROBE ready");
    let _ = std::io::stdout().flush();
    loop {
        let mut n: u32 = 0;
        unsafe { GetNumberOfConsoleInputEvents(input, &mut n) };
        if n == 0 {
            unsafe { WaitForSingleObject(input, 5) };
            if start.elapsed().as_millis() > 10000 {
                println!("PROBE timeout");
                let _ = std::io::stdout().flush();
                return;
            }
            continue;
        }
        let mut records: Vec<InputRecord> = Vec::with_capacity(n as usize);
        unsafe { records.set_len(n as usize) };
        let mut read: u32 = 0;
        let ok = unsafe { ReadConsoleInputW(input, records.as_mut_ptr(), n, &mut read) };
        if ok == 0 {
            println!("PROBE readerror");
            return;
        }
        for r in &records[..read as usize] {
            let t = start.elapsed().as_micros();
            match r.kind {
                KEY_EVENT => unsafe {
                    let k = r.event.key;
                    println!(
                        "REC t={t}us KEY down={} vk={} uc={:#06x} state={:#010x} repeat={}",
                        k.down, k.vk, k.u.unicode_char, k.state, k.repeat
                    );
                },
                MOUSE_EVENT => println!("REC t={t}us MOUSE"),
                WINDOW_BUFFER_SIZE_EVENT => println!("REC t={t}us WINDOW_SIZE"),
                MENU_EVENT => println!("REC t={t}us MENU"),
                FOCUS_EVENT => println!("REC t={t}us FOCUS"),
                other => println!("REC t={t}us KIND={other}"),
            }
            let _ = std::io::stdout().flush();
        }
    }
}
"#;

fn build_probe(dir: &std::path::Path, case: &str) -> std::path::PathBuf {
    // A per-case exe: the ignored cases may run concurrently (plain
    // `cargo test -- --ignored`), and link.exe cannot overwrite an exe
    // another test is still holding (LNK1104).
    let src = dir.join(format!("keyevent_record_probe_child_{case}.rs"));
    std::fs::write(&src, PROBE_SRC).unwrap();
    let exe = dir.join(format!("keyevent_record_probe_child_{case}.exe"));
    let status = Command::new("rustc")
        .args(["-O", "-o"])
        .arg(&exe)
        .arg(&src)
        .status()
        .expect("rustc");
    assert!(status.success(), "probe child build failed");
    exe
}

/// Run the probe under a fresh ConPTY, feed `feed` in one write (the
/// terminal side of the pipe), and return everything the probe printed.
fn run_probe_case(probe: &std::path::Path, feed: &[u8]) -> String {
    let pty_system = portable_pty::native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows: 30,
            cols: 120,
            pixel_width: 0,
            pixel_height: 0,
        })
        .expect("openpty");
    let cmd = CommandBuilder::new(probe);
    let mut child = pair.slave.spawn_command(cmd).expect("spawn probe");
    let mut reader = pair.master.try_clone_reader().expect("reader");
    let (tx, rx) = std::sync::mpsc::channel::<Vec<u8>>();
    std::thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if tx.send(buf[..n].to_vec()).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    let mut writer = pair.master.take_writer().expect("writer");
    let deadline = std::time::Instant::now() + Duration::from_millis(4000);
    let mut out = Vec::new();
    let mut answered = false;
    let mut fed = false;
    while std::time::Instant::now() < deadline {
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(chunk) => {
                out.extend_from_slice(&chunk);
                // Answer conhost's cursor-position query or the client
                // blocks before printing anything.
                if !answered && out.windows(4).any(|w| w == b"\x1b[6n") {
                    let _ = writer.write_all(b"\x1b[1;1R");
                    let _ = writer.flush();
                    answered = true;
                }
                if answered && !fed {
                    let _ = writer.write_all(feed);
                    let _ = writer.flush();
                    fed = true;
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(_) => break,
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    // Scratch-channel lesson: dropping the MasterPty can wedge on some
    // hosts; this probe is a manually-run forensic tool, so leak it.
    std::mem::forget(writer);
    std::mem::forget(pair);
    String::from_utf8_lossy(&out).into_owned()
}

fn key_events(out: &str) -> Vec<&str> {
    out.lines()
        .filter(|line| line.starts_with("REC ") && line.contains("KEY"))
        .collect()
}

/// The niubash#341 channel claim, as an executable check: a fed `\r` byte
/// reaches the client as bare-CR KEY_EVENT records only — `vk=0x0d`,
/// `uc=0x000d`, no control-key state, no LF (`uc=0x000a`, `Enter+CONTROL`)
/// anywhere. The `\r\r` adjacency models the WT paste's empty line.
#[test]
#[ignore = "forensic probe for #341; spawns rustc + a ConPTY child; run with -- --ignored --nocapture"]
fn fed_cr_bytes_synthesize_bare_cr_key_event_records_only() {
    let dir = std::env::temp_dir().join("niu341-record-probe");
    std::fs::create_dir_all(&dir).unwrap();
    let probe = build_probe(&dir, "cr");
    let out = run_probe_case(&probe, b"ab\r\rcd\r");
    let events = key_events(&out);
    assert!(
        !events.is_empty(),
        "no KEY_EVENT records captured; probe output:\n{out}"
    );
    for line in &events {
        let down = line.contains("down=1");
        if !down {
            continue; // releases mirror presses; only presses assert shape
        }
        let is_enter = line.contains("vk=13") && line.contains("uc=0x000d");
        let is_text = !line.contains("vk=13");
        assert!(
            is_enter || is_text,
            "unexpected record shape: {line}\nfull dump:\n{out}"
        );
        assert!(
            !line.contains("uc=0x000a"),
            "an LF record surfaced on a bare-CR feed — the WT KEY_EVENT shape claim is broken\nfull dump:\n{out}"
        );
        assert!(
            line.contains("state=0x00000000"),
            "a control-key state rode a record on a bare-CR feed: {line}\nfull dump:\n{out}"
        );
    }
    let enters = events.iter().filter(|l| l.contains("vk=13")).count();
    assert!(
        enters >= 6,
        "expected press+release pairs for 4 CRs ('ab\\r\\rcd\\r'), got {enters} Enter records:\n{out}"
    );
}

/// The contrast case #252 modeled: a fed `\r\n` pair synthesizes the CR
/// **plus** the VK_CONTROL sandwich and the `Enter + CONTROL` LF partner —
/// the shape the structural probe was built for, and the reason CRLF-shaped
/// regressions could never see #341.
#[test]
#[ignore = "forensic probe for #341; spawns rustc + a ConPTY child; run with -- --ignored --nocapture"]
fn fed_crlf_bytes_synthesize_the_vk_control_sandwich_and_lf_partner() {
    let dir = std::env::temp_dir().join("niu341-record-probe");
    std::fs::create_dir_all(&dir).unwrap();
    let probe = build_probe(&dir, "crlf");
    let out = run_probe_case(&probe, b"a\r\n");
    let events = key_events(&out);
    assert!(
        events
            .iter()
            .any(|l| l.contains("down=1") && l.contains("uc=0x000a")),
        "no LF partner record (uc=0x000a) on a CRLF feed; dump:\n{out}"
    );
    assert!(
        events.iter().any(|l| l.contains("state=0x00000008")),
        "no LEFT_CTRL-pressed record (the VK_CONTROL sandwich) on a CRLF feed; dump:\n{out}"
    );
}

/// Win32-input-mode VT sequences (what WT sends for physical keyboards
/// under `?9001h`) are not decoded by this host's conhost from the input
/// pipe: writing them synthesizes no records at all. Documents why the
/// #341 channel uses byte feeds instead.
#[test]
#[ignore = "forensic probe for #341; spawns rustc + a ConPTY child; run with -- --ignored --nocapture"]
fn win32_input_mode_vt_sequences_are_not_decoded_from_the_input_pipe() {
    let dir = std::env::temp_dir().join("niu341-record-probe");
    std::fs::create_dir_all(&dir).unwrap();
    let probe = build_probe(&dir, "win32input");
    // 'a' press+release in win32-input encoding, with the 9001 DECSET first.
    let feed = b"\x1b[?9001h\x1b[65;30;97;0;1;1_\x1b[65;30;97;0;0;1_";
    let out = run_probe_case(&probe, feed);
    let events = key_events(&out);
    assert!(
        events.is_empty(),
        "this host's conhost decoded win32-input-mode sequences (channel could use them); records:\n{out}"
    );
}
