//! Regression tests for background (`&`) and coproc self-spawn argv.
//!
//! Rubash spawns the host executable for background jobs and coprocs. The
//! spawned argv must stick to the common `-c <script>` contract; a leading
//! `--` is a rubash-CLI-only convention that the niubash launcher rejects as
//! a script-file argument ("unknown argument '--' (not a script file)").

use std::path::PathBuf;
use std::process::Command;

fn niu_binary() -> PathBuf {
    let p = PathBuf::from(env!("CARGO_BIN_EXE_niu"));
    if p.exists() {
        return p;
    }
    let mut fallback = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    fallback.push("target");
    fallback.push("debug");
    fallback.push(if cfg!(windows) { "niu.exe" } else { "niubash" });
    fallback
}

fn run_niu(script: &str) -> (String, String) {
    let output = Command::new(niu_binary())
        .arg("-c")
        .arg(script)
        .env("NIU_SKIP_WINUXCMD_ACTIVATION", "1")
        .output()
        .expect("spawn niubash");
    (
        String::from_utf8_lossy(&output.stdout).to_string(),
        String::from_utf8_lossy(&output.stderr).to_string(),
    )
}

#[test]
fn background_command_keeps_dash_dash_args_out_of_host_cli() {
    let (stdout, stderr) = run_niu("echo --connect-timeout 5 --max-time 10 & wait; echo done");

    assert!(
        !stderr.contains("unknown argument"),
        "background spawn leaked argv into the host CLI: {stderr}"
    );
    assert!(
        stdout.contains("--connect-timeout 5 --max-time 10"),
        "background command lost its arguments, stdout: {stdout}"
    );
    assert!(stdout.contains("done"), "foreground part missing: {stdout}");
}

#[test]
fn coproc_command_keeps_dash_dash_args_out_of_host_cli() {
    let (stdout, stderr) =
        run_niu("coproc C { echo --marker; }; read line <&${C[0]}; echo got=$line");

    assert!(
        !stderr.contains("unknown argument"),
        "coproc spawn leaked argv into the host CLI: {stderr}"
    );
    assert!(
        stdout.contains("got=--marker"),
        "coproc output missing, stdout: {stdout}"
    );
}

/// niubash#362: `&` must survive a large function table. The engine
/// serializes the WHOLE function table into the respawned child's `-c`
/// argument; on Windows that overflows CreateProcessW's 32767-unit
/// command line, and on Unix a single argv entry over MAX_ARG_STRLEN
/// (128 KiB), so the spawn died with `niu: File name too long` before the
/// command ran. The engine now hands an over-budget script to the child
/// through a temp file (__RUBASH_CMD_FILE); niu.exe must read it back.
///
/// The script goes through a FILE (not `-c`) so the test harness's own
/// spawn stays under the limit; the background `echo hello &` is what
/// overflows.
#[test]
fn large_function_table_background_command_runs() {
    let dir = std::env::temp_dir().join(format!("niu-362-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create scratch");
    let path = dir.join("big.sh");

    // ~6000 trivial functions => ~200 KB serialized, past both caps.
    let mut script = String::new();
    for i in 0..6000 {
        script.push_str(&format!("f{i}() {{ local v={i}; : \"$v\"; }}\n"));
    }
    script.push_str("echo hello &\nwait\necho large-fn-status:$?\n");
    std::fs::write(&path, &script).expect("write script");

    let output = Command::new(niu_binary())
        .arg(&path)
        .env("NIU_SKIP_WINUXCMD_ACTIVATION", "1")
        .output()
        .expect("spawn niubash");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    assert!(
        !stderr.contains("File name too long") && !stderr.contains("Argument list too long"),
        "spawn hit an argv/command-line cap: {stderr}"
    );
    assert!(
        stdout.contains("hello"),
        "background child output missing, stdout: {stdout}\nstderr: {stderr}"
    );
    assert!(
        stdout.contains("large-fn-status:0"),
        "wait status wrong, stdout: {stdout}\nstderr: {stderr}"
    );
}
