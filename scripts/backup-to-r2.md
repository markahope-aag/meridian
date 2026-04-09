# Meridian backup — Cloudflare R2

Nightly full mirror of `/meridian` on the Hetzner VM to a Cloudflare R2 bucket.
R2 is a good fit because egress is free (painless restores), storage is cheap
(~$0.015/GB/month), and object versioning gives a point-in-time history so a
silent corruption can be rolled back.

## Architecture

```
/meridian/  ──(rclone sync, nightly 03:00 UTC)──▶  r2://<bucket>
   ^                                                    │
   │                                                    │
   └───────────(rclone copy, on demand)─────────────────┘
                 scripts/restore-from-r2.sh
```

- `scripts/backup-to-r2.sh` — run nightly by cron/systemd
- `scripts/restore-from-r2.sh` — manual disaster recovery + quarterly drill
- `/root/.meridian-backup.env` — non-committed config (bucket name)
- Rclone remote: `meridian-r2` (S3-compatible, Cloudflare provider)

## One-time setup

### 1. In the Cloudflare dashboard

1. Sign in to Cloudflare → **R2 Object Storage**.
2. Create a bucket: **`meridian-backup`** (or any name — match the env file later).
   - Region: automatic / nearest (default).
3. On the bucket settings page, enable **Object Versioning**. This is critical:
   without it, a local `rm` that propagates through the sync is permanent.
4. Under **R2 → Manage R2 API Tokens**, create a token:
   - **Permissions:** Object Read & Write
   - **Specify bucket:** `meridian-backup` only (least privilege)
   - **TTL:** No expiry (or 1 year, with a calendar reminder to rotate)
5. Save the **Access Key ID**, **Secret Access Key**, and the **jurisdictional
   S3 endpoint** (looks like `https://<accountid>.r2.cloudflarestorage.com`).

### 2. On the VM (178.156.209.202)

```bash
# Install rclone if not already present
curl https://rclone.org/install.sh | sudo bash

# Configure the remote (interactive)
rclone config create meridian-r2 s3 \
    provider=Cloudflare \
    access_key_id=<ACCESS_KEY_ID> \
    secret_access_key=<SECRET_ACCESS_KEY> \
    endpoint=https://<ACCOUNT_ID>.r2.cloudflarestorage.com \
    acl=private

# Smoke test — should list the (empty) bucket
rclone lsd meridian-r2:meridian-backup

# Runtime config
cp /meridian/scripts/backup-to-r2.env.example /root/.meridian-backup.env
# edit /root/.meridian-backup.env — set MERIDIAN_BACKUP_BUCKET
chmod 600 /root/.meridian-backup.env

# First manual run to seed the bucket
bash /meridian/scripts/backup-to-r2.sh
tail -f /var/log/meridian-backup/backup-$(date -u +%Y-%m-%d).log
```

### 3. Schedule it

Pick one — either works. I use cron because it's simpler.

**Cron:**

```bash
crontab -e
# Add:
0 3 * * *  /bin/bash /meridian/scripts/backup-to-r2.sh
```

**Systemd timer** (equivalent, easier to inspect):

```ini
# /etc/systemd/system/meridian-backup.service
[Unit]
Description=Meridian nightly R2 backup
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /meridian/scripts/backup-to-r2.sh
```

```ini
# /etc/systemd/system/meridian-backup.timer
[Unit]
Description=Run Meridian backup nightly

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now meridian-backup.timer
sudo systemctl list-timers meridian-backup
```

## Verification

After the first run:

```bash
# Bucket side — confirm the marker file is recent
rclone cat meridian-r2:meridian-backup/_backup-status.yaml

# Bucket size — sanity check
rclone size meridian-r2:meridian-backup

# Local log
ls -lh /var/log/meridian-backup/
```

The dashboard `brain.markahope.com` doesn't know about the backup — that's
intentional. Backup health lives in logs + the marker object, not in the app.

## Quarterly restore drill

Calendar a reminder every 3 months. The procedure:

```bash
# 1. Dry-run listing
bash /meridian/scripts/restore-from-r2.sh

# 2. Actual restore into a scratch directory
bash /meridian/scripts/restore-from-r2.sh --confirm --target /tmp/meridian-restore-test

# 3. Spot-check integrity
diff -rq /meridian/wiki /tmp/meridian-restore-test/wiki | head
ls /tmp/meridian-restore-test/raw | wc -l     # should roughly match /meridian/raw
cat /tmp/meridian-restore-test/_backup-status.yaml
find /tmp/meridian-restore-test/wiki -name '*.md' | wc -l
find /meridian/wiki -name '*.md' | wc -l       # counts should match

# 4. Clean up
rm -rf /tmp/meridian-restore-test
```

A backup you have never restored from is not a backup.

## Full disaster recovery

If the VM disk is dead and you're rebuilding on a fresh instance:

1. Provision a new VM with the same volume mount path (`/meridian`).
2. Install rclone and re-create the `meridian-r2` remote with the saved token.
3. Run `bash scripts/restore-from-r2.sh --confirm --target /meridian`.
4. Restart the receiver and dashboard containers (Coolify will rebuild them
   from the `april-2026-rebuild` branch automatically once they start).
5. Smoke-test: `curl https://meridian.markahope.com/health`, open
   `https://brain.markahope.com`, trigger `/distill` and `/synthesize/queue`.

## Versioning & retention

- Object versioning **must be enabled** on the bucket. Without it, a local
  corruption that propagates through the nightly sync is permanent.
- R2 versioning charges for the old versions at the same rate as live
  objects. At Meridian's scale that's cents per month.
- If storage ever grows past the point where versioning is expensive, add
  a lifecycle rule that expires non-current versions older than 90 days.

## Cost sanity check (April 2026 pricing)

| Item | Unit cost | Expected monthly cost |
|---|---|---|
| R2 storage | $0.015 / GB-month | $0.00 – $0.05 for years |
| R2 Class A ops (writes) | $4.50 / million | Hundreds of thousands of writes = cents |
| R2 Class B ops (reads) | $0.36 / million | Effectively zero for backup-only use |
| Egress | Free | — |

The backup will pay for itself the first time it saves a single hour of work.

## What's NOT backed up

Intentional exclusions (see `EXCLUDES` in `backup-to-r2.sh`):

- `__pycache__/` — regenerated on import
- `.git/` — the code lives on GitHub
- `.cache/` — tool caches
- `*.pyc`, `*.tmp` — transient

Everything else under `/meridian` is fair game, including `capture/`, `raw/`,
`wiki/`, `config.yaml`, `clients.yaml`, `topics.yaml`, `synthesis_queue.json`,
`wiki/log.md`, and `prompts/`.
