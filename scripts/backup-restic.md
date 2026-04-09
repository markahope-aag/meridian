# Meridian backup — Restic → Cloudflare R2

Nightly snapshot backup of `/meridian` on the Hetzner VM to a Cloudflare R2
bucket, managed by [Restic](https://restic.net/).

Why Restic instead of a plain rclone mirror:

- **Snapshots.** Every backup is an atomic point-in-time view you can restore
  from individually. R2 doesn't implement S3-style object versioning, so a
  plain rclone sync has no history layer — an accidental delete or silent
  corruption propagates through the mirror with no recovery path.
- **Deduplication.** The first full backup of `/meridian` compressed 69 MiB of
  source to 23 MiB in the repo — roughly 67% savings. Subsequent nightly
  snapshots only store the deltas, typically a few hundred KB.
- **Encryption.** Client-side encryption with a local key. R2 never sees
  plaintext. Losing the key = losing the backup — there is no recovery
  mechanism.
- **Integrity checks.** `restic check` verifies the repo on every run.

## Architecture

```
/meridian/  ──(restic backup, nightly 03:00 UTC)──▶  r2:meridian/restic (encrypted)
   ^                                                         │
   │                                                         │
   └───────(restic restore <snapshot-id>, on demand)─────────┘
             scripts/restore-restic.sh
```

- `scripts/backup-restic.sh` — nightly snapshot + prune + integrity check
- `scripts/restore-restic.sh` — dry-run by default, `--confirm` to restore
- `/root/.meridian-backup.env` — runtime config with credentials and repo key
- Repo URL: `s3:https://<account>.r2.cloudflarestorage.com/meridian/restic`

## One-time setup

### 1. In Cloudflare

1. **R2 Object Storage** → create a bucket (e.g. `meridian`).
2. Note the **S3 API endpoint** URL shown on the bucket overview.
3. **R2 → Manage R2 API Tokens → Create API Token**
   - Permissions: **Object Read & Write**
   - Specify bucket: scope to the one bucket only
   - TTL: no expiry (or 1 year with a rotation reminder)
   - Save the **Access Key ID** and **Secret Access Key** immediately —
     the secret is shown only once.

### 2. On the VM

```bash
# Install restic
apt-get install -y restic

# Generate a strong repo encryption key — store this in your password manager
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Drop the runtime config
cp /meridian/scripts/backup-restic.env.example /root/.meridian-backup.env
# Edit /root/.meridian-backup.env — fill in RESTIC_REPOSITORY, RESTIC_PASSWORD,
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
chmod 600 /root/.meridian-backup.env

# Initialize the Restic repo in the R2 bucket
source /root/.meridian-backup.env && restic init

# Seed run
/bin/bash /meridian/scripts/backup-restic.sh
tail -n 40 /var/log/meridian-backup/backup-$(date -u +%Y-%m-%d).log
```

### 3. Schedule it

**Cron (simplest):**

```bash
crontab -e
# Add:
0 3 * * *  /bin/bash /meridian/scripts/backup-restic.sh
```

**Systemd timer (equivalent, easier to inspect):**

```ini
# /etc/systemd/system/meridian-backup.service
[Unit]
Description=Meridian nightly Restic backup
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /meridian/scripts/backup-restic.sh
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
```

## Retention

The backup script runs `restic forget` with:

- 7 daily snapshots
- 4 weekly snapshots
- 12 monthly snapshots

Plus `--prune`, which removes blobs no longer referenced by any snapshot. In
steady state the repo size tracks roughly the size of the content plus the
delta accumulated across retained snapshots — well under 1 GB for the
foreseeable future.

Adjust retention by editing the `restic forget` flags in
`scripts/backup-restic.sh`.

## Routine ops

```bash
# Load credentials once per shell
source /root/.meridian-backup.env

# List all snapshots
restic snapshots --compact

# Diff two snapshots (useful for "what changed yesterday?")
restic diff <older-id> <newer-id>

# Browse a snapshot without restoring
restic ls <snapshot-id> /meridian/wiki/knowledge/seo

# Restore a single file from a specific snapshot
restic restore <snapshot-id> --target /tmp/restore --include /meridian/wiki/knowledge/seo/index.md

# Check repo integrity (metadata only)
restic check

# Full integrity check (reads every pack file — slow, run quarterly)
restic check --read-data
```

## Quarterly restore drill

A backup you have never restored from is not a backup. Every quarter:

```bash
# 1. Dry-run listing (shows available snapshots + top of latest)
bash /meridian/scripts/restore-restic.sh

# 2. Actual restore into a scratch dir
bash /meridian/scripts/restore-restic.sh --confirm --target /tmp/meridian-restore-drill

# 3. Spot-check integrity
find /tmp/meridian-restore-drill/meridian/wiki -name '*.md' | wc -l
find /meridian/wiki -name '*.md' | wc -l       # should match within a few
diff -q /meridian/wiki/log.md /tmp/meridian-restore-drill/meridian/wiki/log.md || true

# 4. Full pack verification (slow — reads every object back from R2)
source /root/.meridian-backup.env && restic check --read-data

# 5. Clean up
rm -rf /tmp/meridian-restore-drill
```

## Full disaster recovery

If the VM disk is dead and you are rebuilding on a fresh instance:

1. Provision a new VM with the same volume mount path.
2. Install restic and recreate `/root/.meridian-backup.env` with the same
   `RESTIC_PASSWORD` (from your password manager) and `AWS_*` credentials.
3. `source /root/.meridian-backup.env && restic snapshots` — confirm the repo
   is reachable and the snapshot list is intact.
4. `bash scripts/restore-restic.sh --confirm --target /meridian.restore`
5. `mv /meridian /meridian.old && mv /meridian.restore/meridian /meridian`
6. Restart the receiver and dashboard containers.
7. Smoke-test: `curl https://meridian.markahope.com/health`, open
   `https://brain.markahope.com`, trigger `/synthesize/queue`.

## Cost sanity check (April 2026 pricing)

| Item | Unit cost | Expected monthly cost |
|---|---|---|
| R2 storage | $0.015 / GB-month | **< $0.05** for years (dedup keeps it tiny) |
| R2 Class A ops | $4.50 / million | cents |
| R2 Class B ops | $0.36 / million | cents |
| Egress | Free | — |

A 5 GB working tree with 90 days of daily snapshots typically occupies
1–2 GB in the Restic repo thanks to deduplication and compression.

## What's NOT backed up

Intentional exclusions (see `EXCLUDES` in `backup-restic.sh`):

- `__pycache__`, `*.pyc` — Python bytecode
- `.cache` — tool caches
- `.git` — code lives on GitHub
- `*.tmp`, `*.swp` — transients

Everything else under `/meridian` is included: `capture/`, `raw/`, `wiki/`,
`config.yaml`, `clients.yaml`, `topics.yaml`, `synthesis_queue.json`,
`prompts/`, `agents/`, `receiver/`, `web/`, and `outputs/`.

## Credential rotation

To rotate the R2 API token (do this quarterly or whenever in doubt):

1. Create a new token in Cloudflare (same permissions, same bucket scope).
2. Edit `/root/.meridian-backup.env` with the new `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY`. Keep the old one available briefly in case of
   rollback.
3. Run `source /root/.meridian-backup.env && restic snapshots` to confirm
   the new token works.
4. Delete the old token in Cloudflare.

**Do NOT rotate `RESTIC_PASSWORD`** — changing it makes existing snapshots
unreadable. Restic supports adding/removing keys with `restic key add` /
`restic key remove` if you need to rotate that independently.
