"""Guards for the Windows install scripts.

These encode two things that broke in practice and are invisible in review:

1. ``setup-client.ps1`` was committed as UTF-8 **without** a BOM. PowerShell 5.1
   then reads .ps1 files using the system ANSI code page (GBK on a Chinese
   install), so every Chinese string became mojibake and the file failed to
   *parse* — the user saw a syntax error, not a mojibake clue. Editors and code
   generators routinely strip the BOM, so this needs a test rather than care.
2. ``cmd.exe`` is confused by a BOM (it would treat the bytes as part of the
   first command) and is unreliable with LF-only line endings.

Together these are why "double-click setup-client.cmd" is the supported entry
point: Windows never associates .ps1 with PowerShell, and the default execution
policy blocks unsigned scripts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PS1 = SCRIPTS / "setup-client.ps1"
CMD = SCRIPTS / "setup-client.cmd"
SH = SCRIPTS / "setup-client.sh"

BOM = b"\xef\xbb\xbf"


def _find_bash() -> str | None:
    """Locate a POSIX shell that can actually parse the installer.

    On Windows ``shutil.which("bash")`` finds ``C:\\Windows\\System32\\bash.exe``,
    which is the WSL launcher — it cannot see a ``D:\\`` path, so it "fails" the
    script for reasons that have nothing to do with the script. Prefer Git Bash
    and skip when neither is present.
    """
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(candidate).is_file():
                return candidate
        return None
    return shutil.which("bash")


def test_scripts_exist():
    assert PS1.is_file(), f"missing {PS1}"
    assert CMD.is_file(), f"missing {CMD}"
    assert SH.is_file(), f"missing {SH}"


def test_powershell_script_has_a_utf8_bom():
    """Without it, PowerShell 5.1 reads the file as ANSI and cannot parse it."""
    assert PS1.read_bytes().startswith(BOM), (
        "scripts/setup-client.ps1 must start with a UTF-8 BOM. Without it "
        "PowerShell 5.1 decodes the file using the system ANSI code page, which "
        "corrupts the Chinese output and produces a syntax error."
    )


def test_powershell_script_contains_non_ascii():
    """A BOM only matters because the file is not pure ASCII — assert both."""
    text = PS1.read_text(encoding="utf-8-sig")
    assert any(ord(ch) > 127 for ch in text)


def test_batch_launcher_has_no_bom():
    """cmd.exe would read the BOM bytes as part of the first command."""
    assert not CMD.read_bytes().startswith(BOM)


def test_batch_launcher_uses_crlf():
    raw = CMD.read_bytes()
    bare_lf = sum(1 for i, b in enumerate(raw) if b == 0x0A and (i == 0 or raw[i - 1] != 0x0D))
    assert bare_lf == 0, f"{bare_lf} bare LF line endings; cmd.exe wants CRLF"


def test_batch_launcher_is_ascii_only():
    """cmd.exe reads batch files in the OEM code page; inline CJK would corrupt."""
    raw = CMD.read_bytes()
    assert all(b < 0x80 for b in raw), "setup-client.cmd must stay ASCII-only"


def test_batch_launcher_bypasses_the_execution_policy():
    text = CMD.read_text(encoding="ascii")
    assert "-ExecutionPolicy Bypass" in text, (
        "the launcher exists to sidestep the default policy; without Bypass it "
        "fails on a stock Windows install"
    )
    assert "-File" in text and "setup-client.ps1" in text


def test_batch_launcher_pauses_so_a_double_click_can_be_read():
    assert "pause" in CMD.read_text(encoding="ascii").lower()


@pytest.mark.skipif(_find_bash() is None, reason="no usable POSIX shell")
def test_shell_script_parses():
    bash = _find_bash()
    assert bash is not None
    result = subprocess.run(
        [bash, "-n", str(SH)],
        capture_output=True,
        # Never let the platform locale pick the decoder: on a Chinese Windows box
        # it defaults to GBK and crashes on the script's own UTF-8 diagnostics.
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_shell_script_is_utf8_without_bom():
    """bash chokes on a BOM in a shebang line."""
    assert not SH.read_bytes().startswith(BOM)
    SH.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [PS1, SH])
def test_installers_fall_back_beyond_pypi(path: Path):
    """The packages are not on PyPI yet.

    Asserting this keeps a future edit from reinstating a single
    `pip install shadowscribe-client`, which currently fails with
    "No matching distribution found" and strands the user.
    """
    text = path.read_text(encoding="utf-8-sig")
    assert "github.com/yw1103/ShadowScribe" in text, "no GitHub fallback"
    assert "client" in text, "no local-checkout fallback"
