"""Structured reports and text rendering."""

from __future__ import annotations

from prooftrail.reporting.json_report import (
    REPORT_SCHEMA_VERSION,
    build_report,
    format_trace,
    load_report,
    render_report_text,
    trace_from_report,
    trace_to_dict,
    validate_report,
    write_report,
)

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "build_report",
    "format_trace",
    "load_report",
    "render_report_text",
    "trace_from_report",
    "trace_to_dict",
    "validate_report",
    "write_report",
]
