#!/usr/bin/env python3
"""
brAIn Marketing Coordinator — standalone CLI
CMO-level orchestrator: avvia tutti gli agenti marketing in sequenza/parallelo.

Uso:
  python agents/marketing_coordinator.py --project-id 2
  python agents/marketing_coordinator.py --project-id 2 --phase brand
  python agents/marketing_coordinator.py --brain               # brand identity brAIn
  python agents/marketing_coordinator.py --report --project-id 2
"""
import os, sys, argparse, requests

def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    try:
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass

_load_env()

AGENTS_RUNNER_URL = os.getenv("AGENTS_RUNNER_URL", "http://localhost:8080")


def _post(endpoint, body=None):
    try:
        r = requests.post(f"{AGENTS_RUNNER_URL}{endpoint}", json=body or {}, timeout=30)
        print(f"  {endpoint} → {r.status_code}")
        return r.json()
    except Exception as e:
        print(f"  ERRORE {endpoint}: {e}")
        return {}


def run(project_id, target="project", phase="full"):
    print(f"[MARKETING COORDINATOR] project={project_id} target={target} phase={phase}")
    return _post("/marketing/run", {"project_id": project_id, "target": target, "phase": phase})


def brand(project_id=None, target="project"):
    print(f"[MARKETING COORDINATOR] brand identity project={project_id} target={target}")
    return _post("/marketing/brand", {"project_id": project_id, "target": target})


def report(project_id=None):
    print(f"[MARKETING COORDINATOR] report project={project_id}")
    return _post("/marketing/report", {"project_id": project_id})


def main():
    parser = argparse.ArgumentParser(description="brAIn Marketing Coordinator")
    parser.add_argument("--project-id", type=int, help="ID progetto")
    parser.add_argument("--phase", default="full", choices=["full", "brand", "gtm", "retention"])
    parser.add_argument("--brain", action="store_true", help="Brand identity brAIn")
    parser.add_argument("--report", action="store_true", help="Report settimanale")
    args = parser.parse_args()

    if args.brain:
        brand(target="brain")
    elif args.report:
        report(args.project_id)
    elif args.project_id:
        run(args.project_id, phase=args.phase)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
