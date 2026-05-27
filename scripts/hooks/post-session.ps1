# Meridian post-session hook for Claude Code (Windows PowerShell).
# Installed to $HOME\.claude\hooks\post-session.ps1.
#
# Reads the local Claude Code transcript and POSTs its content to the
# Meridian receiver. The receiver lives on a remote VM and cannot read
# this machine's filesystem, so we send the JSONL inline.
#
# Fails silently — never interrupt a Claude Code session.
#
# Required env (set in your shell profile, NOT here):
#   MERIDIAN_RECEIVER_URL    e.g. https://meridian.markahope.com
#   MERIDIAN_RECEIVER_TOKEN  receiver bearer token

$ErrorActionPreference = 'SilentlyContinue'

try {
    $raw = [Console]::In.ReadToEnd()
    if (-not $raw) { exit 0 }
    $hookData = $raw | ConvertFrom-Json
    $transcriptPath = $hookData.transcript_path

    if (-not $transcriptPath) { exit 0 }
    if (-not $env:MERIDIAN_RECEIVER_URL) { exit 0 }
    if (-not $env:MERIDIAN_RECEIVER_TOKEN) { exit 0 }
    if (-not (Test-Path -LiteralPath $transcriptPath)) { exit 0 }

    $content = Get-Content -Raw -LiteralPath $transcriptPath -Encoding UTF8
    if (-not $content) { exit 0 }

    $body = @{
        transcript_path  = $transcriptPath
        transcript_jsonl = $content
    } | ConvertTo-Json -Compress -Depth 6

    $headers = @{ Authorization = "Bearer $($env:MERIDIAN_RECEIVER_TOKEN)" }
    $uri = "$($env:MERIDIAN_RECEIVER_URL)/capture/claude-session"

    Invoke-RestMethod -Uri $uri -Method Post -Headers $headers `
        -ContentType 'application/json; charset=utf-8' `
        -Body $body -TimeoutSec 60 | Out-Null
} catch {
    # Silent — never break the session
}

exit 0
