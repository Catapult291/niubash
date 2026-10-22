#!/usr/bin/env python3
"""Assemble and pack the Microsoft Store MSIX for Niubash.

Run from the repository root:

    python scripts/make_msix.py \
        --identity-name "<Package/Identity Name from Partner Center>" \
        --publisher "CN=XXXXXXXX-XXXX-..." \
        [--publisher-display-name "unixwin"] \
        [--winuxcmd-dir <dir containing winuxcmd.exe + command links>] \
        [--bundle-dir <extra payload copied into the package>]

What it does:
  1. Reads the package version from Cargo.toml and appends ".0" (the Store
     reserves the fourth version segment).
  2. Renders installer/msix/AppxManifest.xml with the Partner Center identity.
  3. Generates the visual assets from assets/niubash-icon-256.png (Pillow).
  4. Assembles the layout under target/msix/layout and packs it with
     makeappx.exe into target/msix/Niubash_<version>_x64.msix.

Notes:
  - Only relative paths are used inside the script; caller-supplied paths
    may be absolute.
  - The Store build must stay green: no PATH modification, no system setting
    changes. `niu` is exposed via the AppExecutionAlias declared in the
    manifest, and bundled command links must be resolved by niu relative to
    its own executable location.
  - The output .msix is unsigned; the Store re-signs on submission. To
    install locally for testing, sign it yourself first.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_TEMPLATE = REPO_ROOT / "installer" / "msix" / "AppxManifest.xml"
FRAGMENTS_SRC = REPO_ROOT / "installer" / "msix" / "Public"
ICON_SOURCE = REPO_ROOT / "assets" / "niubash-icon-256.png"
CARGO_TOML = REPO_ROOT / "Cargo.toml"
LAYOUT_DIR = REPO_ROOT / "target" / "msix" / "layout"
OUTPUT_DIR = REPO_ROOT / "target" / "msix"

# (output filename, width, height, pad-to-fit)
VISUAL_ASSETS = [
    ("StoreLogo.png", 50, 50, False),
    ("Square44x44Logo.png", 44, 44, False),
    ("Square150x150Logo.png", 150, 150, False),
    ("Square310x310Logo.png", 310, 310, True),
    ("Wide310x150Logo.png", 310, 150, True),
]

WELCOME = """\
Niubash — Windows-native, bash-compatible shell.

Interactive start : niu
Quick check       : niu --version
Shell scripts     : niu script.sh

Store build notes:
  - All bundled commands live inside this package next to niu.exe.
  - The applet command links materialize into the package's private
    LocalCache at first launch (the package root is read-only); the links
    are visible inside niubash sessions only.
  - No system settings are modified; configuration stays under the user
    profile (~/.niubashrc).
