#!/usr/bin/env python3
"""
hexfeed — Cross-platform installer
==================================
Single command to install everything:
    python scripts/install.py

Automatically detects your OS and package manager.
Supports Linux (apt/pacman/dnf/zypper), macOS (Homebrew), and Windows.
"""

import os
import sys
import stat
import shutil
import platform
import subprocess
import urllib.request
import zipfile
import tarfile
import tempfile
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────
HEXFEED_VERSION = "0.1.0"
GITHUB_REPO = "MCookinho/hexfeed"
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"
TARBALL_URL = f"{GITHUB_URL}/archive/refs/tags/v{HEXFEED_VERSION}.tar.gz"
TOR_WIN_URL = "https://archive.torproject.org/tor-package-archive/tobrowser/14.0/tor-win64-0.4.8.15.zip"

INSTALL_DIR = Path.home() / ".local" / "share" / "hexfeed"
BIN_DIR = {
    "linux": Path.home() / ".local" / "bin",
    "darwin": Path.home() / ".local" / "bin",
    "win32": Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "hexfeed",
}
VENV_DIR = INSTALL_DIR / ".venv"


# ─── Helpers ─────────────────────────────────────────────────────────────

def color(text, code):
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"

def green(text):   return color(text, "92")
def cyan(text):    return color(text, "96")
def yellow(text):  return color(text, "93")
def red(text):     return color(text, "91")
def dim(text):     return color(text, "90")
def bold(text):    return color(text, "1")

def header(text):
    print(f"\n{cyan('═══')} {bold(text)} {cyan('═══')}\n")

def info(text):
    print(f"  {cyan('→')} {text}")

def ok(text):
    print(f"  {green('✓')} {text}")

def warn(text):
    print(f"  {yellow('⚠')} {text}")

def fail(text):
    print(f"  {red('✗')} {text}")

def run(cmd, sudo=False, **kwargs):
    """Run a command, print it, and return CompletedProcess."""
    prefix = "sudo " if sudo else ""
    info(f"$ {prefix}{' '.join(str(a) for a in cmd)}")
    if sudo and os.name != "nt":
        cmd = ["sudo"] + list(cmd)
    return subprocess.run(cmd, **{**{"check": False}, **kwargs})

def check_program(name):
    """Check if a program exists on PATH."""
    return shutil.which(name) is not None


# ─── Platform detection ──────────────────────────────────────────────────

def get_os():
    raw = platform.system().lower()
    if raw == "linux":
        return "linux"
    if raw == "darwin":
        return "darwin"
    if raw == "windows":
        return "win32"
    sys.exit(red(f"Unsupported OS: {raw}"))

def get_package_manager():
    managers = [
        ("apt-get", "apt-get", "deb"),
        ("pacman", "pacman", "arch"),
        ("dnf", "dnf", "rpm"),
        ("zypper", "zypper", "rpm"),
        ("apk", "apk", "alpine"),
    ]
    for binary, pkg, _ in managers:
        if check_program(binary):
            return pkg
    return None

def get_distro():
    try:
        with open("/etc/os-release") as f:
            data = f.read()
        if "ID=" in data:
            for line in data.splitlines():
                if line.startswith("ID="):
                    return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return "unknown"


# ─── Tor Install ─────────────────────────────────────────────────────────

def install_tor_linux(pkg_mgr):
    pkgs = {
        "apt-get": ["tor"],
        "pacman":  ["tor"],
        "dnf":     ["tor"],
        "zypper":  ["tor"],
        "apk":     ["tor"],
    }
    cmds = {
        "apt-get": [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "-qq"]],
        "pacman":  [["pacman", "-S", "--noconfirm", "--needed"]],
        "dnf":     [["dnf", "install", "-y"]],
        "zypper":  [["zypper", "install", "-y"]],
        "apk":     [["apk", "add"]],
    }

    to_install = pkgs.get(pkg_mgr, ["tor"])
    steps = cmds.get(pkg_mgr, [["apt-get", "install", "-y"]])

    for step in steps:
        cmd = step + ([] if step[0] == "pacman" else to_install) if step[0] != pkgs[pkg_mgr][0] or True else []
        # reconstruct properly
        full_cmd = step + (to_install if step[0] not in ("apt-get",) or "update" in step else to_install)
        full_cmd = step[:]
        if "update" not in step:
            full_cmd.extend(to_install)

        # Special handling per manager
        if pkg_mgr == "apt-get":
            if "update" in step:
                run(["apt-get", "update", "-qq"], sudo=True)
            else:
                run(["apt-get", "install", "-y", "-qq"] + to_install, sudo=True)
        elif pkg_mgr == "pacman":
            run(["pacman", "-S", "--noconfirm", "--needed"] + to_install, sudo=True)
        elif pkg_mgr == "dnf":
            run(["dnf", "install", "-y"] + to_install, sudo=True)
        elif pkg_mgr == "zypper":
            run(["zypper", "install", "-y"] + to_install, sudo=True)
        elif pkg_mgr == "apk":
            run(["apk", "add"] + to_install, sudo=True)

