"""
Phase 5 setup entrypoint: creates all tables (source_file, raw_line,
ap_lookup, audit_log, standard_line) and loads ap_lookup + raw_line (+ the
source_file rows raw_line's FK requires). standard_line is populated
separately, by db.standard_line_writer, once a human has actually
reviewed a Phase 4 cluster workbook -- there's no ground-truth file to
bulk-load it from here.

Requires DATABASE_URL — see db/connection.py. Never hardcodes a
connection string; this package has no idea which server it's pointed at
until the caller sets the environment variable.

Usage:
    python -m db.load_all
"""
from __future__ import annotations

from .connection import get_engine, get_session_factory
from .loaders import load_ap_lookup, load_raw_lines
from .models import Base


def main() -> int:
    engine = get_engine()
    Base.metadata.create_all(engine)
    print(f"schema ready: {', '.join(sorted(Base.metadata.tables))}")

    Session = get_session_factory()
    with Session() as session:
        n_ap = load_ap_lookup(session)
        print(f"loaded ap_lookup: {n_ap} rows")

        n_raw = load_raw_lines(session)
        print(f"loaded raw_line: {n_raw} rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
