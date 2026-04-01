"""
Setup script — create tables in Supabase using the REST API.
Run once: python setup_db.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from src.db import get_db

def main():
    db = get_db()

    # Test connection
    print("Testing connection...")

    # We'll create tables by inserting test data and checking
    # Since Supabase anon key can't run DDL, we check if the tables exist
    # The user must run schema.sql in Supabase SQL Editor

    tables = ['users', 'menu', 'orders', 'couriers', 'conversations']
    for t in tables:
        try:
            res = db.table(t).select("id").limit(1).execute()
            print(f"  ✅ {t}: OK ({len(res.data)} rows)")
        except Exception as e:
            err = str(e)
            if "not found" in err or "does not exist" in err or "schema cache" in err:
                print(f"  ❌ {t}: NOT FOUND — run db/schema.sql in Supabase SQL Editor!")
            else:
                print(f"  ⚠️  {t}: {err}")

if __name__ == "__main__":
    main()
