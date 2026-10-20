//! Static guard over the release-archive surface (niubash#470).
//!
//! The v1.4.2 x64 zip was 57.1 MB and 61% of it was duplicated payload: the
//! staged tree's hardlinks (wpm's own install layout — the whole `git.exe`
//! family is one binary, `awk`/`gawk` likewise) are flattened by the
//! Copy-Item staging sweep, and Compress-Archive then stored every copy in
//! full. The fix ships two hardlink-native companions next to the unchanged
//! zip: a `.tar.gz` (bsdtar records and restores NTFS hardlinks — measured
//! 54.47 MB -> 29.63 MB on the v1.4.2 tree with the links re-linked after
//! unpack) and a `.7z` (smallest download via solid compression, but
//! measured 7-Zip 26.04 does not restore hardlinks, so it gates on content
//! identity only).
//!
//! A placeholder-and-relink design (zero-byte stand-ins plus a manifest plus
//! a rebuild script) was REJECTED by the owner: manual unzip breaks, wpm
//! upgrade integration is a landmine, antivirus flags zero-byte executables,
//! and the symlink fallback breaks on FAT32/non-admin. These tests pin the
//! chosen design AND the absence of the rejected one, so neither can drift
//! silently.
//!
//! Like `installer_surface.rs`, this is a source-level review leg: the
//! runtime round-trip proof lives in the release workflow's
//! "Verify hardlink-native archives" step, which these pins reference.

use std::fs;
use std::path::PathBuf;

fn repo_file(rel: &str) -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(rel);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("{rel} must exist next to the crate: {e}"))
}

fn packager() -> String {
    repo_file("scripts/package-release.ps1")
}

fn release_workflow() -> String {
    repo_file(".github/workflows/release.yml")
}

#[test]
fn packager_relinks_identical_staged_files_before_compression() {
    let text = packager();
    assert!(
        text.contains("function Complete-StagedHardlinks"),
        "package-release.ps1 must define Complete-StagedHardlinks (the staged-tree \
         hardlink restoration); without it every archive stores duplicated payload \
         in full (niubash#470)"
    );
    // The alignment must happen BEFORE the zip is written, and the
    // hardlink-native archives must be produced by the same script run.
    let align = text
        .find("Complete-StagedHardlinks -StageDir $stageDir")
        .expect("the packager must invoke Complete-StagedHardlinks on the staged tree");
    let compress = text
        .find("Compress-Archive -LiteralPath $stageDir")
        .expect("package-release.ps1 must still produce the classic zip");
    let targz = text
        .find("tar -czf $tarGzPath -C $distDir $packageName")
        .expect("package-release.ps1 must produce the hardlink-native .tar.gz (bsdtar)");
    let seven = text
        .find("-snh -mx=9")
        .expect("package-release.ps1 must produce the .7z companion");
    assert!(
        align < compress,
        "hardlink alignment must precede Compress-Archive"
    );
    assert!(
        align < targz,
        "hardlink alignment must precede the tar.gz pack"
    );
    assert!(align < seven, "hardlink alignment must precede the 7z pack");
}

#[test]
fn packager_hardlink_alignment_is_content_neutral_and_loud_when_degraded() {
    let text = packager();
    // Only SHA256-equal files may be linked — this is the whole safety story.
    assert!(
        text.contains("Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256"),
        "the alignment must group by SHA-256; linking non-identical files would \
         corrupt the package"
    );
    // A volume without hardlink support degrades to copies with a warning,
    // never a failed release and never a silent divergence.
    assert!(
        text.contains("keeping an independent copy"),
        "the alignment must warn loudly and keep copies when hardlinks are \
         unavailable (FAT32/network staging volumes)"
    );
}

#[test]
fn packager_keeps_the_zip_escape_hatch() {
    let text = packager();
    assert!(
        text.contains("[switch]$SkipHardlinkArchives"),
        "-SkipHardlinkArchives must stay available for callers that want the \
         legacy zip-only output shape"
    );
}

#[test]
fn workflow_verifies_archive_round_trip_before_upload() {
    let text = release_workflow();
    assert!(
        text.contains("Verify hardlink-native archives"),
        "release.yml must verify the hardlink-native archives after packaging; \
         an archive that unpacks to anything other than the staged bytes must \
         fail the release"
    );
    // Byte-identity for BOTH companions...
    assert!(
        text.contains(".tar.gz round-trip is not byte-identical"),
        "the workflow must hard-fail when the .tar.gz round-trip diverges from \
         the staged tree"
    );
    assert!(
        text.contains(".7z round-trip is not byte-identical"),
        "the workflow must hard-fail when the .7z round-trip diverges from the \
         staged tree"
    );
    // ...and actual hardlink restoration for the tar.gz (the format that
    // measured as hardlink-faithful; 7-Zip is exempt by documented limit).
    assert!(
        text.contains(".tar.gz did not restore hardlinks"),
        "the workflow must assert link counts on the .tar.gz extraction; content \
         identity alone cannot see a flattened link family"
    );
    assert!(
        text.contains("Get-HardLinkCount"),
        "the workflow needs the fsutil-based link counter for the restoration probe"
    );
}

#[test]
fn workflow_uploads_and_hashes_both_companions() {
    let text = release_workflow();
    for needle in [
        "dist/niubash-v${{ steps.release.outputs.version }}-win-${{ matrix.arch }}.tar.gz",
        "dist/niubash-v${{ steps.release.outputs.version }}-win-${{ matrix.arch }}.7z",
        "dist/niubash-win-${{ matrix.arch }}.tar.gz",
        "dist/niubash-win-${{ matrix.arch }}.7z",
    ] {
        assert!(
            text.contains(needle),
            "release.yml must upload the archive asset and its latest-compatible \
             alias: {needle}"
        );
    }
    assert!(
        text.contains("-o -name '*.7z'"),
        "SHA256SUMS must cover the .7z payload (niubash#311: every shipped \
         artifact is hashed)"
    );
    assert!(
        text.contains("artifacts/**/*.7z"),
        "the GitHub release must attach the .7z assets"
    );
}

#[test]
fn release_body_recommends_tar_gz_and_discloses_the_7z_limitation() {
    let text = release_workflow();
    assert!(
        text.contains("prefer `*.tar.gz` — it is hardlink-native"),
        "the release body must steer users to the hardlink-native tar.gz \
         (download size + on-disk footprint) — niubash#470"
    );
    assert!(
        text.contains("degrades gracefully to independent byte-identical copies"),
        "the release body must document the harmless degradation on filesystems \
         without hardlink support (FAT32/exFAT)"
    );
    assert!(
        text.contains("restore the shared families as independent copies"),
        "the release body must disclose that 7-Zip does not restore the \
         hardlinks (measured on 26.04) so the .7z choice is an informed one"
    );
}

#[test]
fn rejected_placeholder_design_must_not_return() {
    let packager_text = packager();
    let workflow_text = release_workflow();
    for (what, text) in [
        ("scripts/package-release.ps1", packager_text.as_str()),
        (".github/workflows/release.yml", workflow_text.as_str()),
    ] {
        for needle in ["relink-manifest", "placeholder"] {
            assert!(
                !text.contains(needle),
                "{what} mentions '{needle}': the zero-byte-placeholder + manifest \
                 + relink-script design was REJECTED for the release archives \
                 (manual unzip breaks, wpm upgrade integration is a landmine, \
                 antivirus flags zero-byte executables, symlink fallback breaks \
                 on FAT32/non-admin). The shipped design keeps every staged file \
                 a complete real file — hardlinks only share storage."
            );
        }
    }
}
