#!/bin/bash
# Collect system-level admin statistics and write to /meridian/state/admin-stats.json.
# Run every 15 minutes via cron so the dashboard admin page has fresh data.
#
# This runs on the HOST (not inside a container), giving it access to
# docker stats, system stats, and cron configuration that the dashboard
# container can't see directly. The output JSON is readable by the
# dashboard via its /meridian/ bind mount.

set -uo pipefail

MERIDIAN_DIR="${MERIDIAN_REPO_DIR:-/meridian}"
OUTPUT="$MERIDIAN_DIR/state/admin-stats.json"
mkdir -p "$MERIDIAN_DIR/state"

python3 - "$MERIDIAN_DIR" "$OUTPUT" << 'PYEOF'
import json, subprocess, os, shutil, sys
from datetime import datetime
from pathlib import Path

MERIDIAN = sys.argv[1]
OUTPUT = sys.argv[2]

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
    except Exception:
        return ""

# Disk
disk = shutil.disk_usage(MERIDIAN)
total_gb = round(disk.total / (1024**3), 1)
used_gb = round(disk.used / (1024**3), 1)
free_gb = round(disk.free / (1024**3), 1)
pct = round(disk.used / disk.total * 100, 1)

# Memory
mem_raw = run("free -m | awk '/Mem:/ {print $2, $3, $7}'").split()
mem_total = int(mem_raw[0]) if len(mem_raw) >= 1 else 0
mem_used = int(mem_raw[1]) if len(mem_raw) >= 2 else 0
mem_avail = int(mem_raw[2]) if len(mem_raw) >= 3 else 0

# Uptime
uptime = run("uptime -p")
load_avg = run("cat /proc/loadavg").split()[:3]

# Git
git_sha = run(f"cd {MERIDIAN} && git rev-parse HEAD")[:12]
git_branch = run(f"cd {MERIDIAN} && git branch --show-current")
git_last_msg = run(f"cd {MERIDIAN} && git log -1 --format=%s HEAD")[:80]
git_last_date = run(f"cd {MERIDIAN} && git log -1 --format=%aI HEAD")[:19]

# Docker containers
containers = []
raw = run("docker ps --format '{{.ID}}\\t{{.Image}}\\t{{.Status}}\\t{{.Names}}'")
for line in raw.split("\n"):
    parts = line.split("\t")
    if len(parts) >= 4:
        containers.append({
            "id": parts[0][:12],
            "image": parts[1].split(":")[0][-30:],
            "status": parts[2],
            "name": parts[3],
        })

# Cron entries
crons = run("crontab -l").split("\n")
cron_entries = []
for c in crons:
    c = c.strip()
    if c and not c.startswith("#"):
        parts = c.split(None, 5)
        if len(parts) >= 6:
            cron_entries.append({
                "schedule": " ".join(parts[:5]),
                "command": parts[5][:80],
            })

# n8n workflows (try to fetch, fail gracefully)
n8n_workflows = []
try:
    import urllib.request
    key_file = Path(MERIDIAN) / "state" / "n8n-api-key.txt"
    if key_file.exists():
        key = key_file.read_text().strip()
    else:
        key = ""
    if key:
        req = urllib.request.Request(
            "https://auto.asymmetric.pro/api/v1/workflows?limit=100",
            headers={
                "X-N8N-API-KEY": key,
                "User-Agent": "Mozilla/5.0 meridian-admin",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            wf_data = json.loads(r.read())
        for w in wf_data.get("data", []):
            n = w.get("name", "").lower()
            if "meridian" in n or "conceptual" in n:
                n8n_workflows.append({
                    "id": w.get("id", ""),
                    "name": w.get("name", ""),
                    "active": w.get("active", False),
                })
except Exception:
    pass

# Last restic backup
last_backup_line = run("ls -t /var/log/meridian-deploy/backup-*.log 2>/dev/null | head -1")
backup_status = "unknown"
backup_date = ""
if last_backup_line:
    backup_date = run(f"stat -c '%y' '{last_backup_line}'")[:19]
    tail = run(f"tail -5 '{last_backup_line}'")
    if "snapshot" in tail.lower() or "saved" in tail.lower():
        backup_status = "success"
    elif "error" in tail.lower() or "fatal" in tail.lower():
        backup_status = "error"
    else:
        backup_status = "completed"

# Recent deploy
last_deploy_log = run("ls -t /var/log/meridian-deploy/deploy-*.log 2>/dev/null | head -1")
last_deploy_time = ""
if last_deploy_log:
    last_deploy_time = run(f"grep 'deploy finished' '{last_deploy_log}' | tail -1 | grep -oP '\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}}'")

stats = {
    "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "system": {
        "disk_total_gb": total_gb,
        "disk_used_gb": used_gb,
        "disk_free_gb": free_gb,
        "disk_used_pct": pct,
        "mem_total_mb": mem_total,
        "mem_used_mb": mem_used,
        "mem_available_mb": mem_avail,
        "uptime": uptime,
        "load_avg": load_avg,
    },
    "git": {
        "sha": git_sha,
        "branch": git_branch,
        "last_commit_msg": git_last_msg,
        "last_commit_date": git_last_date,
    },
    "containers": containers,
    "cron_entries": cron_entries,
    "n8n_workflows": n8n_workflows,
    "backup": {
        "status": backup_status,
        "last_date": backup_date,
    },
    "deploy": {
        "last_deploy": last_deploy_time,
    },
}

with open(OUTPUT, "w") as f:
    json.dump(stats, f, indent=2)
print(json.dumps({"status": "ok", "path": OUTPUT}, indent=2))
PYEOF
