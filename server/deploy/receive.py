"""Forced SSH command: receive a main-branch server archive, then deploy it."""
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile

RELEASES = Path("/home/ubuntu/petsense-releases")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def extract_archive(stream, target):
    with tarfile.open(fileobj=stream, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > 500 or sum(member.size for member in members) > MAX_ARCHIVE_BYTES:
            raise ValueError("release archive is too large")
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] != "server" or not (member.isdir() or member.isfile())):
                raise ValueError("unsafe release archive member")
        target.mkdir(parents=True, exist_ok=True)
        archive.extractall(target, members=members, filter="data")


def main():
    match = re.fullmatch(r"deploy ([0-9a-f]{40})", os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if not match:
        raise SystemExit("Only 'deploy <40-character commit SHA>' is permitted")
    sha = match.group(1)
    RELEASES.mkdir(exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix=sha + "-", dir=RELEASES))
    with tempfile.TemporaryFile() as archive:
        count = 0
        while block := sys.stdin.buffer.read(1024 * 1024):
            count += len(block)
            if count > MAX_ARCHIVE_BYTES:
                raise ValueError("release archive is too large")
            archive.write(block)
        archive.seek(0)
        extract_archive(archive, release)
    # The remote command is supervised by systemd so a disconnected runner does
    # not interrupt activation or rollback. --wait propagates deployment failures.
    subprocess.run([
        "sudo", "systemd-run", "--wait", "--pipe", "--collect",
        "--unit=petsense-deploy-" + release.name,
        "--uid=ubuntu", "--gid=ubuntu", "--setenv=HOME=/home/ubuntu",
        "python3.12", str(release / "server/deploy/deploy_server.py"), str(release), sha,
    ], check=True)


if __name__ == "__main__":
    main()
