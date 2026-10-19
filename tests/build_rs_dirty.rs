//! Unit tests for build.rs's rubash stamp (rubash#827, recommendation 3).
//!
//! #488's blind spot: the banner prints rubash's HEAD hash, but a hash does
//! not guarantee content — the v1.4.0 incident shipped a banner naming a
//! commit whose tree the binary did not match. The `-dirty` half of the stamp
//! flags uncommitted trees; the `-dirty-<digest>` half names WHICH uncommitted
//! state was compiled, so two different dirty trees can never print the same
//! banner.
//!
//! build.rs is included directly (`#[path]`) so these tests exercise the exact
//! code a build runs, against throwaway fixture repos — no sibling `../rubash`
//! involved (the real checkout path keeps working the way it always has).

// The test crate only calls `revision_label_for_checkout`; everything else in
// build.rs (main, the icon embedder, the lock reader) is legitimately unused
// HERE while very much used by the real build script. CI runs the suite under
// `-D warnings`, so the unused halves must be allowed at the module edge —
// NOT inside build.rs, where genuine dead code should still fail the build.
#[allow(dead_code)]
#[path = "../build.rs"]
mod build_rs;

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

/// A throwaway git repo that cleans up after itself.
struct FixtureRepo {
    dir: PathBuf,
}

impl FixtureRepo {
    fn new(name: &str) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("niu-buildrs-{name}-{}-{nanos}", std::process::id()));
        fs::create_dir_all(&dir).expect("create fixture dir");
        assert!(git(&dir, &["init", "-q"]), "git init failed");
        assert!(git(
            &dir,
            &["config", "user.email", "build-rs-test@example.test"]
        ));
        assert!(git(&dir, &["config", "user.name", "build.rs test"]));
        assert!(git(&dir, &["config", "commit.gpgsign", "false"]));
        Self { dir }
    }

    fn write(&self, rel: &str, content: &str) {
        let path = self.dir.join(rel);
        fs::create_dir_all(path.parent().expect("parent")).expect("create parent dir");
        fs::write(path, content).expect("write fixture file");
    }

    fn commit_all(&self, message: &str) {
        assert!(git(&self.dir, &["add", "-A"]), "git add failed");
        assert!(
            git(&self.dir, &["commit", "-q", "-m", message]),
            "git commit failed"
        );
    }

    fn head_short(&self) -> String {
        let output = Command::new("git")
            .arg("-C")
            .arg(&self.dir)
            .args(["rev-parse", "--short=12", "HEAD"])
            .output()
            .expect("run git rev-parse");
        assert!(output.status.success(), "git rev-parse failed");
        String::from_utf8_lossy(&output.stdout).trim().to_string()
    }

    /// The stamp build.rs would bake into the banner for this tree.
    fn stamp(&self) -> String {
        build_rs::revision_label_for_checkout(&self.dir).expect("stamp the fixture repo")
    }
}

impl Drop for FixtureRepo {
    fn drop(&mut self) {
        // Best effort: a leftover temp dir must never fail a test.
        let _ = fs::remove_dir_all(&self.dir);
    }
}

fn git(dir: &Path, args: &[&str]) -> bool {
    Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn digest_suffix(stamp: &str) -> &str {
    stamp.rsplit('-').next().expect("non-empty stamp")
}

#[test]
fn a_clean_tree_stamps_bare_head() {
    let repo = FixtureRepo::new("clean");
    repo.write("engine.txt", "committed content\n");
    repo.commit_all("initial");

    let head = repo.head_short();
    assert_eq!(repo.stamp(), head, "a clean tree must stamp the bare HEAD");
}

#[test]
fn a_dirty_tree_stamps_dirty_with_a_stable_content_digest() {
    let repo = FixtureRepo::new("dirty");
    repo.write("engine.txt", "committed content\n");
    repo.commit_all("initial");
    let head = repo.head_short();

    repo.write("engine.txt", "committed content\nuncommitted edit\n");
    let stamp = repo.stamp();

    assert!(
        stamp.starts_with(&format!("{head}-dirty-")),
        "a dirty tree must stamp '<head>-dirty-<digest>', got {stamp:?}"
    );
    let digest = digest_suffix(&stamp);
    assert_eq!(
        digest.len(),
        8,
        "digest must be 8 hex chars, got {digest:?}"
    );
    assert!(
        digest.bytes().all(|byte| byte.is_ascii_hexdigit()),
        "digest must be hex, got {digest:?}"
    );

    // Same edits, same digest: the stamp is a function of the content.
    assert_eq!(repo.stamp(), stamp, "the dirty digest must be stable");

    // Different edits, different digest: two dirty trees cannot share a banner.
    repo.write("engine.txt", "committed content\na different edit\n");
    let other = repo.stamp();
    assert_ne!(other, stamp, "a changed dirty state must change the stamp");
    assert_ne!(
        digest_suffix(&other),
        digest,
        "the digest must track the content"
    );
}

#[test]
fn staged_but_uncommitted_changes_are_dirty_too() {
    let repo = FixtureRepo::new("staged");
    repo.write("engine.txt", "committed content\n");
    repo.commit_all("initial");
    let head = repo.head_short();

    // Staged without a commit is exactly the "hash honest, content ahead"
    // shape #488/#827 call out: it must not print as a bare HEAD.
    repo.write("engine.txt", "committed content\nstaged edit\n");
    assert!(git(&repo.dir, &["add", "engine.txt"]), "git add failed");

    let stamp = repo.stamp();
    assert!(
        stamp.starts_with(&format!("{head}-dirty-")),
        "a staged-but-uncommitted tree must stamp dirty, got {stamp:?}"
    );
}

#[test]
fn untracked_files_do_not_dirty_the_stamp() {
    let repo = FixtureRepo::new("untracked");
    repo.write("engine.txt", "committed content\n");
    repo.commit_all("initial");
    let head = repo.head_short();

    // Same rule as `git describe --dirty`: untracked scratch files are not
    // part of the compiled content and must not bake a bogus dirty marker.
    repo.write("scratch.tmp", "never added\n");
    assert_eq!(
        repo.stamp(),
        head,
        "untracked files must not dirty the stamp"
    );
}

#[test]
fn restoring_the_tree_restores_the_bare_head_stamp() {
    let repo = FixtureRepo::new("restore");
    let original = "committed content\n";
    repo.write("engine.txt", original);
    repo.commit_all("initial");
    let head = repo.head_short();

    repo.write("engine.txt", "committed content\nedited\n");
    assert!(
        repo.stamp().starts_with(&format!("{head}-dirty-")),
        "precondition: the edited tree stamps dirty"
    );

    repo.write("engine.txt", original);
    assert_eq!(
        repo.stamp(),
        head,
        "restoring the content must restore the bare HEAD stamp"
    );
}
