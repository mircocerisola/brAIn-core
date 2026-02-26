#!/usr/bin/env python3
"""
brAIn Migration Runner — psycopg2 diretto, no Supabase CLI, no token expiry.
Uso: python utils/migrate.py [--dry-run] [--file nome_file.sql] [--force]

Per uso Supabase CLI (db pull, etc.): usa il token da SUPABASE_ACCESS_TOKEN
   supabase login --token $SUPABASE_ACCESS_TOKEN
   npx supabase db pull
"""
import os
import sys
import glob
import argparse
from datetime import datetime, timezone

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERRORE: psycopg2 non installato. Esegui: pip install psycopg2-binary")
    sys.exit(1)

# Carica .env
def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    env = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env

def get_db_conn():
    env = _load_env()
    # DB password da env var o da MEMORY.md
    db_password = os.environ.get("DB_PASSWORD") or env.get("DB_PASSWORD")
    if not db_password:
        db_password = input("DB password Supabase: ").strip()

    supabase_url = os.environ.get("SUPABASE_URL") or env.get("SUPABASE_URL", "")
    # Estrai host da URL: https://rcwawecswjzpnycuirpx.supabase.co -> rcwawecswjzpnycuirpx.supabase.co
    host = supabase_url.replace("https://", "").replace("http://", "").rstrip("/")
    db_host = f"db.{host}" if not host.startswith("db.") else host

    conn = psycopg2.connect(
        host=db_host,
        port=5432,
        dbname="postgres",
        user="postgres",
        password=db_password,
        sslmode="require",
    )
    return conn

def ensure_migration_history(conn):
    """Crea la tabella migration_history se non esiste."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS migration_history (
                id serial PRIMARY KEY,
                filename text UNIQUE NOT NULL,
                applied_at timestamptz DEFAULT now()
            );
        """)
    conn.commit()

def get_applied_migrations(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM migration_history ORDER BY filename;")
        return {row[0] for row in cur.fetchall()}

def apply_migration(conn, filepath, dry_run=False):
    filename = os.path.basename(filepath)
    with open(filepath, 'r', encoding='utf-8') as f:
        sql = f.read().strip()
    if not sql:
        print(f"  SKIP {filename}: file vuoto")
        return True

    if dry_run:
        print(f"  [DRY-RUN] {filename}: {len(sql)} chars SQL")
        return True

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO migration_history (filename) VALUES (%s) ON CONFLICT DO NOTHING;", (filename,))
        conn.commit()
        print(f"  OK {filename}")
        return True
    except Exception as e:
        conn.rollback()
        print(f"  ERRORE {filename}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="brAIn Migration Runner")
    parser.add_argument("--dry-run", action="store_true", help="Mostra cosa farebbe senza applicare")
    parser.add_argument("--file", help="Applica solo questo file .sql (basename)")
    parser.add_argument("--force", action="store_true", help="Riapplica anche migrazione gia' applicata")
    args = parser.parse_args()

    migrations_dir = os.path.join(os.path.dirname(__file__), '..', 'supabase', 'migrations')
    sql_files = sorted(glob.glob(os.path.join(migrations_dir, '*.sql')))

    if not sql_files:
        print("Nessun file .sql trovato in supabase/migrations/")
        return

    print(f"brAIn Migration Runner — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Trovati {len(sql_files)} file migration")

    try:
        conn = get_db_conn()
        ensure_migration_history(conn)
        applied = get_applied_migrations(conn)
        print(f"Migration gia' applicate: {len(applied)}")
        print()
    except Exception as e:
        print(f"ERRORE connessione DB: {e}")
        sys.exit(1)

    pending = []
    for f in sql_files:
        fname = os.path.basename(f)
        if args.file and fname != args.file:
            continue
        if fname in applied and not args.force:
            print(f"  SKIP {fname} (gia' applicata)")
            continue
        pending.append(f)

    if not pending:
        print("Nessuna migrazione da applicare.")
        conn.close()
        return

    print(f"Da applicare: {len(pending)}")
    ok = 0
    for f in pending:
        if apply_migration(conn, f, dry_run=args.dry_run):
            ok += 1

    conn.close()
    print()
    print(f"Completato: {ok}/{len(pending)} migration applicate" + (" (dry-run)" if args.dry_run else ""))

if __name__ == "__main__":
    main()
