"""Deploy an allowlisted release without replacing production data directories.

Managed paths may be files or directories (the petsense/ package). Directory
swaps are staged as ``.deploy-pending`` siblings and renamed into place; the
previous tree is captured under ``previous/`` for rollback.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

MANAGED_FILES = (
    "petsense", "wsgi.py",
    "alembic", "alembic.ini",
    "models/random_forest_1s.joblib",
    "action-dashboard/index.html",
    "requirements.txt", "requirements.lock",
)
# 29MB 的动作模型不随发布包传输（跨境带宽扛不住）；发布包缺失时复用现网文件。
OPTIONAL_FILES = {"models/random_forest_1s.joblib"}
ROOT = Path("/home/ubuntu/petsense-server")
SYSTEM = Path("/etc/systemd/system/petsense.service.d")


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        temporary = target.with_name(target.name + ".deploy-pending")
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(source, temporary)
        if target.exists():
            shutil.rmtree(target)
        os.replace(temporary, target)
        return
    temporary = target.with_name(target.name + ".deploy-pending")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)


def check_health(token_file=Path("/home/ubuntu/.petsense-collection-token")):
    token = token_file.read_text().strip()
    if not token:
        raise RuntimeError("collection credential is empty")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    error = None
    for _ in range(20):
        try:
            with opener.open("http://127.0.0.1:8080/api/stats", timeout=3) as response:
                if not isinstance(json.load(response), dict):
                    raise RuntimeError("invalid stats response")
            with opener.open("http://127.0.0.1:8080/api/action/status", timeout=3) as response:
                if not json.load(response).get("enabled"):
                    raise RuntimeError("action classifier is disabled")
            try:
                opener.open("http://127.0.0.1:8080/api/collection/v1/time", timeout=3).close()
            except urllib.error.HTTPError as response:
                if response.code != 401:
                    raise
            else:
                raise RuntimeError("collection route must require authentication")
            request = urllib.request.Request(
                "http://127.0.0.1:8080/api/collection/v1/time",
                headers={"Authorization": "Bearer " + token},
            )
            with opener.open(request, timeout=3) as response:
                if not isinstance(json.load(response).get("server_us"), int):
                    raise RuntimeError("invalid authenticated collection response")
            return
        except (OSError, ValueError, RuntimeError) as failure:
            error = failure
            time.sleep(2)
    raise RuntimeError(f"deployment health check failed: {error}")


def activate(release, sha, root=ROOT, system=SYSTEM, run=subprocess.run, health=check_health):
    source = release / "server"
    for name in MANAGED_FILES:
        path = source / name
        if path.exists() and not path.is_symlink():
            continue
        # 可选文件（动作模型）不随包传输时，复用现网已有的一份
        if name in OPTIONAL_FILES and (root / name).exists():
            continue
        raise FileNotFoundError(f"missing regular release file: {name}")
    deployed = [name for name in MANAGED_FILES if (source / name).exists()]
    backup = release / "previous"
    backup.mkdir()
    existing = []
    for name in deployed:
        if (root / name).exists():
            existing.append(name)
            atomic_copy(root / name, backup / name)
    dropin = system / "90-petsense-deploy.conf"
    had_dropin = dropin.exists()
    if had_dropin:
        atomic_copy(dropin, backup / "systemd.conf")
    previous_sha = root / ".deployed-sha"
    old_sha = previous_sha.read_bytes() if previous_sha.exists() else None
    # Every operation that can change the live service is inside the rollback scope.
    try:
        for name in deployed:
            atomic_copy(source / name, root / name)
        candidate = release / "systemd.conf"
        candidate.write_text(
            "[Service]\nExecStart=\n"
            f"ExecStart={release}/.venv/bin/python {root}/wsgi.py\n"
        )
        atomic_copy(candidate, dropin)
        run(["sudo", "systemctl", "daemon-reload"], check=True)
        run(["sudo", "systemctl", "restart", "petsense"], check=True)
        health()
        marker = release / "deployed-sha"
        marker.write_text(sha + "\n")
        atomic_copy(marker, previous_sha)
    except BaseException:
        for name in deployed:
            if name in existing:
                atomic_copy(backup / name, root / name)
            else:
                target = root / name
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
        if had_dropin:
            atomic_copy(backup / "systemd.conf", dropin)
        else:
            dropin.unlink(missing_ok=True)
        if old_sha is None:
            previous_sha.unlink(missing_ok=True)
        else:
            previous_sha.write_bytes(old_sha)
        run(["sudo", "systemctl", "daemon-reload"], check=True)
        run(["sudo", "systemctl", "restart", "petsense"], check=True)
        print("Restored previous code and interpreter", flush=True)
        raise


def main():
    release = Path(sys.argv[1]).resolve()
    sha = sys.argv[2]
    with (ROOT / ".deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for binary in ("ffmpeg", "ffprobe"):
            if not shutil.which(binary):
                raise RuntimeError(f"missing system dependency: {binary}")
        if not Path("/home/ubuntu/.petsense-collection-token").read_text().strip():
            raise RuntimeError("collection credential is empty")
        subprocess.run(["python3.12", "-m", "venv", str(release / ".venv")], check=True)
        python = str(release / ".venv/bin/python")
        # 腾讯云机器默认走 PyPI 官源很慢，固定用腾讯内网镜像安装锁定依赖
        subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check",
                        "-i", "https://mirrors.cloud.tencent.com/pypi/simple",
                        "-r", str(release / "server/requirements.lock")], check=True)
        subprocess.run([python, "-m", "unittest", "discover", "-s", "server/tests", "-v"],
                       cwd=release, check=True,
                       env={**os.environ, "PETSENSE_MQTT_DISABLED": "1"})
        # 模型优先用发布包内的；包不含模型（常态）时用现网文件校验加载
        model = release / "server" / "models" / "random_forest_1s.joblib"
        if not model.exists():
            model = ROOT / "models" / "random_forest_1s.joblib"
            if not model.exists():
                raise FileNotFoundError("action model missing from both release and live root")
        subprocess.run([python, "-c", "from petsense.actions.classifier import ActionClassifier; "
                        f"ActionClassifier({str(model)!r})"],
                       cwd=release / "server", check=True)
        activate(release, sha)
        print(f"Deployed {sha}", flush=True)


if __name__ == "__main__":
    main()
