//! Static guard over the installer surface (`installer/niubash.iss`).
//!
//! niubash#398 found an unowned .NET shim occupying the
//! `C:\Program Files\Git\bin\bash.exe` convention slot and suspected the niu
//! installer had put it there. The investigation (PR #414) overturned the
//! premise — the installer runs with `PrivilegesRequired=lowest`, never
//! elevates, and the slot directory is admin-walled — and ruled that the
//! install tree does NOT claim the slot. That ruling lived only in a GitHub
//! comment; these tests pin it to the repository so the surface cannot drift
//! back: every write, delete and run surface of the installer stays inside
//! the app-owned tree (plus the one HKCU PATH value), and nothing in the
//! script can even name a foreign absolute location.
//!
//! This is the "installer script logic review" leg of the #398 verification;
//! `scripts/installer-slot-probe.py` is the runtime leg (controlled-directory
//! install/relink/uninstall replay with byte-identity checks).

use std::fs;
use std::path::PathBuf;

fn installer_script() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("installer")
        .join("niubash.iss")
}

/// The whole script, lines attached, for failure messages that point at the
/// offending line the way a review would.
fn script_lines() -> Vec<(usize, String)> {
    let text = fs::read_to_string(installer_script())
        .expect("installer/niubash.iss must exist next to the crate");
    text.lines()
        .enumerate()
        .map(|(i, line)| (i + 1, line.to_string()))
        .collect()
}

fn lines_matching(lines: &[(usize, String)], needle: &str) -> Vec<(usize, String)> {
    lines
        .iter()
        .filter(|(_, line)| line.contains(needle))
        .cloned()
        .collect()
}

/// Extract `Key: value` entries from a named section. Inno directive lines
/// are `Key: "value"; Key: "value"; ...` — split each non-comment line on
/// the `;` separators and return the quoted-or-bare `value` for every piece
/// whose key matches `keyword`. Section bodies run until the next `[Bracket]`
/// header. (None of the directives in this script carry a `;` inside a quoted
/// value; if one ever does, this parser must grow quote awareness first.)
fn section_entries(
    lines: &[(usize, String)],
    section: &str,
    keyword: &str,
) -> Vec<(usize, String)> {
    let header = format!("[{section}]");
    let mut in_section = false;
    let mut found = Vec::new();
    let prefix = format!("{keyword}:");
    for (number, line) in lines {
        let trimmed = line.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            in_section = trimmed == header;
            continue;
        }
        if !in_section || trimmed.starts_with(';') || trimmed.is_empty() {
            continue;
        }
        for piece in trimmed.split(';') {
            let piece = piece.trim();
            if let Some(rest) = piece.strip_prefix(&prefix) {
                let value = rest.trim().trim_matches('"').to_string();
                found.push((*number, value));
            }
        }
    }
    found
}

/// niubash#398 ruling, fact 1: the installer never elevates, so it can never
/// write an admin-walled foreign slot such as `C:\Program Files\Git\bin`.
#[test]
fn installer_requires_lowest_privileges() {
    let lines = script_lines();
    let declared = lines_matching(&lines, "PrivilegesRequired");
    assert_eq!(
        declared.len(),
        1,
        "exactly one PrivilegesRequired line expected: {declared:?}"
    );
    assert!(
        declared[0]
            .1
            .replace(" ", "")
            .to_lowercase()
            .contains("privilegesrequired=lowest"),
        "PrivilegesRequired must stay `lowest` — elevation is the only way this \
         installer could ever touch a foreign slot like C:\\Program Files\\Git\\bin, \
         and niubash#398's ruling is that it never does: {declared:?}"
    );
}

