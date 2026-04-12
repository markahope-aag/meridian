#!/usr/bin/env python3
"""Meridian CLI — thin HTTP client for the Meridian knowledge system.

All commands make authenticated HTTP calls to the meridian-receiver on the VM.
Config is read from ~/.meridian/config.yaml.

Usage:
    meridian ask "What is the transformer architecture?"
    meridian debrief
    meridian debrief --session abc123
    meridian context "authentication"
    meridian capture --url https://example.com/article
    meridian capture --file ./notes.md
    meridian capture --text "Quick note about something"
    meridian status
"""

import argparse
import io
import json
import sys
from pathlib import Path

import requests
import yaml

# Fix Unicode output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def load_config() -> dict:
    """Load config from ~/.meridian/config.yaml."""
    config_path = Path.home() / ".meridian" / "config.yaml"
    if not config_path.exists():
        print(
            "Error: ~/.meridian/config.yaml not found.\n"
            "Run scripts/setup-machine.sh to configure this machine,\n"
            "or create the file manually with receiver_url and token.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    if not config.get("receiver_url") or not config.get("token"):
        print("Error: receiver_url and token must be set in ~/.meridian/config.yaml", file=sys.stderr)
        sys.exit(1)

    return config


def api_call(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Make an authenticated API call to the receiver."""
    config = load_config()
    url = f"{config['receiver_url'].rstrip('/')}{endpoint}"
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
    }

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=130)
        else:
            resp = requests.post(url, headers=headers, json=data, timeout=130)
    except requests.ConnectionError:
        print(f"Error: cannot reach receiver at {config['receiver_url']}", file=sys.stderr)
        print("Is the meridian-receiver running on Coolify?", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print("Error: request timed out (>130s)", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 401:
        print("Error: unauthorized — check your token in ~/.meridian/config.yaml", file=sys.stderr)
        sys.exit(1)

    try:
        return resp.json()
    except ValueError:
        return {"status": "error", "raw": resp.text}


def cmd_ask(args):
    """Ask the knowledge base a question."""
    question = " ".join(args.question)
    result = api_call("POST", "/ask", {"question": question})
    if result.get("status") == "ok":
        print(result.get("result", ""))
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_debrief(args):
    """Debrief a Claude Code session."""
    data = {}
    if args.session:
        data["session_id"] = args.session

    result = api_call("POST", "/debrief", data)
    if result.get("status") == "ok":
        print(result.get("result", ""))
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_context(args):
    """Get a context brief on a topic."""
    topic = " ".join(args.topic)
    result = api_call("POST", "/context", {"topic": topic})
    if result.get("status") == "ok":
        print(result.get("brief", ""))
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_capture(args):
    """Capture content into the knowledge base."""
    if args.url:
        # Fetch URL content and send to receiver
        try:
            resp = requests.get(args.url, timeout=30, headers={
                "User-Agent": "Meridian/1.0"
            })
            resp.raise_for_status()
            content = resp.text
            title = args.title or args.url.split("/")[-1] or "untitled"
        except requests.RequestException as e:
            print(f"Error fetching URL: {e}", file=sys.stderr)
            sys.exit(1)

        result = api_call("POST", "/capture", {
            "title": title,
            "content": content,
            "source_url": args.url,
            "source_type": args.type or "article",
        })

    elif args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        content = filepath.read_text(encoding="utf-8", errors="replace")
        title = args.title or filepath.stem.replace("-", " ").replace("_", " ").title()

        result = api_call("POST", "/capture", {
            "title": title,
            "content": content,
            "source_type": args.type or "note",
        })

    elif args.text:
        result = api_call("POST", "/capture", {
            "title": args.title or "Quick Note",
            "content": args.text,
            "source_type": args.type or "note",
        })
    else:
        print("Error: provide --url, --file, or --text", file=sys.stderr)
        sys.exit(1)

    if result.get("status") == "ok":
        print(f"Captured: {result.get('filename', 'unknown')}")
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_lint(args):
    """Run wiki health check."""
    data = {"scope": args.scope}
    if args.dry_run:
        data["mode"] = "dry-run"

    # Start async job
    result = api_call("POST", "/lint", data)
    if result.get("status") == "accepted":
        job_id = result["job_id"]
        print(f"Lint job started: {job_id}", file=sys.stderr)

        # Poll until complete
        import time
        while True:
            time.sleep(3)
            job = api_call("GET", f"/jobs/{job_id}")
            status = job.get("status")
            if status == "completed":
                try:
                    output = json.loads(job.get("result", "{}"))
                    print(output.get("report", job.get("result", "")))
                except (json.JSONDecodeError, TypeError):
                    print(job.get("result", ""))
                return
            elif status == "failed":
                print(f"Error: {job.get('error', 'unknown')}", file=sys.stderr)
                sys.exit(1)
            else:
                print(".", end="", flush=True, file=sys.stderr)
    elif result.get("status") == "ok":
        try:
            output = json.loads(result.get("result", "{}"))
            print(output.get("report", result.get("result", "")))
        except (json.JSONDecodeError, TypeError):
            print(result.get("result", ""))
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_conceptualize(args):
    """Run a conceptual agent mode, or show Layer 4 status."""
    if args.status:
        # Status view uses the dashboard read path via /concepts/stats if
        # present, falling back to reading layer4 directly via the receiver.
        result = api_call("GET", "/concepts/stats")
        if result and result.get("status") == "ok":
            summary = result.get("summary", {})
            print("Layer 4 Conceptual Knowledge")
            print(f"  Active patterns:      {summary.get('active_patterns', 0)}")
            print(f"  Emerging hypotheses:  {summary.get('emerging', 0)}")
            print(f"  Resolved contradictions: {summary.get('contradictions_resolved', 0)}")
            print(f"  Unresolved contradictions: {summary.get('contradictions_unresolved', 0)}")
            print(f"  Drift articles:       {summary.get('drift', 0)}")
            by_conf = summary.get("by_confidence", {})
            if by_conf:
                print()
                print("  Confidence distribution:")
                for k in ("low", "medium", "high", "established"):
                    if k in by_conf:
                        print(f"    {k:13}  {by_conf[k]}")
            print()
            print("  Next scheduled runs:")
            print("    Mode A (connections):    Sunday 09:00 UTC")
            print("    Mode B (maturation):     Sunday 09:30 UTC")
            print("    Mode C (emergence):      daily 09:00 UTC")
            print("    Mode D (contradictions): 1st Sunday of month 10:00 UTC")
            return
        print("Error: could not fetch Layer 4 status from receiver", file=sys.stderr)
        sys.exit(1)

    if not args.mode:
        print("Error: --mode required (or --status)", file=sys.stderr)
        sys.exit(1)

    data = {"mode": args.mode}
    if args.dry_run:
        data["dry_run"] = True
    if args.verbose:
        data["verbose"] = True
    if args.limit is not None:
        data["limit"] = args.limit

    print(f"Conceptualize: mode={args.mode} dry_run={args.dry_run}", file=sys.stderr)
    result = api_call("POST", "/conceptualize", data)

    if result.get("status") == "accepted":
        job_id = result["job_id"]
        print(f"Conceptual agent job started: {job_id}", file=sys.stderr)
        import time
        while True:
            time.sleep(5)
            job = api_call("GET", f"/jobs/{job_id}")
            status = job.get("status")
            if status == "completed":
                try:
                    output = json.loads(job.get("result", "{}"))
                except (json.JSONDecodeError, TypeError):
                    print(job.get("result", ""))
                    return
                mode = output.get("mode", args.mode)
                print()
                print(f"=== {mode} — {'dry-run' if output.get('dry_run') else 'live'} ===")
                if mode == "connections":
                    written = output.get("written", [])
                    rejected = output.get("rejected", [])
                    print(f"  Candidates evaluated: {output.get('candidates_evaluated', 0)}")
                    print(f"  Articles written:     {len(written)}")
                    print(f"  Rejected by LLM gate: {len(rejected)}")
                    print(f"  Validation failures:  {len(output.get('validation_failures', []))}")
                    if written:
                        print()
                        print("  Written:")
                        for w in written:
                            print(f"    {w['path']}  ({w['a']} x {w['b']})")
                elif mode == "maturation":
                    print(f"  Patterns reviewed: {output.get('patterns_reviewed', 0)}")
                    print(f"  Updates applied:   {output.get('updates_applied', 0)}")
                    print(f"  Unchanged:         {output.get('unchanged', 0)}")
                elif mode == "emergence":
                    print(f"  New evidence links: {output.get('new_evidence_count', 0)}")
                    print(f"  Candidate patterns: {output.get('candidate_patterns_count', 0)}")
                    print(f"  Promoted to queue:  {output.get('promoted_to_queue', 0)}")
                elif mode == "contradictions":
                    resolved = output.get("resolved", [])
                    unresolved = output.get("unresolved", [])
                    print(f"  Candidates:  {output.get('candidates', 0)}")
                    print(f"  Resolved:    {len(resolved)}")
                    print(f"  Unresolved:  {len(unresolved)}")
                    if resolved:
                        print()
                        print("  Resolved:")
                        for r in resolved:
                            print(f"    {r['topic']} → {r['slug']}  [{r.get('frame', '?')}]")
                            if r.get("decision_rule"):
                                print(f"      rule: {r['decision_rule']}")
                return
            elif status == "failed":
                print(f"Error: {job.get('error', 'unknown')}", file=sys.stderr)
                sys.exit(1)
            else:
                print(".", end="", flush=True, file=sys.stderr)
    elif result.get("status") == "ok":
        print(result.get("result", ""))
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_synthesize(args):
    """Synthesize a topic or run the schedule."""
    if args.queue:
        result = api_call("GET", "/synthesize/queue")
        print(f"Pending:  {result.get('pending', '?')}")
        print(f"Running:  {result.get('running', '?')}")
        print(f"Complete: {result.get('complete', '?')}")
        print(f"Failed:   {result.get('failed', '?')}")
        print(f"Total:    {result.get('total', '?')}")
        next_5 = result.get("next_5", [])
        if next_5:
            print(f"\nNext up:")
            for t in next_5:
                print(f"  {t['topic']} ({t.get('fragment_count', '?')} fragments)")
        return

    if args.schedule:
        result = api_call("POST", "/synthesize/schedule", {"limit": args.limit or 5})
    elif args.topic:
        topic = " ".join(args.topic) if isinstance(args.topic, list) else args.topic
        result = api_call("POST", "/synthesize", {"topic": topic})
    else:
        print("Error: provide a topic name, --schedule, or --queue", file=sys.stderr)
        sys.exit(1)

    if result.get("status") == "accepted":
        job_id = result["job_id"]
        print(f"Synthesis job started: {job_id}", file=sys.stderr)
        import time
        while True:
            time.sleep(5)
            job = api_call("GET", f"/jobs/{job_id}")
            status = job.get("status")
            if status == "completed":
                try:
                    output = json.loads(job.get("result", "{}"))
                    if "results" in output:
                        for r in output["results"]:
                            print(f"\n{r.get('topic', '?')}: {r.get('evidence_count', '?')} evidence, "
                                  f"{r.get('claims', '?')} claims → {r.get('output_path', '?')}")
                    else:
                        print(f"\n{output.get('topic', '?')}: {output.get('evidence_count', '?')} evidence, "
                              f"{output.get('claims', '?')} claims → {output.get('output_path', '?')}")
                except (json.JSONDecodeError, TypeError):
                    print(job.get("result", ""))
                return
            elif status == "failed":
                print(f"Error: {job.get('error', 'unknown')}", file=sys.stderr)
                sys.exit(1)
            else:
                print(".", end="", flush=True, file=sys.stderr)
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    """Check receiver health."""
    config = load_config()
    print(f"Receiver: {config['receiver_url']}")
    result = api_call("GET", "/health")
    if result.get("status") == "ok":
        print(f"Status:   healthy")
        print(f"Root:     {result.get('meridian_root', '?')}")
        print(f"Capture:  {'ok' if result.get('capture_exists') else 'missing'}")
        print(f"Wiki:     {'ok' if result.get('wiki_exists') else 'missing'}")
    else:
        print(f"Status:   unhealthy")
        print(f"Error:    {result.get('error', 'unknown')}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="meridian",
        description="Meridian — personal knowledge system CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ask
    p_ask = sub.add_parser("ask", help="Ask the knowledge base a question")
    p_ask.add_argument("question", nargs="+", help="Your question")
    p_ask.set_defaults(func=cmd_ask)

    # debrief
    p_debrief = sub.add_parser("debrief", help="Debrief a Claude Code session")
    p_debrief.add_argument("--session", help="Specific session ID to debrief")
    p_debrief.set_defaults(func=cmd_debrief)

    # context
    p_context = sub.add_parser("context", help="Get a context brief on a topic")
    p_context.add_argument("topic", nargs="+", help="Topic to search for")
    p_context.set_defaults(func=cmd_context)

    # capture
    p_capture = sub.add_parser("capture", help="Capture content into the knowledge base")
    p_capture.add_argument("--url", help="URL to fetch and capture")
    p_capture.add_argument("--file", help="Local file to capture")
    p_capture.add_argument("--text", help="Raw text to capture")
    p_capture.add_argument("--title", help="Override title")
    p_capture.add_argument("--type", help="Source type override")
    p_capture.set_defaults(func=cmd_capture)

    # lint
    p_lint = sub.add_parser("lint", help="Run wiki health check")
    p_lint.add_argument("--scope", default="all",
                        choices=["contradictions", "orphans", "gaps", "all"],
                        help="Which checks to run")
    p_lint.add_argument("--dry-run", action="store_true",
                        help="Report only, no changes")
    p_lint.set_defaults(func=cmd_lint)

    # synthesize
    p_synth = sub.add_parser("synthesize", help="Synthesize Layer 3 knowledge")
    p_synth.add_argument("topic", nargs="*", help="Topic slug to synthesize")
    p_synth.add_argument("--schedule", action="store_true", help="Process next batch from queue")
    p_synth.add_argument("--queue", action="store_true", help="Show queue status")
    p_synth.add_argument("--limit", type=int, help="Max topics for --schedule")
    p_synth.set_defaults(func=cmd_synthesize)

    # conceptualize — Layer 4 conceptual agent
    p_concept = sub.add_parser(
        "conceptualize",
        help="Run the Layer 4 conceptual agent (pattern discovery + maturation + emergence + contradictions)",
    )
    p_concept.add_argument(
        "--mode",
        choices=["connections", "maturation", "emergence", "contradictions"],
        help="Which mode to run",
    )
    p_concept.add_argument("--dry-run", action="store_true",
                           help="Report only, no writes")
    p_concept.add_argument("--limit", type=int,
                           help="For Mode A: cap the number of new articles written (default 5)")
    p_concept.add_argument("--verbose", action="store_true",
                           help="Verbose logging from the agent")
    p_concept.add_argument("--status", action="store_true",
                           help="Show the current Layer 4 state — counts, confidence, drift")
    p_concept.set_defaults(func=cmd_conceptualize)

    # status
    p_status = sub.add_parser("status", help="Check receiver health")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
