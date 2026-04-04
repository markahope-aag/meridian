# Meridian — New Machine Setup

## 1. Install Syncthing

**Windows:**
```
winget install Syncthing.Syncthing
```

**macOS:**
```
brew install syncthing
brew services start syncthing
```

Open the Syncthing web UI at `http://localhost:8384`.

## 2. Add the VM as a remote device

In Syncthing web UI → **Add Remote Device** → paste:

```
DI5EKMW-F62MMYX-OFVPLP6-JGOWUVG-YVHJODP-QWTTN7X-2MU724L-NRB44A3
```

Name it `Hetzner VM` and save.

**Important:** The VM also needs to add your device. Give your device ID (Actions → Show ID) to Claude Code and have it run:

```bash
ssh root@178.156.209.202 "syncthing cli config devices add --device-id YOUR-DEVICE-ID --name YOUR-MACHINE-NAME"
ssh root@178.156.209.202 "syncthing cli config folders meridian devices add --device-id YOUR-DEVICE-ID"
ssh root@178.156.209.202 "systemctl restart syncthing"
```

## 3. Accept the Meridian folder

After pairing, Syncthing will prompt you to accept the **Meridian** shared folder. Accept it and set the local path:

- **Windows:** `C:\Users\markh\Meridian`
- **macOS:** `~/Meridian`

## 4. Open in Obsidian

1. Open Obsidian
2. Click the vault icon (bottom-left) → **Manage Vaults**
3. **Open folder as vault** → select the Meridian folder above

## 5. Install Meridian CLI and hooks

```bash
git clone https://github.com/markahope-aag/meridian.git
cd meridian
bash scripts/setup-machine.sh
```

This installs:
- `meridian` CLI (ask, debrief, context, capture, status)
- Claude Code post-session hook
- `~/.meridian/config.yaml` with receiver URL and token

You'll be prompted for the receiver token during setup.

## Quick verification

```bash
meridian status          # should show healthy
```

In Obsidian, check `capture/` — you should see synced files within seconds.
