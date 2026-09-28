"""S-4 audit emission (best-effort, never raises).

The production framework exposes ``emit_trace_event(event_type, payload, state)`` from
``shared.utils.audit_logger`` (SDK: the S-4 audit rule, how-to/use-shared-services). The local SDK stub does not
ship it, so this shim keeps every call site identical in both environments — the same idiom
``src/api/server.py`` uses for its framework imports.

**It must never raise.** A node's ``execute()`` that dies inside an audit call turns into
``node_error`` and fails the PB-6 invoke-order boundary, which is exactly how a wrong import
signature was caught in CI on a sibling template.

**No-persist (SoT §4 Step 7)**: callers pass counts, rule references and error codes only. Never a
packet body, a change-notice excerpt, an identifier, a commercial amount, or a rejected value.
"""

from __future__ import annotations

import json
import sys
from typing import Any

try:  # production
    from shared.utils.audit_logger import emit_trace_event as _platform_emit
except Exception:  # pragma: no cover - import-time environment branch
    _platform_emit = None

TEMPLATE_ID = "SVC-C2-168"


def emit_trace_event(event_type: str, payload: dict[str, Any], state: dict[str, Any] | None = None) -> None:
    """Emit one domain audit event (S-4). Falls back to a structured stderr line under a local stub."""
    if _platform_emit is not None:
        try:
            _platform_emit(event_type, payload, state)
            return
        except Exception:  # pragma: no cover - defensive; auditing must not break a node
            pass
    record = {
        "template_id": TEMPLATE_ID,
        "event_type": event_type,
        "payload": payload,
        "session_id": (state or {}).get("session_id"),
    }
    try:
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
    except Exception:  # pragma: no cover - defensive
        pass
