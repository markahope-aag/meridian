# Meridian — New Machine Setup

## Quick Start

```bash
git clone https://github.com/markahope-aag/meridian.git
cd meridian
bash scripts/setup-machine.sh
```

The script handles everything: CLI install, Syncthing install + pairing, SSH key setup, Claude Code hooks.

## What the Script Does

1. **Collects credentials** — receiver URL, receiver token, Coolify API token
2. **Creates `~/.meridian/config.yaml`** — receiver URL and token for the CLI
3. **Creates `~/.meridian/.env`** — Coolify API token and infrastructure credentials
4. **Fetches Coolify SSH key** — downloads the server's private key from Coolify API for SSH access
5. **Installs Syncthing** — via winget (Windows) or brew (macOS)
6. **Pairs Syncthing with VM** — SSHes into the VM, adds this machine's device, restarts Syncthing
7. **Installs meridian CLI** — `pip install -e ./cli`
8. **Installs Claude Code post-session hook**
9. **Adds local SSH key to VM** — so future SSH works without the Coolify key

## Prerequisites

- Python 3.10+
- pip
- jq (for merging Claude Code settings)
- curl

## Manual Syncthing Steps (after script completes)

The script pairs the devices automatically, but you still need to:

1. Open `http://localhost:8384` in your browser
2. Add the VM as a remote device — device ID will be printed by the script
3. Accept the **meridian** folder when prompted
4. Set folder path to:
   - **Windows:** `C:\Users\markh\Meridian`
   - **macOS:** `~/Meridian`

## Open in Obsidian (optional)

1. Open Obsidian
2. Manage Vaults → **Open folder as vault** → select the Meridian folder above

## Verify

```bash
meridian status          # should show healthy
```

## Infrastructure Reference

| Resource | Location |
|---|---|
| VM IP | 178.156.209.202 |
| Coolify dashboard | https://app.coolify.io |
| Receiver | https://meridian.markahope.com |
| n8n | https://auto.asymmetric.pro |
| Credentials | `~/.meridian/.env` |
| Coolify SSH key | `~/.meridian/coolify_rsa` |
| CLI config | `~/.meridian/config.yaml` |

## Troubleshooting

### SSH fails to VM
Re-fetch the Coolify key:
```bash
source ~/.meridian/.env
curl -s -H "Authorization: Bearer $COOLIFY_API_TOKEN" -H "Accept: application/json" \
  "https://app.coolify.io/api/v1/security/keys/qo40kgg4wggkso44w44gkc8k" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['private_key'])" > ~/.meridian/coolify_rsa
chmod 600 ~/.meridian/coolify_rsa
```

### Syncthing shows "Disconnected"
- Ensure Syncthing is running: `C:\Users\markh\syncthing.exe` or `Start-Process ... -WindowStyle Hidden`
- Check VM pairing: `ssh -i ~/.meridian/coolify_rsa root@178.156.209.202 "syncthing cli config devices list"`

### Coolify web terminal
Do NOT use it for long commands — it wraps lines and breaks them. Always use SSH directly.
