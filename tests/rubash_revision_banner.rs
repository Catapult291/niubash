//! Pin the `niu --version` banner to the rubash commit that is actually linked.
//!
//! build.rs used to prefer a sibling `../rubash` checkout over `Cargo.lock`, so
//! on any machine that had that directory — every developer box — the banner
//! printed the local tree's HEAD (with a `-dirty` marker) instead of the commit
//! Cargo resolved and compiled. Bug reports carrying that banner therefore
//! named code that was never in the binary.

use std::fs;
use std::path::PathBuf;
use std::process::Command;

/// The revision Cargo resolved for the `rubash` git dependency, truncated to
/// the 12 characters the banner prints. `None` when rubash is a path
/// dependency, which is the only case where the local checkout describes the
/// build.
fn rubash_rev_from_lock() -> Option<String> {
    let lock = fs::read_to_string(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("Cargo.lock"))
        .expect("read Cargo.lock");

    let mut in_rubash = false;
    for line in lock.lines() {
        let trimmed = line.trim();
        if trimmed == "[[package]]" {
            in_rubash = false;
            continue;
        }
        if trimmed == "name = \"rubash\"" {
            in_rubash = true;
            continue;
        }
        if !in_rubash {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix("source = ") {
            let rev = rest.trim_matches('"').rsplit_once('#')?.1;
            return Some(rev.chars().take(12).collect());
        }
    }
    None
}

/// Pull the revision out of the `  rubash   git <rev>` banner line.
fn banner_rubash_rev(stdout: &str) -> Option<String> {
    stdout
        .lines()
        .find_map(|line| line.trim().strip_prefix("rubash"))
        .and_then(|rest| rest.trim().strip_prefix("git "))
        .map(|rev| rev.trim().to_string())
}

#[test]
fn version_banner_reports_the_locked_rubash_commit() {
    let Some(locked) = rubash_rev_from_lock() else {
        // rubash is a path dependency; the banner is allowed to describe the
        // local checkout instead.
        return;
    };

    let output = Command::new(PathBuf::from(env!("CARGO_BIN_EXE_niu")))
        .arg("--version")
        .output()
        .expect("spawn niu --version");
    let stdout = String::from_utf8_lossy(&output.stdout);

    let reported = banner_rubash_rev(&stdout)
        .unwrap_or_else(|| panic!("no rubash line in the version banner:\n{stdout}"));

    assert_eq!(
        reported, locked,
        "the version banner must name the rubash commit Cargo actually links \
         (Cargo.lock), not a sibling ../rubash checkout's HEAD:\n{stdout}"
    );
}

/// The release pipeline's smoke gate (`.github/workflows/release.yml`,
/// build-linux / build-macos "Smoke-test built binary" step) greps the
/// `--version` banner for two exact substrings:
///
/// - `Niubash <version> ` — the head line must keep the version followed by
///   a space before the tagline.
/// - `rubash   git <rev>` — the engine line must keep the three spaces and
///   the `git ` prefix (printed only when build.rs resolved a real commit).
///
/// This test pins those substrings so a banner rewording fails `cargo test`
/// here instead of failing a release build's smoke gate.
#[test]
fn version_banner_lines_match_release_smoke_greps() {
    let output = Command::new(PathBuf::from(env!("CARGO_BIN_EXE_niu")))
        .arg("--version")
        .output()
        .expect("spawn niu --version");
    let stdout = String::from_utf8_lossy(&output.stdout);

    let head = stdout.lines().next().unwrap_or_default();
    assert!(
        head.starts_with(&format!("Niubash {} ", env!("CARGO_PKG_VERSION"))),
        "release smoke greps for 'Niubash <version> ' — banner head was {head:?}"
    );

    if let Some(rev) = banner_rubash_rev(&stdout) {
        assert!(
            stdout.contains(&format!("rubash   git {rev}")),
            "release smoke greps for 'rubash   git <rev>' (three spaces) — banner was:\n{stdout}"
        );
    }
}

/// `--version` ends with the same unixwin portal lines as the `--help`
/// tail (GNU home-page convention): the domain must be verbatim so users
/// and downstream packaging can grep either face for the project home.
#[test]
fn version_banner_closes_with_the_unixwin_portal_lines() {
    let output = Command::new(PathBuf::from(env!("CARGO_BIN_EXE_niu")))
        .arg("--version")
        .output()
        .expect("spawn niu --version");
    let stdout = String::from_utf8_lossy(&output.stdout);

    for line in [
        "Home: <https://unixwin.dev>",
        "Docs & downloads: <https://unixwin.dev>",
    ] {
        assert!(
            stdout.contains(line),
            "niu --version lost the portal line `{line}`:\n{stdout}"
        );
    }
}
