"""
SQLAlchemy ORM models for spec §6's data model. Phase 5 scope, per the
task: source_file, raw_line, ap_lookup, audit_log are real and loaded;
normalized_line and consolidation_decision are NOT built yet (later
phases). standard_line is now real too (see StandardLine's own docstring
for how it deviates from spec's literal DDL, and why).
"""
from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, CHAR, CheckConstraint, ForeignKey, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SourceFile(Base):
    __tablename__ = "source_file"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)  # MASTER_REF, SITE_D_PFMEA, SITE_B...
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    sheet_name: Mapped[str] = mapped_column(Text, nullable=False)
    plant: Mapped[str] = mapped_column(Text, nullable=False)
    customer: Mapped[str | None] = mapped_column(Text)
    color_code: Mapped[str | None] = mapped_column(Text)  # from 04_SOURCE_COLOR_LEGEND
    imported_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="now()"
    )
    imported_by: Mapped[str] = mapped_column(Text, nullable=False)
    rating_method: Mapped[str] = mapped_column(Text, nullable=False)  # AP | RPN | MIXED | N/A
    row_count: Mapped[int] = mapped_column(nullable=False)

    raw_lines: Mapped[list["RawLine"]] = relationship(back_populates="source_file")


class RawLine(Base):
    __tablename__ = "raw_line"
    __table_args__ = (UniqueConstraint("source_file_id", "source_row_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_file.id"), nullable=False)
    source_row_ref: Mapped[str] = mapped_column(Text, nullable=False)  # '01_ALL_SOURCES_GROUPED!R123'
    source_row_id: Mapped[str | None] = mapped_column(Text)  # '00.0.1', 'GEMBA-002', 'SITE_D_PFMEA_A-012'
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # every original cell, verbatim
    original_wording: Mapped[dict | None] = mapped_column(JSONB)  # pre-translation text per field

    source_file: Mapped["SourceFile"] = relationship(back_populates="raw_lines")


class ApLookup(Base):
    __tablename__ = "ap_lookup"

    severity: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    occurrence: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    detection: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    ap: Mapped[str] = mapped_column(CHAR(1), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    record_id: Mapped[int] = mapped_column(nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="now()"
    )


class StandardLine(Base):
    """Versioned, human-approved standard PFMEA content (spec §6).

    DEVIATION FROM SPEC'S LITERAL DDL: spec has
    `normalized_line_id INT NOT NULL REFERENCES normalized_line(id)`.
    normalized_line does not exist as a table in this codebase — Phase 3's
    NORMALIZER runs entirely in-memory over ground_truth files, and
    nothing from it is persisted to Postgres. Rather than fabricate a FK
    against a table that isn't there (same reasoning the old stub used),
    this stores the consolidated row's full content directly as JSONB
    (`line_payload`) — exactly the record shape
    pfmea_agents.decision_ingestion.build_final_lines produces, which is
    itself what would eventually populate normalized_line. `line_key`
    (f"{source}:{row_id}") stands in as the natural identity for
    versioning until normalized_line exists.

    APPROVAL: every write from db.standard_line_writer.write_consolidated_rows
    sets status='draft'. Nothing in this module, or anywhere reachable
    from the decision-ingestion pipeline, sets status='approved' — the
    only function allowed to is db.standard_line_writer.approve_standard_line,
    which requires an explicit human `approved_by` identity (spec §7: "No
    MCP tool may write to standard_line with status='approved'. Approval
    is human-only.").
    """
    __tablename__ = "standard_line"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','reviewed','approved','archived')",
            name="standard_line_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_family: Mapped[str] = mapped_column(Text, nullable=False)  # chokes | brushes | graphite | assembly
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    line_key: Mapped[str] = mapped_column(Text, nullable=False)  # f"{source}:{row_id}" -- see class docstring
    line_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # the full consolidated row, verbatim
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    supersedes: Mapped[int | None] = mapped_column(ForeignKey("standard_line.id"))
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="now()"
    )
