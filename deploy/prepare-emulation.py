#!/usr/bin/env python3
"""Prepare pinned native helpers for the experimental amd64 containers on ARM64.

Run as the normal Docker-enabled user with passwordless sudo on Ubuntu 24.04.
Installs only the x86_64 binfmt override; does not restart Docker or the host.
"""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
QEMU_IMAGE = "tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0"
DOCKER_IMAGE = "docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c"
QEMU = Path("/usr/local/libexec/vm2api/qemu-x86_64")
CONF = Path("/etc/binfmt.d/qemu-x86_64.conf")
SOURCE_CONF = ROOT / "deploy/qemu-x86_64.conf"
RUNTIME = ROOT / ".local"

def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def preserve_or_install(source, target, mode):
    if target.is_symlink():
        raise SystemExit(f"Refusing symlink target: {target}")
    if target.exists():
        if digest(source) != digest(target):
            raise SystemExit(f"Existing different file preserved: {target}")
        return
    run("sudo", "-n", "install", "-m", mode, str(source), str(target))

def extract(image, source, target):
    run("docker", "pull", "--platform", "linux/arm64", image)
    name = "vm2api-emulation-assets-" + uuid.uuid4().hex[:12]
    run("docker", "create", "--platform", "linux/arm64", "--name", name, image)
    try:
        run("docker", "cp", name + ":" + source, str(target))
    finally:
        run("docker", "container", "rm", name)

if platform.system() != "Linux" or platform.machine() != "aarch64":
    raise SystemExit("This preparation script requires a Linux ARM64 Docker host")
if not Path("/usr/lib/systemd/systemd-binfmt").exists():
    raise SystemExit("systemd-binfmt is required; this recipe was verified on Ubuntu 24.04")
run("sudo", "-n", "true")
run("docker", "info", "--format", "{{.Architecture}}")
if CONF.exists() and CONF.read_bytes() != SOURCE_CONF.read_bytes():
    raise SystemExit(f"Existing different binfmt override preserved: {CONF}")
RUNTIME.mkdir(exist_ok=True)

with tempfile.TemporaryDirectory(prefix=".vm2api-assets-", dir=ROOT) as directory:
    directory = Path(directory)
    docker = directory / "docker-arm64"
    qemu = directory / "qemu-x86_64"
    extract(DOCKER_IMAGE, "/usr/local/bin/docker", docker)
    extract(QEMU_IMAGE, "/usr/bin/qemu-x86_64", qemu)
    for binary in (docker, qemu):
        description = subprocess.check_output(["file", str(binary)], text=True)
        if "ARM aarch64" not in description or not any(
                kind in description for kind in ("statically linked", "static-pie linked")):
            raise SystemExit(f"Expected a static ARM64 executable: {description}")
    run(str(qemu), "--version")
    run("sudo", "-n", "install", "-d", "-m", "755", str(QEMU.parent))
    preserve_or_install(qemu, QEMU, "755")
    preserve_or_install(docker, RUNTIME / "docker-arm64", "755")
    preserve_or_install(SOURCE_CONF, CONF, "644")

# Disable the older distro handler, if managed by binfmt-support, so it cannot
# replace our handler at the next boot. Existing emulated processes survive.
if shutil.which("update-binfmts") and subprocess.run(
        ["update-binfmts", "--display", "qemu-x86_64"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    run("sudo", "-n", "update-binfmts", "--disable", "qemu-x86_64")
handler = Path("/proc/sys/fs/binfmt_misc/qemu-x86_64")
if handler.exists():
    run("sudo", "-n", "tee", str(handler), input="-1\n", text=True,
        stdout=subprocess.DEVNULL)
run("sudo", "-n", "/usr/lib/systemd/systemd-binfmt", str(CONF))
print(handler.read_text())
print("Prepared QEMU 10.2.3 and the native static Docker CLI. No service restart was performed.")
