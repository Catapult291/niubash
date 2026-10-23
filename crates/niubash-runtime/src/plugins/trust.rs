//! Graded trust protocol for external plugin sources (design §12).
//!
//! Two tiers, weakest to strongest:
//!
//! * [`TrustPolicy::Checksum`] — the hash lock. The tree digest is recorded
//!   at install, enforced at `trust`/`verify`, and pinned in the registry.
//!   This is the default; every source gets it for free.
//! * [`TrustPolicy::LocalSign`] — the local signature tier. The user ran
//!   `niu plugin source sign <id>` after reviewing the tree: the tree
//!   digest is signed with a machine-local ed25519 key. Any update that
//!   changes the tree re-enters the execution gate until re-signed —
//!   "only trees I personally reviewed and signed stay trusted".
//!
//! Signatures never travel over the network (there is no key server); the
//! signature records the *reviewer's* local provenance, which is why the
//! public key is stored alongside and the key file never leaves
//! `~/.niubash/sources/`.

use std::fs;
use std::path::PathBuf;

use anyhow::{anyhow, Context};
use ed25519_dalek::{Signer, SigningKey, Verifier};
use serde::{Deserialize, Serialize};

use super::sources::sources_root;

/// Trust tier of a source (§12.1: the index `signature` field stays
/// `unsupported` upstream; these tiers are the *local* trust protocol).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "kebab-case")]
pub enum TrustPolicy {
    /// Hash lock: recorded tree digest enforced at every gate (default).
    #[default]
    Checksum,
    /// Local signature: this exact tree was reviewed and signed with the
    /// machine-local ed25519 key.
    LocalSign,
}

impl TrustPolicy {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Checksum => "checksum",
            Self::LocalSign => "local-sign",
        }
    }
}

/// A local signature over one source tree digest.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceSignature {
    /// Signing scheme tag (`ed25519-local@0.1.0`).
    pub algorithm: String,
    /// Hex-encoded ed25519 public key of the local signing key.
    pub public_key: String,
    /// The tree digest (sha256 hex) that was signed.
    pub digest: String,
    /// Hex-encoded ed25519 signature over `digest`.
    pub signature: String,
}

const SIGNATURE_ALGORITHM: &str = "ed25519-local@0.1.0";

fn signing_key_path() -> PathBuf {
    sources_root().join("signing-key.ed25519")
}

/// Load the machine-local signing key, creating it on first use (32 random
/// seed bytes from the OS RNG). The key is never copied anywhere else.
fn load_or_create_signing_key() -> anyhow::Result<SigningKey> {
    let path = signing_key_path();
    if path.is_file() {
        let bytes = fs::read(&path).context("failed to read the plugin signing key")?;
        let seed: [u8; 32] = bytes
            .try_into()
            .map_err(|_| anyhow!("plugin signing key is malformed (expected 32 bytes)"))?;
        return Ok(SigningKey::from_bytes(&seed));
    }
    let key = SigningKey::generate(&mut rand_core::OsRng);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&path, key.to_bytes()).with_context(|| {
        format!(
            "failed to write the plugin signing key to {}",
            path.display()
        )
    })?;
    Ok(key)
}

/// Sign a tree digest with the local key.
pub fn sign_digest(digest: &str) -> anyhow::Result<SourceSignature> {
    let key = load_or_create_signing_key()?;
    let signature = key.sign(digest.as_bytes());
    Ok(SourceSignature {
        algorithm: SIGNATURE_ALGORITHM.to_string(),
        public_key: hex(&key.verifying_key().to_bytes()),
        digest: digest.to_ascii_lowercase(),
        signature: hex(&signature.to_bytes()),
    })
}

