#!/usr/bin/env python3
"""
brAIn Brand Agent — standalone
Genera brand DNA, naming options, logo SVG, brand kit.
Uso: python agents/marketing/brand_agent.py --project-id 2
     python agents/marketing/brand_agent.py --project-id 2 --target brain
"""
import os, sys, argparse, requests

def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
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


def run(project_id, target="project"):
    print(f"[BRAND AGENT] project={project_id} target={target}")
    try:
        r = requests.post(f"{AGENTS_RUNNER_URL}/marketing/brand",
                          json={"project_id": project_id, "target": target}, timeout=30)
        print(f"  → {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  ERRORE: {e}")


def main():
    parser = argparse.ArgumentParser(description="brAIn Brand Agent")
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--target", default="project", choices=["project", "brain"])
    args = parser.parse_args()
    run(args.project_id, args.target)


if __name__ == "__main__":
    main()
