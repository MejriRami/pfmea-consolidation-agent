"""
Phase 5 — Postgres database foundation (spec §6).

Real, working tables: source_file, raw_line, ap_lookup, audit_log.
standard_line exists only as a stub (see models.py) — its full shape in
spec §6 depends on normalized_line, which isn't built yet.

Never imports psycopg2/SQLAlchemy connection details with a hardcoded
credential — see connection.py: the connection string always comes from
the DATABASE_URL environment variable.
"""