"""


def read_version() -> str:
    text = CARGO_TOML.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("error: could not read version from Cargo.toml")
    version = match.group(1)
    if version.count(".") != 2:
        raise SystemExit(f"error: unexpected version format in Cargo.toml: {version}")
    return version + ".0"  # Store reserves the fourth segment; it must be 0


def render_manifest(identity_name: str, publisher: str,
                    publisher_display_name: str, version: str) -> None:
    template = MANIFEST_TEMPLATE.read_text(encoding="utf-8")
    manifest = (
        template
        .replace("__IDENTITY_NAME__", identity_name)
        .replace("__PUBLISHER__", publisher)
        .replace("__PUBLISHER_DISPLAY_NAME__", publisher_display_name)
        .replace("__VERSION__", version)
    )
    if "__" in manifest:
        raise SystemExit("error: unresolved placeholder left in manifest")
    (LAYOUT_DIR / "AppxManifest.xml").write_text(manifest, encoding="utf-8")


def generate_visual_assets() -> None:
    from PIL import Image

    source = Image.open(ICON_SOURCE).convert("RGBA")
    assets_dir = LAYOUT_DIR / "Assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    for name, width, height, pad in VISUAL_ASSETS:
        if pad:
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            icon = source.copy()
            icon.thumbnail((width, height), Image.LANCZOS)
            offset = ((width - icon.width) // 2, (height - icon.height) // 2)
            canvas.paste(icon, offset, icon)
            out = canvas
        else:
            out = source.resize((width, height), Image.LANCZOS)
        out.save(assets_dir / name)


def copy_fragments() -> None:
    """Copy the packaged Public/ tree into the layout and emit the Windows
    Terminal fragment icon beside the JSON.

    The manifest declares the com.microsoft.windows.terminal.settings app
    extension with PublicFolder="Public"; WT reads Public\\Fragments\\*.json
    directly (no subdirectories). A bare icon name resolves against the
    fragment file's own directory on WT >= 1.24.
    """
    if not FRAGMENTS_SRC.is_dir():
        return
    dest = LAYOUT_DIR / "Public"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FRAGMENTS_SRC, dest)

    from PIL import Image

    icon = Image.open(ICON_SOURCE).convert("RGBA").resize((64, 64), Image.LANCZOS)
    fragments_dir = dest / "Fragments"
    fragments_dir.mkdir(parents=True, exist_ok=True)
    icon.save(fragments_dir / "niubash.png")


def copy_payload(args: argparse.Namespace) -> None:
    shutil.copy2(args.niu_exe, LAYOUT_DIR / "niu.exe")

    if args.winuxcmd_dir:
        dest = LAYOUT_DIR / "winuxcmd"
        if dest.exists():
            shutil.rmtree(dest)
        # The package root is read-only, so shipped link directories are dead
        # weight: the applet links materialize into the package's private
        # LocalCache at first launch from the multicall's own applet table.
        # bin/ (PATH-entry links) and usr/bin/ (applet links) hold nothing
        # but hardlinks to winuxcmd.exe / forwarders — copying them would
        # balloon the MSIX with hundreds of expanded copies of the same
        # payload (makeappx has no hardlink notion). Everything that IS data
        # (etc/, share/, Library/, opt/ wpm payloads, the multicall itself)
        # ships. Deleted AFTER the copy: ignore_patterns would match any
        # depth, and wpm payloads may legitimately carry their own bin/.
        shutil.copytree(args.winuxcmd_dir, dest,
                        ignore=shutil.ignore_patterns(
                            "*.pdb", "*.lib", "*.exp",
                            "tmp", "home",
                            "__pycache__"))
        for link_dir in ("bin", "usr/bin"):
            link_path = dest / link_dir
            if link_path.exists():
                shutil.rmtree(link_path)

    if args.bundle_dir:
        dest = LAYOUT_DIR / "bundle"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(args.bundle_dir, dest)

    (LAYOUT_DIR / "README.txt").write_text(WELCOME, encoding="utf-8")


def find_makeappx() -> str:
    found = shutil.which("makeappx")
    if found:
        return found

    sdk_dir = os.environ.get("WindowsSdkDir")
    sdk_version = os.environ.get("WindowsSDKVersion", "").rstrip("\\")
    if sdk_dir and sdk_version:
        candidate = Path(sdk_dir) / "bin" / sdk_version / "x64" / "makeappx.exe"
        if candidate.exists():
            return str(candidate)

    # Search every fixed drive root for a Windows Kits installation.
    candidates = []
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        pattern = f"{drive}:/Windows Kits/10/bin/*/x64/makeappx.exe"
        candidates.extend(glob.glob(pattern))
    if candidates:
        return sorted(candidates)[-1]  # newest SDK version wins

    raise SystemExit(
        "error: makeappx.exe not found. Install the Windows SDK "
        "(https://developer.microsoft.com/windows/downloads/windows-sdk/) "
        "or add its bin/x64 directory to PATH."
    )


def pack(version: str, makeappx: str) -> Path:
    output = OUTPUT_DIR / f"Niubash_{version}_x64.msix"
    subprocess.run(
        [makeappx, "pack", "/d", str(LAYOUT_DIR), "/p", str(output), "/o"],
        check=True,
    )
    return output


def resolve_identity(args: argparse.Namespace) -> tuple[str, str, str]:
    """Identity triple: explicit flags win, else scripts/release/msix-identity.json,
    else a clearly-marked preview placeholder. The Store re-signs the uploaded
    package under the Partner Center identity, so a placeholder build is fine
    for container verification but must never be submitted."""
    if args.identity_name and args.publisher:
        return args.identity_name, args.publisher, args.publisher_display_name
    pin_path = REPO_ROOT / "scripts" / "release" / "msix-identity.json"
    if pin_path.exists():
        import json

        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        return (args.identity_name or pin["identity_name"],
                args.publisher or pin["publisher"],
                args.publisher_display_name or pin.get("publisher_display_name",
                                                        "unixwin"))
    if args.identity_name or args.publisher:
        missing = "publisher" if args.identity_name else "identity-name"
        raise SystemExit(f"error: --{missing} is required when only one identity "
                         "flag is given (or provide scripts/release/msix-identity.json)")
    return ("NiubashPreview", "CN=00000000-0000-0000-0000-000000000000", "unixwin")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--identity-name", default=None,
                        help="Package/Identity Name from Partner Center "
                             "(default: scripts/release/msix-identity.json, else a preview placeholder)")
    parser.add_argument("--publisher", default=None,
                        help='Publisher subject, e.g. "CN=XXXXXXXX-..." (default: pin file)')
    parser.add_argument("--publisher-display-name", default=None,
                        help="Publisher display name shown in the Store (default: pin file)")
    parser.add_argument("--niu-exe", type=Path,
                        default=REPO_ROOT / "target" / "release" / "niu.exe",
                        help="Release niu.exe to package (default: target/release/niu.exe)")
    parser.add_argument("--winuxcmd-dir", type=Path, default=None,
                        help="Directory with winuxcmd.exe + command links to bundle")
    parser.add_argument("--bundle-dir", type=Path, default=None,
                        help="Extra payload directory copied into the package")
    args = parser.parse_args()

    if not args.niu_exe.exists():
        raise SystemExit(f"error: {args.niu_exe} not found; build first with "
                         "scripts/build.py or scripts/build-with-vs.ps1")

    if LAYOUT_DIR.exists():
        shutil.rmtree(LAYOUT_DIR)
    LAYOUT_DIR.mkdir(parents=True)

    version = read_version()
    identity_name, publisher, publisher_display_name = resolve_identity(args)
    if identity_name == "NiubashPreview":
        print("NOTE: packing with the PREVIEW placeholder identity — fine for "
              "container verification, NOT submittable to the Store (set "
              "scripts/release/msix-identity.json to the Partner Center values).")
    render_manifest(identity_name, publisher,
                    publisher_display_name, version)
    generate_visual_assets()
    copy_fragments()
    copy_payload(args)
    output = pack(version, find_makeappx())
    print(f"packed: {output}")


if __name__ == "__main__":
    main()