/// niubash#398 ruling, fact 2: the script's world is Inno constants and
/// relative names. No absolute drive path and no `Program Files` reference
/// may appear anywhere — including comments — so the Git Bash slot cannot be
/// named, let alone written, from this surface.
#[test]
fn installer_never_names_an_absolute_foreign_location() {
    let lines = script_lines();
    let mut offenders = Vec::new();
    for (number, line) in &lines {
        // A drive-letter absolute path: `X:` followed by `\` or `/`, where
        // the letter starts a word (this excludes URL schemes like `https:`).
        let bytes = line.as_bytes();
        let mut index = 0;
        while index + 2 < bytes.len() {
            let starts_word = index == 0 || !bytes[index - 1].is_ascii_alphabetic();
            if starts_word
                && bytes[index].is_ascii_alphabetic()
                && bytes[index + 1] == b':'
                && (bytes[index + 2] == b'\\' || bytes[index + 2] == b'/')
            {
                offenders.push(format!("line {number}: absolute path — {line:?}"));
                break;
            }
            index += 1;
        }
        if line.to_lowercase().contains("program files") {
            offenders.push(format!(
                "line {number}: 'Program Files' reference — {line:?}"
            ));
        }
    }
    assert!(
        offenders.is_empty(),
        "installer/niubash.iss must not reference any absolute foreign location \
         (niubash#398: the Git Bash slot stays unclaimed; the app tree is reached \
         through Inno constants like {{app}}/{{localappdata}}):\n{}",
        offenders.join("\n")
    );
}

/// Every file the installer writes or deletes lives under `{app}` (or is an
/// exact retired name under `{app}`), so an uninstall can never leave a
/// dangling artifact in a directory niu does not own.
#[test]
fn file_write_and_delete_surfaces_stay_under_the_app_directory() {
    let lines = script_lines();

    for section in ["Files", "InstallDelete", "UninstallDelete"] {
        let keyword = if section == "Files" {
            "DestDir"
        } else {
            "Name"
        };
        for (number, value) in section_entries(&lines, section, keyword) {
            let bare = value.trim_start_matches('"').trim_end_matches('"');
            assert!(
                bare == "{app}" || bare.starts_with("{app}\\"),
                "[{section}] line {number} writes outside the app tree: {value:?} — \
                 niubash#398: the installer's filesystem surface is {{app}}-confined"
            );
        }
    }
}

/// Inno wildcard match, `*` = any run, case-insensitive — enough for the
/// sanctioned retired-shape patterns (each carries at most two stars).
fn glob_matches(pattern: &str, text: &str) -> bool {
    let pattern = pattern.to_lowercase();
    let text = text.to_lowercase();
    let parts: Vec<&str> = pattern.split('*').collect();
    if parts.len() == 1 {
        return pattern == text;
    }
    let mut cursor = 0;
    for (index, part) in parts.iter().enumerate() {
        if index == 0 {
            if !text.starts_with(part) {
                return false;
            }
            cursor = part.len();
        } else if index == parts.len() - 1 {
            // The final literal must close the string, past everything found
            // so far.
            if text.len() < cursor + part.len() || !text[cursor..].ends_with(part) {
                return false;
            }
        } else {
            match text[cursor..].find(part) {
                Some(position) => cursor += position + part.len(),
                None => return false,
            }
        }
    }
    true
}

/// The delete entries only ever match generated retired-shape wildcards (plus
/// the one exact retired binary name), never a live entry file. This is what
/// keeps the #376 sweep from eating a user's `bash.exe`/`sh.exe`/`git.exe`
/// while it cleans the dead ones — and what makes the uninstall log replay
/// unable to reach outside {app} even through a wildcard.
#[test]
fn delete_entries_match_only_retired_shapes() {
    let lines = script_lines();
    // Shapes the [InstallDelete] comment sanctions: generated backup shapes
    // only. An exact name must be a retired binary (allow-listed here).
    const SANCTIONED_SHAPES: [&str; 3] = ["*.bak-*", "*.old-*", "*.niu-old-*"];
    const RETIRED_EXACT: [&str; 1] = ["{app}\\niubash.exe"];

    for section in ["InstallDelete", "UninstallDelete"] {
        for (number, value) in section_entries(&lines, section, "Name") {
            let bare = value.trim_start_matches('"').trim_end_matches('"');
            if RETIRED_EXACT
                .iter()
                .any(|allowed| allowed.eq_ignore_ascii_case(bare))
            {
                continue;
            }
            assert!(
                SANCTIONED_SHAPES
                    .iter()
                    .any(|shape| glob_matches(shape, bare)),
                "[{section}] line {number} deletes by a non-retired-shape name: {value:?} — \
                 sweeps must match generated shapes ({SANCTIONED_SHAPES:?}) or an \
                 allow-listed retired name, never a live entry"
            );
        }
    }
}

