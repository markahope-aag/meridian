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
import json, re, subprocess, os, shutil, sys
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

# Health of every scheduled job, derived from its log file.
#
# Each runner writes /var/log/<dir>/<name>-<date>.log. Three things can
# go wrong and all three used to be invisible: the job errors, the job
# stops running entirely, or the collector looks in the wrong place. The
# backup check did the last one for months (it read meridian-deploy/
# while restic writes to meridian-backup/), so a dead backup and a
# healthy one produced identical output.
#
# Age is the part that matters most. A job that quietly stopped still
# leaves its last successful log behind, so "success" alone says nothing
# about whether it ran recently.

def job_health(patterns, stale_after_hours, success_markers=()):
    """Return {status, last_date, age_hours} for a scheduled job.

    status is one of: success, completed, error, stale, missing.
    """
    result = {
        "status": "missing",
        "last_date": "",
        "age_hours": None,
        "stale_after_hours": stale_after_hours,
    }
    newest = run(f"ls -t {' '.join(patterns)} 2>/dev/null | head -1")
    if not newest:
        return result

    result["last_date"] = run(f"stat -c '%y' '{newest}'")[:19]
    tail = run(f"tail -20 '{newest}'").lower()
    # Match real failures without tripping on a clean summary line. The
    # classifier ends every successful run with "Errors 0", so a bare
    # substring test for "error" would mark healthy runs as broken.
    has_error = bool(
        re.search(r"\b(fatal|traceback)\b", tail)
        or re.search(r"error:", tail)
        or re.search(r"\berrors?\s+[1-9]", tail)
    )
    if has_error:
        result["status"] = "error"
    elif not success_markers or any(m in tail for m in success_markers):
        result["status"] = "success"
    else:
        result["status"] = "completed"

    try:
        mtime = float(run(f"stat -c '%Y' '{newest}'"))
        age = round((datetime.utcnow().timestamp() - mtime) / 3600, 1)
        result["age_hours"] = age
        if age > stale_after_hours and result["status"] != "error":
            result["status"] = "stale"
    except (ValueError, TypeError):
        pass
    return result


DEPLOY_LOGS = "/var/log/meridian-deploy"

# stale_after allows one missed run of each job's own cadence.
jobs = {
    "backup": job_health(
        [f"/var/log/meridian-backup/backup-*.log", f"{DEPLOY_LOGS}/backup-*.log"],
        36, ("snapshot", "saved"),
    ),
    "clientbrain_sync": job_health([f"{DEPLOY_LOGS}/clientbrain-sync-*.log"], 36),
    # Classification runs at the end of run-git-ingest.sh, so its output
    # lands in the git-ingest log. It only writes classify-engineering-*.log
    # when the standalone runner is used, which nothing schedules.
    #
    # This entry used to point only at that standalone log and therefore
    # reported "missing" forever, while classification was in fact running
    # fine every hour. A monitor that can never go green is the same
    # permanently-red noise the depth alert was producing. Checking both
    # patterns means whichever path runs, health is reported, and a failing
    # classifier still shows up because job_health scans the log tail for
    # errors.
    "git_ingest": job_health([f"{DEPLOY_LOGS}/git-ingest-*.log"], 3),
    "classify": job_health(
        [f"{DEPLOY_LOGS}/classify-engineering-*.log", f"{DEPLOY_LOGS}/git-ingest-*.log"],
        3,
    ),
    "lint": job_health([f"{DEPLOY_LOGS}/lint-*.log"], 192),
    "evolution": job_health([f"{DEPLOY_LOGS}/evolution-*.log"], 192),
}

# Kept as a top-level key because the dashboard reads stats["backup"].
backup = jobs["backup"]
backup_status = backup["status"]
backup_date = backup["last_date"]
backup_age_hours = backup["age_hours"]
BACKUP_STALE_AFTER_HOURS = backup["stale_after_hours"]

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
        "age_hours": backup_age_hours,
        "stale_after_hours": BACKUP_STALE_AFTER_HOURS,
    },
    "jobs": jobs,
    "deploy": {
        "last_deploy": last_deploy_time,
    },
}

with open(OUTPUT, "w") as f:
    json.dump(stats, f, indent=2)
print(json.dumps({"status": "ok", "path": OUTPUT}, indent=2))
PYEOF
