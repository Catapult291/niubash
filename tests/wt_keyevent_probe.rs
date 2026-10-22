//! SCRATCH (issue #341 investigation, not for merge): drive a real niu
//! binary under portable-pty ConPTY with the owner script in the WT
//! KEY_EVENT shape (bare CRs), capture the transcript.

#![cfg(windows)]

use std::io::{Read, Write};
use std::time::Duration;

use portable_pty::{ChildKiller, CommandBuilder, PtySize};

const SCRIPT_PATH: &str = "C:/Users/cmx/AppData/Local/Temp/issue341-owner-script-ci.sh";

#[test]
#[ignore = "scratch driver for #341; run with -- --ignored"]
fn drive_niu_with_wt_shape_paste() {
    let mode = std::env::var("NIU341_MODE").unwrap_or_else(|_| "paste".into());
    let niu =
        std::env::var("NIU_BIN").unwrap_or_else(|_| "D:/repo/niubash/target/debug/niu.exe".into());
    let payload = match mode.as_str() {
        // minimal typed repro for the engine panic (no paste at all)
        "typed-declare" => "declare -A capital=(\r  [中国]=\"北京\"\r)\r".to_string(),
        "typed-declare-oneline" => {
            "declare -A capital\rcapital[中国]=北京\rcapital[日本]=东京\rfor c in \"${!capital[@]}\"; do echo \"  $c -> ${capital[$c]}\"; done\r".to_string()
        }
        "typed-declare-assoc" => {
            "declare -A capital=([中国]=\"北京\" [日本]=\"东京\")\rfor c in \"${!capital[@]}\"; do echo \"  $c\"; done\r".to_string()
        }
        _ => {
            let script = std::fs::read_to_string(SCRIPT_PATH).unwrap();
            script.replace("\r\n", "\n").replace('\n', "\r")
        }
    };
    println!("payload bytes: {}", payload.len());

    let pty_system = portable_pty::native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows: 40,
            cols: 120,
            pixel_width: 0,
            pixel_height: 0,
        })
        .expect("openpty");
    let mut cmd = CommandBuilder::new(&niu);
    cmd.env("NIU_LANG", "en");
    let mut child = pair.slave.spawn_command(cmd).expect("spawn niu");
    let mut reader = pair.master.try_clone_reader().expect("reader");
    let mut writer = pair.master.take_writer().expect("writer");
    let (tx, rx) = std::sync::mpsc::channel::<Vec<u8>>();
    std::thread::spawn(move || {
        let mut buf = [0u8; 16384];
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
    let deadline = std::time::Instant::now() + Duration::from_secs(45);
    let mut transcript = Vec::new();
    let mut answered = false;
    let mut sent_at: Option<std::time::Instant> = None;
    while std::time::Instant::now() < deadline {
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(chunk) => {
                transcript.extend_from_slice(&chunk);
                if !answered && find(&transcript, b"\x1b[6n").is_some() {
                    let _ = writer.write_all(b"\x1b[1;1R");
                    let _ = writer.flush();
                    answered = true;
                    println!("[trace] cursor query answered");
                }
                if sent_at.is_none() && answered && find(&transcript, b"Niubash ").is_some() {
                    std::thread::sleep(Duration::from_millis(800));
                    writer.write_all(payload.as_bytes()).unwrap();
                    writer.flush().unwrap();
                    sent_at = Some(std::time::Instant::now());
                    println!("[trace] paste sent");
                }
                if find(&transcript, b"panicked at").is_some() {
                    println!("[trace] PANIC in transcript");
                    break;
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(_) => break,
        }
        if let Some(t) = sent_at {
            if t.elapsed() > Duration::from_secs(30) {
                break;
            }
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    let out = String::from_utf8_lossy(&transcript).into_owned();
    std::fs::write(std::env::temp_dir().join("niu341-transcript.txt"), &out).unwrap();
    // Strip ANSI for the readable summary.
    let mut plain = String::new();
    let mut in_esc = false;
    for ch in out.chars() {
        match ch {
            '\u{1b}' => in_esc = true,
            _ if in_esc => {
                if ch.is_ascii_alphabetic() {
                    in_esc = false;
                }
            }
            _ => plain.push(ch),
        }
    }
    std::fs::write(std::env::temp_dir().join("niu341-plain.txt"), &plain).unwrap();
    for marker in [
        "10 是偶数，函数返回成功",
        "7 不是偶数，函数返回失败",
        "===== 测试结束 =====",
        "panicked at",
        "is_even 10; then",
    ] {
        println!("marker {marker:?}: {}", plain.contains(marker));
    }
    println!("plain length: {}", plain.len());
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || hay.len() < needle.len() {
        return None;
    }
    hay.windows(needle.len()).position(|w| w == needle)
}
