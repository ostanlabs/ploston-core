"""Authoring-DX OTel meters for the workflow management surface.

Spec: WORKFLOW_AUTHORING_DX_V2 §Measurement Plan. Six instruments
that quantify the round-trip and token savings claims of M-081:

- ``ploston_workflow_schema_response_bytes`` (histogram) — Tier 1
  payload size; ensures the budget under 2K tokens stays honest.
- ``ploston_workflow_create_roundtrips_total`` (counter) — count of
  ``workflow_create`` calls per session; target is 1 per session.
- ``ploston_workflow_patch_calls_total`` (counter) — replaces the
  pre-change ``workflow_update`` count.
- ``ploston_draft_created_total`` (counter) — drafts produced when
  ``workflow_create`` returns invalid YAML.
- ``ploston_draft_promoted_total`` (counter) — drafts promoted to
  live workflows via ``workflow_patch``.
- ``ploston_suggested_fix_accepted_total`` /
  ``ploston_suggested_fix_rejected_total`` (counters) — fix
  acceptance rate by error ``kind``.

The class is intentionally a thin wrapper: handlers call
``record_*`` methods so tests can assert on a no-op double without
needing a live OTel pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opentelemetry.metrics import Counter, Histogram, Meter


_PREFIX = "ploston"


class WorkflowAuthoringMetrics:
    """Six-meter facade for the M-081 Measurement Plan."""

    _schema_response_bytes: Histogram | None
    _workflow_create_roundtrips_total: Counter | None
    _workflow_patch_calls_total: Counter | None
    _draft_created_total: Counter | None
    _draft_promoted_total: Counter | None
    _suggested_fix_accepted_total: Counter | None
    _suggested_fix_rejected_total: Counter | None

    def __init__(self, meter: Meter | None) -> None:
        """Create instruments lazily.

        ``meter`` may be ``None`` when telemetry is disabled (tests,
        local dev). All ``record_*`` calls become silent no-ops in
        that case so callers don't need to branch on telemetry being
        present.
        """
        self._meter = meter
        if meter is None:
            self._schema_response_bytes = None
            self._workflow_create_roundtrips_total = None
            self._workflow_patch_calls_total = None
            self._draft_created_total = None
            self._draft_promoted_total = None
            self._suggested_fix_accepted_total = None
            self._suggested_fix_rejected_total = None
            return

        self._schema_response_bytes = meter.create_histogram(
            name=f"{_PREFIX}_workflow_schema_response_bytes",
            description="workflow_schema response payload size, in bytes.",
            unit="By",
        )
        self._workflow_create_roundtrips_total = meter.create_counter(
            name=f"{_PREFIX}_workflow_create_roundtrips_total",
            description="Total workflow_create calls (per session, target 1).",
            unit="1",
        )
        self._workflow_patch_calls_total = meter.create_counter(
            name=f"{_PREFIX}_workflow_patch_calls_total",
            description="Total workflow_patch calls; replaces workflow_update count.",
            unit="1",
        )
        self._draft_created_total = meter.create_counter(
            name=f"{_PREFIX}_draft_created_total",
            description="Drafts produced when workflow_create yields invalid YAML.",
            unit="1",
        )
        self._draft_promoted_total = meter.create_counter(
            name=f"{_PREFIX}_draft_promoted_total",
            description="Drafts promoted to live workflows via workflow_patch.",
            unit="1",
        )
        self._suggested_fix_accepted_total = meter.create_counter(
            name=f"{_PREFIX}_suggested_fix_accepted_total",
            description="suggested_fix entries accepted by an agent.",
            unit="1",
        )
        self._suggested_fix_rejected_total = meter.create_counter(
            name=f"{_PREFIX}_suggested_fix_rejected_total",
            description="suggested_fix entries rejected (agent picked a different op).",
            unit="1",
        )

    # ── Recording helpers ─────────────────────────────────────────

    def record_schema_response_bytes(self, byte_size: int, *, section: str = "tier1") -> None:
        if self._schema_response_bytes is not None:
            self._schema_response_bytes.record(byte_size, attributes={"section": section})

    def record_workflow_create(self, *, status: str) -> None:
        if self._workflow_create_roundtrips_total is not None:
            self._workflow_create_roundtrips_total.add(1, attributes={"status": status})

    def record_workflow_patch(self, *, target: str, status: str) -> None:
        """``target`` is one of ``draft``/``live``; ``status`` is ``patched``/``draft``."""
        if self._workflow_patch_calls_total is not None:
            self._workflow_patch_calls_total.add(1, attributes={"target": target, "status": status})

    def record_draft_created(self) -> None:
        if self._draft_created_total is not None:
            self._draft_created_total.add(1)

    def record_draft_promoted(self) -> None:
        if self._draft_promoted_total is not None:
            self._draft_promoted_total.add(1)

    def record_suggested_fix(self, *, accepted: bool, kind: str = "unknown") -> None:
        if accepted:
            if self._suggested_fix_accepted_total is not None:
                self._suggested_fix_accepted_total.add(1, attributes={"kind": kind})
        else:
            if self._suggested_fix_rejected_total is not None:
                self._suggested_fix_rejected_total.add(1, attributes={"kind": kind})

    # ── Test affordance ───────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        """Return ``True`` when a real meter was provided."""
        return self._meter is not None
