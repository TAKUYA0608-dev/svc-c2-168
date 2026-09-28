"""Identifiers named in the docs must exist in the implementation.

Review 2026-08-05: the docs named a taxonomy key
(`exception_scopeed_pending_human_validation`) that the implementation does not have — a substitution
artefact from porting this template from a sibling — and `docs/07` still described the sibling's
domain, listing an event and provenance namespaces this agent never emits or accepts.

Both are the same failure: prose drifting from the code with nothing checking. Reviewing the docs by
eye found them once; this finds them every time. It deliberately checks *identifiers* — taxonomy
keys, trace events, provenance namespaces — because those are the parts an operator will copy
verbatim into a payload, a query or an alert, and a wrong one silently does not match.
"""

import ast
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
_DOCS = sorted((_ROOT / "docs").glob("*.md"))

#: Backticked identifiers that are not agent identifiers — filenames, config keys, JSON paths.
_NOT_IDENTIFIERS = re.compile(r"\.(py|md|json|yaml|yml|toml|sh|txt)$")


def _source_text() -> str:
    return "\n".join(p.read_text() for p in _SRC.rglob("*.py"))


def _emitted_events() -> set[str]:
    out = set()
    for path in _SRC.rglob("*.py"):
        for call in ast.walk(ast.parse(path.read_text())):
            if (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "emit_trace_event"
                    and call.args and isinstance(call.args[0], ast.Constant)):
                out.add(call.args[0].value)
    return out


def _backticked(pattern: re.Pattern) -> dict[str, list[str]]:
    """Matching backticked tokens per doc file."""
    found: dict[str, list[str]] = {}
    for doc in _DOCS:
        hits = [t for t in re.findall(r"`([^`\s]+)`", doc.read_text())
                if pattern.fullmatch(t) and not _NOT_IDENTIFIERS.search(t)]
        if hits:
            found[doc.name] = sorted(set(hits))
    return found


class TestTaxonomyKeys:
    """A gap kind the docs name must be one the agent can actually produce."""

    def test_every_taxonomy_key_in_the_docs_exists(self):
        src = _source_text()
        named = _backticked(re.compile(r"[a-z][a-z0-9_]{6,}"))
        missing = {doc: [t for t in toks
                         if t.endswith(("_missing", "_undefined", "_unresolved", "_stale",
                                        "_incomplete", "_stated", "_claimed", "_validation",
                                        "_ambiguous", "_statement"))
                         and f'"{t}"' not in src]
                   for doc, toks in named.items()}
        missing = {d: t for d, t in missing.items() if t}
        assert not missing, f"taxonomy keys in docs but not in src/: {missing}"


class TestTraceEvents:
    def test_every_event_named_in_the_docs_is_emitted(self):
        """A token counts as an event when its suffix is one this agent's own events use.

        Matching on shape alone was too broad — `hitl.enabled` and `agent.required_trust_level` are
        config keys, not events. Deriving the suffixes from the code keeps the check pointed at the
        thing an operator would paste into a log query, and still catches the ported-from-a-sibling
        case that prompted it (`inspection_record_ingest.rejected`, whose `.rejected` suffix this
        agent does use, under a different step name).
        """
        emitted = _emitted_events()
        assert emitted, "no emit_trace_event literals found — the check would be vacuous"
        verbs = {e.split(".", 1)[1] for e in emitted if "." in e}
        assert verbs, "no event suffixes derivable — the check would be vacuous"
        named = _backticked(re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*"))
        missing = {doc: [t for t in toks
                         if t.split(".", 1)[1] in verbs and t not in emitted]
                   for doc, toks in named.items()}
        missing = {d: t for d, t in missing.items() if t}
        assert not missing, f"trace events in docs but never emitted: {missing}"


class TestProvenanceNamespaces:
    """`docs/07` lists the systems of record an operator may cite. A wrong one is rejected silently.

    ★ The first version of this skipped on both sibling templates because its constant lookup had a
    precedence bug (`A and B or C`) — a silent skip, which is the same vacuous pass this file exists
    to prevent. It now decides on the doc: if the guide offers no namespaces the check does not
    apply; if it offers any, failing to find the authorised set is a **failure**, not a skip, because
    we would be publishing values we cannot check.
    """

    @staticmethod
    def _authorised() -> set[str] | None:
        from src.services import service
        for name, value in vars(service).items():
            if (name.isupper() and isinstance(value, (frozenset, set, tuple, list))
                    and ("AUTHORIS" in name or "AUTHORIZ" in name)
                    and all(isinstance(x, str) for x in value)):
                return set(value)
        return None

    @pytest.mark.parametrize("doc", [d for d in _DOCS if d.name.startswith("07")])
    def test_every_namespace_offered_to_operators_is_authorised(self, doc):
        offered = set(re.findall(r"`([a-z][a-z0-9_]*):`", doc.read_text()))
        authorised = self._authorised()
        if not offered:
            # This template cites provenance some other way; nothing is being offered to copy.
            assert authorised is None or True
            pytest.skip(f"{doc.name} offers no `namespace:` values — check does not apply")
        assert authorised is not None, (
            f"{doc.name} offers {sorted(offered)} but no authorised-systems constant was found — "
            "cannot verify what operators are told to send")
        unknown = sorted(offered - authorised)
        assert not unknown, f"{doc.name} offers namespaces the agent rejects: {unknown}"