/// Verify a recorded signature against an actual tree digest. Returns
/// `false` (never errors) on any mismatch, stale digest, bad hex, or wrong
/// algorithm — callers decide how to surface it.
pub fn verify_signature(record: &SourceSignature, actual_digest: &str) -> bool {
    if record.algorithm != SIGNATURE_ALGORITHM {
        return false;
    }
    if !record.digest.eq_ignore_ascii_case(actual_digest) {
        return false; // signed a different tree than the one present
    }
    let Some(public_key) = unhex(&record.public_key) else {
        return false;
    };
    let Ok(public_key) = <[u8; 32]>::try_from(public_key.as_slice()) else {
        return false;
    };
    let Ok(verifying_key) = ed25519_dalek::VerifyingKey::from_bytes(&public_key) else {
        return false;
    };
    let Some(signature) = unhex(&record.signature) else {
        return false;
    };
    let Ok(signature) = <[u8; 64]>::try_from(signature.as_slice()) else {
        return false;
    };
    verifying_key
        .verify(
            record.digest.to_ascii_lowercase().as_bytes(),
            &signature.into(),
        )
        .is_ok()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    let text = text.trim();
    if !text.len().is_multiple_of(2) {
        return None;
    }
    let mut out = Vec::with_capacity(text.len() / 2);
    let bytes = text.as_bytes();
    for pair in bytes.chunks(2) {
        let high = (pair[0] as char).to_digit(16)?;
        let low = (pair[1] as char).to_digit(16)?;
        out.push((high * 16 + low) as u8);
    }
    Some(out)
}

/// Path of the local signing key (exposed for tests).
#[cfg(test)]
pub(crate) fn local_signing_key_path() -> PathBuf {
    signing_key_path()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::PROCESS_STATE_LOCK;
    use std::path::Path;

    struct EnvVarGuard {
        name: &'static str,
        previous: Option<std::ffi::OsString>,
    }

    impl EnvVarGuard {
        fn set(name: &'static str, value: &Path) -> Self {
            let previous = std::env::var_os(name);
            std::env::set_var(name, value);
            Self { name, previous }
        }
    }

    impl Drop for EnvVarGuard {
        fn drop(&mut self) {
            match &self.previous {
                Some(value) => std::env::set_var(self.name, value),
                None => std::env::remove_var(self.name),
            }
        }
    }

    fn temp_root(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "niu-trust-{}-{}-{}",
            label,
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn signature_round_trip_and_tamper_detection() {
        let _env_lock = PROCESS_STATE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let temp = temp_root("round-trip");
        let _guard = EnvVarGuard::set("NIU_PLUGIN_SOURCES_ROOT", &temp);
        let signature = sign_digest(&"a".repeat(64)).unwrap();
        assert_eq!(signature.algorithm, SIGNATURE_ALGORITHM);
        assert_eq!(signature.digest, "a".repeat(64));
        assert!(verify_signature(&signature, &"a".repeat(64)));
        // A different tree is not covered by this signature.
        assert!(!verify_signature(&signature, &"b".repeat(64)));
        // Bit-flipping the signature must invalidate it.
        let mut tampered = signature.clone();
        let mut bytes = tampered.signature.into_bytes();
        bytes[0] = if bytes[0] == b'0' { b'1' } else { b'0' };
        tampered.signature = String::from_utf8(bytes).unwrap();
        assert!(!verify_signature(&tampered, &"a".repeat(64)));
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn signing_key_is_created_once_and_reused_per_root() {
        let _env_lock = PROCESS_STATE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let temp = temp_root("key-reuse");
        let _guard = EnvVarGuard::set("NIU_PLUGIN_SOURCES_ROOT", &temp);
        assert!(!local_signing_key_path().exists());

        let first = sign_digest(&"c".repeat(64)).unwrap();
        let second = sign_digest(&"c".repeat(64)).unwrap();
        // Same local key signs both digests, and the key landed in the
        // (overridden) sources root, not the real home.
        assert_eq!(first.public_key, second.public_key);
        assert!(verify_signature(&first, &"c".repeat(64)));
        assert!(local_signing_key_path().starts_with(&temp));
        assert!(local_signing_key_path().is_file());
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn trust_policy_serializes_snake_case() {
        assert_eq!(TrustPolicy::Checksum.as_str(), "checksum");
        assert_eq!(TrustPolicy::LocalSign.as_str(), "local-sign");
        // As a record value (the shape the registry stores).
        #[derive(serde::Serialize)]
        struct Wrapper {
            trust_policy: TrustPolicy,
        }
        let text = toml::to_string(&Wrapper {
            trust_policy: TrustPolicy::LocalSign,
        })
        .unwrap();
        assert!(text.contains("local-sign"), "{text}");
        let text = toml::to_string(&Wrapper {
            trust_policy: TrustPolicy::Checksum,
        })
        .unwrap();
        assert!(text.contains("checksum"), "{text}");
    }
}
