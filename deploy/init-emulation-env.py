#!/usr/bin/env python3
"""Create private credentials for the isolated amd64 emulation deployment."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
values = {
    "VM2API_ADMIN_USER": "admin",
    "VM2API_ADMIN_PASSWORD": secrets.token_urlsafe(24),
    "VM2API_API_KEY": secrets.token_hex(32),
    "VM2API_DB_SECRET": secrets.token_hex(32),
    "VM2API_CONTAINER_NAME": "vm2api-arm-experiment",
    "VM2API_BIND_HOST": "127.0.0.1",
    "PORT": "8787",
}
try:
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit("Existing .env preserved; review it before starting.")
with os.fdopen(fd, "w") as stream:
    stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
print(f"Created {env_file} with mode 0600. Credentials are not printed.")