def install_tor_macos():
    if not check_program("brew"):
        info("Installing Homebrew...")
        run(["/bin/bash", "-c",
             "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"])
    run(["brew", "install", "tor"])

def install_tor_windows(install_dir):
    info("Downloading Tor Expert Bundle...")
    zip_path = install_dir / "tor-win64.zip"
    urllib.request.urlretrieve(TOR_WIN_URL, zip_path)
    info("Extracting Tor...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(install_dir)
    zip_path.unlink()
    # Find tor.exe
    tor_exe = list(install_dir.rglob("tor.exe"))
    if tor_exe:
        tor_bin = tor_exe[0].parent
        info(f"Tor extracted to: {tor_bin}")
        return tor_bin
    return None


# ─── hexfeed install ─────────────────────────────────────────────────────

def install_hexfeed(venv_dir, project_dir=None):
    """Install hexfeed into a virtual environment."""
    info("Setting up virtual environment...")
    run([sys.executable, "-m", "venv", str(venv_dir)])

    pip = str(venv_dir / "bin" / "pip") if os.name != "nt" else str(venv_dir / "Scripts" / "pip")

    info("Upgrading pip, setuptools, wheel...")
    run([pip, "install", "--quiet", "--upgrade", "pip", "setuptools", "wheel"])

    if project_dir and (Path(project_dir) / "pyproject.toml").exists():
        info(f"Installing hexfeed from {project_dir}...")
        run([pip, "install", "--quiet", "-e", str(project_dir)])
    else:
        info("Downloading and installing hexfeed from GitHub...")
        run([pip, "install", "--quiet", f"hexfeed=={HEXFEED_VERSION}"])

    # Check binaries exist
    if os.name != "nt":
        bins = [venv_dir / "bin" / "hexfeed", venv_dir / "bin" / "hexfeed-server"]
    else:
        bins = [venv_dir / "Scripts" / "hexfeed.exe", venv_dir / "Scripts" / "hexfeed-server.exe"]

    for b in bins:
        if b.exists():
            ok(f"Binary created: {b}")
        else:
            # On Windows pip may not create exe wrappers
            if os.name == "nt":
                b = b.with_suffix(".exe") if not b.suffix else b
                if not b.exists():
                    warn(f"Binary not found: {b}")


# ─── Launchers ───────────────────────────────────────────────────────────

def create_launchers(bin_dir, venv_dir):
    """Create launcher scripts/symlinks in the user's PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        # Symlinks for Unix
        for name in ("hexfeed", "hexfeed-server"):
            src = venv_dir / "bin" / name
            dst = bin_dir / name
            if src.exists():
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                dst.symlink_to(src)
                ok(f"Symlink: {dst} → {src}")
    else:
        # Batch wrappers for Windows
        scripts_dir = venv_dir / "Scripts"
        for name in ("hexfeed", "hexfeed-server"):
            exe = scripts_dir / f"{name}.exe"
            if exe.exists():
                dst = bin_dir / f"{name}.exe"
                shutil.copy2(exe, dst)
                ok(f"Wrapper: {dst}")
            else:
                bat = bin_dir / f"{name}.cmd"
                bat.write_text(f'@"{scripts_dir / name}" %*\r\n')
                ok(f"Batch wrapper: {bat}")


# ─── PATH ────────────────────────────────────────────────────────────────

def ensure_in_path(bin_dir, auto_yes=False):
    """Check and optionally add bin_dir to PATH."""
    if str(bin_dir) in os.environ.get("PATH", ""):
        return

    def confirm(prompt):
        if auto_yes:
            return True
        try:
            ans = input(prompt).strip().lower()
            return ans in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    shell_config = None
    if os.name != "nt":
        shell = os.environ.get("SHELL", "")
        home = Path.home()
        if "zsh" in shell:
            shell_config = home / ".zshrc"
        elif "bash" in shell:
            shell_config = home / ".bashrc"
        elif "fish" in shell:
            shell_config = home / ".config" / "fish" / "config.fish"

        if shell_config and shell_config.exists():
            path_line = f'\nexport PATH="{bin_dir}:$PATH"\n'
            if path_line.strip() not in shell_config.read_text():
                print(f"\n  {yellow('⚠')} {bin_dir} is not in your PATH.")
                if confirm(f"  Add it to {shell_config}? [Y/n] "):
                    with open(shell_config, "a") as f:
                        f.write(f"\n# Added by hexfeed installer\nexport PATH=\"{bin_dir}:$PATH\"\n")
                    ok(f"Added to {shell_config}")
                    print(f"  {dim('→ Restart your shell or run:')} source {shell_config}")
        else:
            warn(f"Add {bin_dir} to your PATH manually.")
    else:
        print(f"\n  {yellow('⚠')} {bin_dir} is not in your PATH.")
        if confirm("  Add it to your User PATH? [Y/n] "):
            run(["powershell", "-Command",
                 f"[Environment]::SetEnvironmentVariable('Path', "
                 f"[Environment]::GetEnvironmentVariable('Path', 'User') + ';{bin_dir}', "
                 f"'User')"])
            ok("Added to User PATH (restart terminal to apply)")


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="hexfeed cross-platform installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/install.py                # auto-install everything\n"
            "  python scripts/install.py -y             # non-interactive (answer yes to all)\n"
            "  python scripts/install.py --no-tor        # skip Tor installation\n"
            "  python scripts/install.py --dir ~/hex     # custom directory\n"
        )
    )
    parser.add_argument("--no-tor", action="store_true", help="Skip Tor installation")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive mode (answer yes to all prompts)")
    parser.add_argument("--dir", type=Path, default=None, help="Install directory (default: ~/.local/share/hexfeed)")
    parser.add_argument("--bin-dir", type=Path, default=None, help="Binary directory (default: ~/.local/bin or LOCALAPPDATA\\Programs\\hexfeed on Windows)")
    args = parser.parse_args()

    os_name = get_os()

    print(f"\n  {green('⬡')} {bold('hexfeed')} — Cross-platform installer v{HEXFEED_VERSION}")
    print(f"  {dim('OS:')} {os_name} ({platform.machine()})")
    print()

    inst_dir = Path(args.dir) if args.dir else INSTALL_DIR
    bin_dir = Path(args.bin_dir) if args.bin_dir else BIN_DIR[os_name]
    project_dir = Path(__file__).resolve().parent.parent

    # ── Step 1: Install Tor ──
    if not args.no_tor:
        header("Installing Tor")
        if check_program("tor"):
            ok("Tor is already installed.")
        else:
            if os_name == "linux":
                pkg_mgr = get_package_manager()
                if pkg_mgr:
                    install_tor_linux(pkg_mgr)
                else:
                    warn("No known package manager found. Install Tor manually: https://torproject.org")
            elif os_name == "darwin":
                install_tor_macos()
            elif os_name == "win32":
                install_dir = inst_dir / "tor"
                install_tor_windows(install_dir)
            ok("Tor installed!")
    else:
        info("Skipping Tor installation (--no-tor).")

    # ── Step 2: Install hexfeed ──
    header("Installing hexfeed")
    install_hexfeed(inst_dir / ".venv", project_dir)

    # ── Step 3: Create launchers ──
    header("Creating launchers")
    create_launchers(bin_dir, inst_dir / ".venv")

    # ── Step 4: PATH ──
    header("PATH setup")
    ensure_in_path(bin_dir, auto_yes=args.yes)

    # ── Done ──
    print()
    print(f"  {green('✓')} {bold('hexfeed installed successfully!')}")
    print()
    print(f"  {cyan('→')} Start the server:  {bold('hexfeed-server')}")
    print(f"  {cyan('→')} Start the client:  {bold('hexfeed')}")
    print(f"  {cyan('→')} Web UI:            http://127.0.0.1:8080")
    print()
    print(f"  {dim('Installed to:')} {inst_dir}")
    print(f"  {dim('Binaries in:')} {bin_dir}")
    print()


if __name__ == "__main__":
    main()
