#!/bin/bash
# Load Meridian credentials from 1Password into the environment.
#
# Secrets live in a 1Password document, not in a plaintext file on disk.
# The only credential that has to exist locally is the 1Password service
# account token itself, which is the unavoidable bootstrap.
#
# Usage:
#   source scripts/load-secrets.sh              # export into this shell
#   scripts/load-secrets.sh --check             # verify access, export nothing
#   scripts/load-secrets.sh -- <command> [args] # run one command with secrets
#
# Configuration:
#   OP_SERVICE_ACCOUNT_TOKEN  service account token (or an `op signin` session)
#   MERIDIAN_OP_VAULT         vault holding the document
#   MERIDIAN_OP_ITEM          document title
#
# Values are piped straight from `op` into the environment. Nothing is
# written to disk, so there is no temp file to forget to delete.

VAULT="${MERIDIAN_OP_VAULT:-DevBox .env Files}"
ITEM="${MERIDIAN_OP_ITEM:-meridian.env}"

_ms_die() {
    echo "load-secrets: $1" >&2
    return 1
}

_ms_token_from_file() {
    # Fall back to the Claude credentials file so an interactive shell
    # works without the token already being exported.
    local f="$HOME/.claude/.env.credentials"
    [ -r "$f" ] || return 1
    grep -E '^OP_SERVICE_ACCOUNT_TOKEN=' "$f" | head -1 | cut -d= -f2- | tr -d "\"' \r"
}

meridian_load_secrets() {
    local check_only="${1:-}"

    command -v op >/dev/null 2>&1 || { _ms_die "1Password CLI (op) is not installed"; return 1; }

    if [ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]; then
        local tok
        tok="$(_ms_token_from_file)"
        [ -n "$tok" ] && export OP_SERVICE_ACCOUNT_TOKEN="$tok"
    fi

    local doc
    if ! doc="$(op document get "$ITEM" --vault "$VAULT" 2>&1)"; then
        _ms_die "could not read '$ITEM' from '$VAULT': ${doc%%$'\n'*}"
        return 1
    fi

    local count=0 key val line
    while IFS= read -r line; do
        # Skip comments and blanks. Read key/value by hand rather than
        # eval so a value containing spaces, quotes, or a semicolon is
        # data and never becomes shell syntax.
        case "$line" in ''|'#'*|' '*'#'*) continue ;; esac
        [ "${line#*=}" = "$line" ] && continue
        key="${line%%=*}"
        val="${line#*=}"
        key="${key%"${key##*[![:space:]]}"}"
        case "$key" in ''|*[!A-Za-z0-9_]*) continue ;; esac
        # Strip one layer of surrounding quotes if present.
        case "$val" in
            \"*\") val="${val#\"}"; val="${val%\"}" ;;
            \'*\') val="${val#\'}"; val="${val%\'}" ;;
        esac
        if [ -z "$check_only" ]; then
            export "$key=$val"
        fi
        count=$((count + 1))
    done <<< "$doc"

    if [ "$count" -eq 0 ]; then
        _ms_die "'$ITEM' contained no variables"
        return 1
    fi

    if [ -n "$check_only" ]; then
        echo "load-secrets: OK, $count variables available in '$VAULT/$ITEM'"
    else
        echo "load-secrets: loaded $count variables from '$VAULT/$ITEM'" >&2
    fi
    return 0
}

# Sourced: define the function and load immediately.
# Executed: honor --check / -- <command>.
if [ "${BASH_SOURCE[0]}" != "${0}" ]; then
    meridian_load_secrets
else
    case "${1:-}" in
        --check)
            meridian_load_secrets check
            ;;
        --)
            shift
            [ $# -gt 0 ] || { echo "load-secrets: no command given after --" >&2; exit 2; }
            meridian_load_secrets || exit 1
            exec "$@"
            ;;
        *)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
fi
