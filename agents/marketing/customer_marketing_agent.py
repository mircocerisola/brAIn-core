#!/usr/bin/env python3
"""
brAIn Customer Marketing Agent — standalone
Genera onboarding journey, retention playbook, referral program, upsell strategy.
Uso: python agents/marketing/customer_marketing_agent.py --project-id 2
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


def run(project_id):
    print(f"[CUSTOMER MARKETING AGENT] project={project_id}")
    try:
        r = requests.post(f"{AGENTS_RUNNER_URL}/marketing/run",
                          json={"project_id": project_id, "phase": "retention"}, timeout=30)
        print(f"  → {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  ERRORE: {e}")


def main():
    parser = argparse.ArgumentParser(description="brAIn Customer Marketing Agent")
    parser.add_argument("--project-id", type=int, required=True)
    args = parser.parse_args()
    run(args.project_id)


if __name__ == "__main__":
    main()
