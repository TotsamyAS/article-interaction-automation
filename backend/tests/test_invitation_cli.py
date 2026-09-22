"""Verify the actual Docker shell entry point without issuing credentials."""
import subprocess
from pathlib import Path


def test_invitation_shell_forwards_help_without_loading_secrets():
    script = Path(__file__).resolve().parents[1] / "scripts" / "invite.sh"
    result = subprocess.run(
        ["sh", str(script), "--help"],
        cwd="/tmp",
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "--role" in result.stdout
    assert "--revoke" in result.stdout
    assert result.stderr == ""