/// Every external process the installer launches comes from the app tree, so
/// the [Run] steps execute the payload niu.exe (the #397 relink) and the
/// bundled winuxcmd dispatcher — never a downloaded or foreign binary.
#[test]
fn run_steps_execute_only_app_tree_binaries() {
    let lines = script_lines();
    for (number, value) in section_entries(&lines, "Run", "Filename") {
        let bare = value.trim_start_matches('"').trim_end_matches('"');
        assert!(
            bare.starts_with("{app}\\") || bare.starts_with("{sys}\\"),
            "[Run] line {number} launches a binary from outside the app tree: {value:?} — \
             only {{app}} payloads (and {{sys}} taskkill for the silent-update \
             close) may run (niubash#398: nothing reaches for a foreign slot)"
        );
    }
}

/// The only registry surface is the user `Environment\Path` value: add on
/// install (the PATH task), sweep on uninstall. Anything else — an uninstall
/// that wrote a marker, a repair that poked a foreign registration — would be
/// a #398-shaped surprise: state left behind that outlives the app tree.
#[test]
fn registry_writes_confine_to_the_user_environment_path_value() {
    let lines = script_lines();
    let writes = lines_matching(&lines, "RegWrite");
    assert!(
        !writes.is_empty(),
        "the PATH task requires its registry writes; found none — did the \
         mechanism move? Update this guard with it deliberately"
    );
    for (number, line) in &writes {
        assert!(
            line.contains("HKEY_CURRENT_USER"),
            "line {number}: registry write outside HKEY_CURRENT_USER: {line:?}"
        );
        assert!(
            line.contains("NiubashEnvironmentKey") && line.contains("NiubashPathValueName"),
            "line {number}: registry write outside the Environment\\Path value: {line:?}"
        );
    }
    // The constants those writes go through pin the exact key and value.
    let key_line = lines_matching(&lines, "NiubashEnvironmentKey =");
    let value_line = lines_matching(&lines, "NiubashPathValueName =");
    assert!(
        key_line.iter().any(|(_, l)| l.contains("'Environment'")),
        "NiubashEnvironmentKey must name the HKCU 'Environment' key: {key_line:?}"
    );
    assert!(
        value_line.iter().any(|(_, l)| l.contains("'Path'")),
        "NiubashPathValueName must name the 'Path' value: {value_line:?}"
    );
}

/// Shortcuts point at the payload in {app}; their locations are Inno-managed
/// shell folders that the uninstall log owns, so no shortcut can dangle a
/// foreign slot into a session after uninstall (the #398 failure shape).
#[test]
fn shortcuts_point_into_the_app_tree() {
    let lines = script_lines();
    const SHELL_FOLDER_PREFIXES: [&str; 3] = ["{group}", "{autodesktop}", "{userdesktop}"];
    for (number, value) in section_entries(&lines, "Icons", "Filename") {
        let bare = value.trim_start_matches('"').trim_end_matches('"');
        assert!(
            bare.starts_with("{app}\\"),
            "[Icons] line {number}: shortcut target outside the app tree: {value:?}"
        );
    }
    for (number, value) in section_entries(&lines, "Icons", "Name") {
        let bare = value.trim_start_matches('"').trim_end_matches('"');
        assert!(
            SHELL_FOLDER_PREFIXES
                .iter()
                .any(|prefix| bare.starts_with(prefix)),
            "[Icons] line {number}: shortcut placed outside Inno-managed shell \
             folders {SHELL_FOLDER_PREFIXES:?}: {value:?}"
        );
    }
}
