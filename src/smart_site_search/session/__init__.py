"""Apply one validated interpretation to a session without mutating its history."""

from uuid import NAMESPACE_URL, uuid5

from smart_site_search.contracts.intent import (
    ContextDelta, Filter, Interpretation, QueryState, Reference, SearchSession,
)


def resolve_reference(session: SearchSession, reference: Reference | None) -> str | None:
    if reference is None:
        return None
    result = next((r for r in session.recent_results if r.request_id == reference.request_id), None)
    if result is None:
        return None
    if reference.ordinal is not None:
        if reference.ordinal > len(result.display_order):
            return None
        card = result.display_order[reference.ordinal - 1]
        return card if reference.card_id in (None, card) else None
    if reference.card_id in result.display_order:
        return reference.card_id
    return None


def reduce_context(
    session: SearchSession, interpretation: Interpretation, request_id: str,
) -> tuple[QueryState, ContextDelta]:
    intent = interpretation
    previous = session.query_state
    fresh = previous is None or intent.operation == "new_topic"
    delta = ContextDelta(topic_changed=fresh)
    reference_card = resolve_reference(session, intent.reference)
    if previous and (intent.operation == "reference" or intent.reference is not None) and not reference_card:
        return previous.model_copy(deep=True), ContextDelta()
    if previous and intent.ambiguity == "needs_clarification" and intent.operation in ("more", "reference"):
        return previous.model_copy(deep=True), ContextDelta()

    state = QueryState(goal=intent.goal, mode=intent.mode) if fresh else previous.model_copy(deep=True)
    active = {}
    for item in previous.filters if previous else ():
        if fresh and item.scope != "session":
            delta.removed_filter_ids.append(item.filter_id)
        else:
            active[item.scope, item.component_id, item.key] = item
    if fresh or intent.operation == "refine":
        state.goal = intent.goal
        state.mode = intent.mode
        for attr in ("class_ids", "role_ids", "search_terms"):
            incoming = getattr(intent, attr)
            if incoming:
                setattr(state, attr, list(dict.fromkeys(incoming)))
    for entity in intent.entities:
        if entity not in state.entities:
            state.entities.append(entity.model_copy())
    state.unresolved_constraints = list(dict.fromkeys([*state.unresolved_constraints, *intent.unresolved_constraints]))
    state.omit_seen = intent.operation == "more"
    if reference_card:
        state.reference_card_id = delta.resolved_reference = reference_card
    for change in intent.filter_changes:
        if change.action == "clear_scope":
            for key in list(active):
                if key[:2] == (change.scope, change.component_id):
                    delta.removed_filter_ids.append(active.pop(key).filter_id)
            continue
        key = (change.scope, change.component_id, change.filter.key if change.filter else change.key)
        old = active.get(key)
        if change.action == "remove":
            if old:
                delta.removed_filter_ids.append(active.pop(key).filter_id)
            continue
        new = change.filter.model_copy(deep=True)
        if old:
            delta.replaced_filter_ids.append(old.filter_id)
            if old.key == "vendor_id" and old.strength == new.strength == "exclude":
                new.values = list(dict.fromkeys([*old.values, *new.values]))
                new.op = "in"
        new.source_turn_id = request_id
        new.filter_id = str(uuid5(NAMESPACE_URL, repr((request_id, *key))))
        active[key] = Filter.model_validate(new.model_dump())
        delta.added_filter_ids.append(new.filter_id)
    state.filters = list(active.values())
    # Exclusions reflect the effective constraints, including retained session filters.
    state.excluded_card_ids = _excluded(state, "card_id")
    state.excluded_vendor_ids = _excluded(state, "vendor_id")
    return QueryState.model_validate(state.model_dump()), delta


def _excluded(state: QueryState, key: str) -> list[str]:
    values = [v for f in state.filters if f.scope != "component" and f.key == key and f.strength == "exclude"
              for v in f.values if isinstance(v, str)]
    return list(dict.fromkeys(values))
