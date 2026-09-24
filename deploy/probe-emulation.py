#!/usr/bin/env python3
"""Local administrative probe; never print the API key or account credentials."""
import argparse
import json
import time
from pathlib import Path
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["status", "start", "stop"])
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
env = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines()
           if line and not line.startswith("#") and "=" in line)
base = "http://127.0.0.1:" + env.get("PORT", "8787")

def request(path, method="GET", timeout=240):
    req = urllib.request.Request(base + path, method=method,
        data=b"{}" if method == "POST" else None,
        headers={"Authorization": "Bearer " + env["VM2API_API_KEY"],
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {"http_status": error.code, "body": json.load(error)}

# These paths contain only health/runtime fields, never export the entire VM.
def sanitize(value):
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()
                if not any(word in key.lower() for word in
                           ("token", "secret", "password", "credential", "cookie", "api_key"))}
    return value

deadline = time.monotonic() + 120
while True:
    try:
        health = request("/health", timeout=2)
        if health.get("http_status"):
            raise urllib.error.URLError("Health endpoint unavailable")
        break
    except (urllib.error.URLError, TimeoutError):
        if time.monotonic() >= deadline:
            raise SystemExit("Control plane did not become ready within 120 seconds")
        time.sleep(2)

if args.action != "status":
    result = request("/api/panel/vms/vm-01/" + args.action, "POST")
    detail = result.get("data", {})
    action_summary = {"action": args.action, "ok": result.get("ok"),
                      "error": result.get("error"),
                      "boot": detail.get("boot"), "halt": detail.get("halt")}
    print(json.dumps(sanitize(action_summary), ensure_ascii=False))
    if result.get("ok") is not True:
        raise SystemExit(1)
result = request("/api/panel/vms/vm-01")
if result.get("ok") is not True:
    print(json.dumps(sanitize(result), ensure_ascii=False))
    raise SystemExit(1)
data = result.get("data", result)
vm = data.get("vm", data)
summary = {key: vm.get(key) for key in
    ("id", "status", "state", "runtime", "kernel", "error", "schedulable",
     "schedule_disabled_reason")}
summary["kernel_detail"] = data.get("kernel")
print(json.dumps(sanitize(summary), ensure_ascii=False))
