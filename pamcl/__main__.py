"""
PAMCL CLI — Command-line tools for manifest management.

Usage:
    python -m pamcl validate <manifest.yaml>
    python -m pamcl inspect  <manifest.yaml>
"""

import argparse
import sys
from pathlib import Path

import yaml

from pamcl.loader import validate


def cmd_validate(args):
    """Validate a Composition Manifest."""
    path = Path(args.manifest)
    if not path.exists():
        print(f"✗ File not found: {path}", file=sys.stderr)
        return 1

    with open(path) as f:
        try:
            manifest = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"✗ YAML parse error: {e}", file=sys.stderr)
            return 1

    if manifest is None:
        print(f"✗ Empty manifest: {path}", file=sys.stderr)
        return 1

    errors = validate(manifest)
    if errors:
        print(f"✗ Validation failed ({len(errors)} error(s)):\n")
        for e in errors:
            print(f"  • {e}")
        return 1
    else:
        api_ver = manifest.get("apiVersion", "?")
        name = manifest.get("metadata", {}).get("name", "unnamed")
        n_agents = len(manifest.get("agents", []))
        n_rules = len(manifest.get("constraints", {}).get("rules", []))
        print(f"✓ Valid manifest: {name} ({api_ver})")
        print(f"  {n_agents} agent(s), {n_rules} constraint rule(s)")
        return 0


def cmd_inspect(args):
    """Inspect a Composition Manifest — print structured summary."""
    path = Path(args.manifest)
    if not path.exists():
        print(f"✗ File not found: {path}", file=sys.stderr)
        return 1

    with open(path) as f:
        try:
            manifest = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"✗ YAML parse error: {e}", file=sys.stderr)
            return 1

    if manifest is None:
        print(f"✗ Empty manifest: {path}", file=sys.stderr)
        return 1

    # ── Metadata ──
    meta = manifest.get("metadata", {})
    print("=" * 60)
    print(f"  Manifest: {meta.get('name', 'unnamed')}")
    print(f"  Version:  {meta.get('version', '?')}")
    print(f"  API:      {manifest.get('apiVersion', '?')}")
    print("=" * 60)

    # ── Agents ──
    agents = manifest.get("agents", [])
    print(f"\nAgents ({len(agents)}):")
    for a in agents:
        role = a.get("role", "-")
        print(f"  [{role:>11}]  {a['id']}  ←  {a['class']}")
        config = a.get("config", {})
        if config:
            for k, v in config.items():
                print(f"                  {k}: {v}")

    # ── Constraints ──
    constraints = manifest.get("constraints", {})
    rules = constraints.get("rules", [])
    warmup = constraints.get("warmup_steps", 60)
    print(f"\nConstraints ({len(rules)} rules, warmup={warmup} steps):")
    for r in rules:
        exempt = " [warmup_exempt]" if r.get("warmup_exempt") else ""
        rtype = r.get("type", "?")
        if rtype == "max":
            limits = f"soft={r.get('soft_limit', '?')}, hard={r.get('hard_limit', '-')}"
        elif rtype == "min":
            limits = f"soft={r.get('soft_limit', '?')}, hard={r.get('hard_limit', '-')}"
        elif rtype == "range":
            limits = f"[{r.get('min', '?')}, {r.get('max', '?')}]"
            hmin = r.get("hard_min")
            hmax = r.get("hard_max")
            if hmin or hmax:
                limits += f" hard=[{hmin or '?'}, {hmax or '?'}]"
        else:
            limits = "?"
        print(f"  {r['id']:>16}  {r.get('variable', '?'):>20}  {rtype:>5}  {limits}{exempt}")

    # ── Control limits ──
    ctrl = manifest.get("control_limits", {})
    if ctrl:
        print(f"\nControl Limits ({len(ctrl)}):")
        for name, bounds in ctrl.items():
            lo = bounds.get("min", "-∞")
            hi = bounds.get("max", "+∞")
            print(f"  {name:>30}  [{lo}, {hi}]")

    # ── Scheduling ──
    sched = manifest.get("scheduling", {})
    print(f"\nScheduling:")
    print(f"  coordinator:     {sched.get('coordinator_id', '?')}")
    print(f"  sub_agents:      {sched.get('sub_agents', [])}")
    print(f"  coord_interval:  {sched.get('coordinator_interval_steps', '?')} steps")
    print(f"  control_dt:      {sched.get('control_interval_s', '?')} s")
    print(f"  episode:         {sched.get('episode_length_min', '?')} min")

    dispatch = sched.get("setpoint_dispatch", {})
    if dispatch:
        print(f"\n  Setpoint Dispatch:")
        for agent_id, mapping in dispatch.items():
            for coord_key, agent_key in mapping.items():
                print(f"    coordinator.{coord_key} → {agent_id}.{agent_key}")

    # ── Validation ──
    errors = validate(manifest)
    if errors:
        print(f"\n✗ Validation: {len(errors)} error(s)")
        for e in errors:
            print(f"  • {e}")
        return 1
    else:
        print(f"\n✓ Validation: passed")
        return 0


def cmd_dashboard(args):
    """Launch audit log visualization dashboard."""
    from pamcl.dashboard import serve_dashboard
    try:
        serve_dashboard(
            log_path=args.logfile,
            port=args.port,
            open_browser=not args.no_browser,
        )
        return 0
    except FileNotFoundError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


def main():
    parser = argparse.ArgumentParser(
        prog="pamcl",
        description="PAMCL — Physical AI Meta-Control Layer CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # validate
    p_validate = subparsers.add_parser(
        "validate",
        help="Validate a Composition Manifest",
    )
    p_validate.add_argument("manifest", help="Path to YAML manifest file")
    p_validate.set_defaults(func=cmd_validate)

    # inspect
    p_inspect = subparsers.add_parser(
        "inspect",
        help="Inspect a Composition Manifest (structured summary)",
    )
    p_inspect.add_argument("manifest", help="Path to YAML manifest file")
    p_inspect.set_defaults(func=cmd_inspect)

    # dashboard
    p_dash = subparsers.add_parser(
        "dashboard",
        help="Launch audit log visualization dashboard",
    )
    p_dash.add_argument("logfile", help="Path to JSONL audit log file")
    p_dash.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    p_dash.add_argument("--no-browser", action="store_true", help="Don't open browser")
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
