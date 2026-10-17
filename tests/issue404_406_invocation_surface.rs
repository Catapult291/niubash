//! niubash#404 / #406: launcher-side invocation surface.
//!
//! #406: `niu -- <missing>` must answer with the SHELL's missing-file
//! diagnostic and status 127, not the launcher's raw `(os error 2)` + rc 1.
//!
//! #404: an `exit` inside the startup rc must not print the exit.def:59-62
//! echo. GNU's condition is `interactive && sourcelevel == 0`, so a
//! reader-phase `exit` still echoes — only the sourced-file exit is silent.

use std::process::Command;

fn niu() -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_niu"));
    cmd.env("NIU_SKIP_WINUXCMD_ACTIVATION", "1");
    cmd
}

#[test]
fn missing_script_operand_uses_the_shell_diagnostic() {
    let out = niu()
        .args(["--", "definitely-no-such-script.sh"])
        .output()
        .expect("spawn niubash");
    let stderr = String::from_utf8_lossy(&out.stderr);

    assert_eq!(out.status.code(), Some(127), "stderr: {stderr}");
    assert!(
        stderr.contains("definitely-no-such-script.sh: No such file or directory"),
        "expected the shell missing-file diagnostic, stderr: {stderr}"
    );
    assert!(
        !stderr.contains("os error"),
        "raw launcher OS error leaked: {stderr}"
    );
}

#[test]
fn startup_rc_exit_does_not_echo_the_exit_word() {
    let dir = std::env::temp_dir().join(format!("niu-404-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create scratch");
    let rc = dir.join("rc.sh");
    std::fs::write(&rc, "echo RAN\nexit 3\n").expect("write rc");

    let out = niu()
        .args([
            "--rcfile",
            rc.to_str().expect("utf-8 path"),
            "-C",
            "echo AFTER",
        ])
        .output()
        .expect("spawn niubash");
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);

    assert_eq!(
        out.status.code(),
        Some(3),
        "stdout: {stdout}\nstderr: {stderr}"
    );
    assert_eq!(
        stdout.trim_end(),
        "RAN",
        "the -C body must not run after the rc exits; stdout: {stdout}"
    );
    assert!(
        !stderr.lines().any(|line| line.trim() == "exit"),
        "the rc's exit leaked the exit.def echo: {stderr:?}"
    );
}

#[test]
fn reader_phase_exit_still_echoes() {
    // The guard must not silence the documented echo: a reader-phase `exit`
    // (sourcelevel == 0) prints "exit" exactly like GNU `bash -i -c 'exit 3'`.
    let out = niu()
        .args(["-C", "exit 3"])
        .output()
        .expect("spawn niubash");
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(3), "stderr: {stderr}");
    assert!(
        stderr.lines().any(|line| line.trim() == "exit"),
        "reader-phase exit must echo \"exit\": {stderr:?}"
    );
}
