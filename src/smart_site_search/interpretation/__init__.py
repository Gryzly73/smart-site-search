"""Interpret a Russian search turn, keeping catalog identities under server control."""

import re
from uuid import uuid4

from smart_site_search.contracts.intent import (
    Entity, ExpertComponent, ExpertPlan, FilterChange, Interpretation, Reference,
)
from smart_site_search.session import resolve_reference

from .provider import DEFAULT_MODEL, Pricing, Responses, clean
from .rules import SESSION_SCOPE, local_intent, lookup_entities, make_filter, norm, unsupported_measurements
from .wire import ModelIntent, ModelPlan

__all__ = ["Interpreter", "Pricing", "DEFAULT_MODEL"]


def _taxonomy(tax):
    compact = {}
    for group in ("classes", "roles"):
        compact[group] = {
            k: {"label": v.get("label", k), "aliases": v.get("aliases", [])[:5]} if isinstance(v, dict) else v
            for k, v in tax.get(group, {}).items() if not isinstance(v, dict) or v.get("verified") is not False
        }
    compact["fields"] = {k: {a: v[a] for a in ("type", "unit", "ops", "values") if a in v}
                         for k, v in tax.get("fields", {}).items() if isinstance(v, dict)}
    return compact


def _check_ids(ids, tax, key):
    if any(i not in tax[key] for i in ids):
        raise ValueError("unknown taxonomy id")
    return list(dict.fromkeys(ids))


def _check_filter(item, tax, source, turn, scope="topic", component=None):
    meta = tax["fields"].get(item.key)
    if meta is None or item.op not in meta.get("ops", []) or not item.source_text.strip() or norm(item.source_text) not in norm(source):
        raise ValueError("unsupported constraint")
    # Card and vendor IDs originate in server lookup. They are never provider-supplied.
    if item.key in {"card_id", "vendor_id"}:
        raise ValueError("catalog identity must be resolved locally")
    if meta.get("unit") and item.unit and norm(meta["unit"]) != norm(item.unit):
        raise ValueError("wrong unit")
    if item.key in ("price_minor", "total_price_minor"):
        if not item.currency or not re.fullmatch(r"[A-Z]{3}", item.currency):
            raise ValueError("currency required")
    elif item.currency is not None:
        raise ValueError("irrelevant currency")
    numeric = {"term_months", "users", "devices", "nodes", "cores", "ram_gib", "vram_gb",
               "storage_gb", "price_minor", "total_price_minor"}
    for value in item.values:
        if item.key in numeric or item.op in ("between", "gte", "lte"):
            if type(value) is not int or value < 0:
                raise ValueError("expected nonnegative integer")
        elif item.key in ("available", "saas", "domestic_hint", "perpetual"):
            if type(value) is not bool:
                raise ValueError("expected boolean")
        elif not isinstance(value, str):
            raise ValueError("expected string")
        if meta.get("values") and value not in meta["values"]:
            raise ValueError("unregistered enum value")
    if item.key in ("role_id", "class_id"):
        _check_ids(item.values, tax, "roles" if item.key == "role_id" else "classes")
    return make_filter(item.key, item.op, item.values, item.source_text, scope, item.strength, item.currency,
                       meta.get("unit") or item.unit, component, turn)


def _session(session):
    if not session or not session.query_state:
        return None
    state = session.query_state
    return {"goal": state.goal[:500], "mode": state.mode, "class_ids": state.class_ids, "role_ids": state.role_ids,
            "entities": [e.model_dump(exclude={"resolved_id"}) for e in state.entities[:12]],
            "filters": [f.model_dump(include={"key", "op", "values", "unit", "currency", "strength", "scope"})
                        for f in state.filters],
            "recent_result_sizes": [len(r.display_order) for r in session.recent_results[-2:]],
            "turns": [{"query": t.query[:300], "summary": t.response_summary[:300]} for t in session.last_turns[-2:]]}


class Interpreter(Responses):
    def interpret(self, query, session_snapshot, taxonomy, deadline):
        local = local_intent(query, session_snapshot, taxonomy)
        if local.interpretation_source == "rules":
            return local
        tax = _taxonomy(taxonomy)
        lookups = lookup_entities(taxonomy)
        data = {"query": query, "session": _session(session_snapshot), "taxonomy": tax,
                "lookup": [{"text": e["text"], "type": e["type"]} for e in lookups[:8]]}
        def validate(wire, call_id):
            return self._interpretation(wire, local, query, session_snapshot, taxonomy, tax, lookups)
        return self.call("interpret", data, ModelIntent, validate, deadline) or local

    def _interpretation(self, wire, local, query, session, taxonomy, tax, lookups):
        classes = _check_ids(wire.class_ids, tax, "classes")
        roles = _check_ids(wire.role_ids, tax, "roles")
        if not wire.goal.strip():
            raise ValueError("empty goal")
        turn = taxonomy.get("request_id", "current")
        operation = wire.operation
        mode = "expert" if local.mode == "expert" else wire.mode
        question = wire.clarification_question
        unresolved = list(dict.fromkeys([*wire.unresolved_constraints, *unsupported_measurements(query)]))
        entities = []
        safe_query = clean(query)
        for entity in wire.entities:
            if not entity.text.strip() or norm(entity.text) not in norm(safe_query):
                if (not self.yandex_folder or wire.mode == "direct" or entity.usage == "excluded"
                        or not (classes or roles)):
                    raise ValueError("entity absent from user turn")
                continue
            resolved = {e["resolved_id"] for e in lookups if e["type"] == entity.type and norm(e["text"]) == norm(entity.text)}
            verified = next(iter(resolved)) if len(resolved) == 1 else None
            entities.append(Entity(**entity.model_dump(), resolved_id=verified))
        known_entities = {(entity.type, norm(entity.text)) for entity in entities if entity.resolved_id}
        entities.extend(entity for entity in local.entities if entity.resolved_id
                        and (entity.type, norm(entity.text)) not in known_entities)
        changes = {}
        known = {(change.scope, change.key): change for change in local.filter_changes}
        for change in wire.filter_changes:
            scope = change.scope
            if scope == "session" and not SESSION_SCOPE.search(norm(query)):
                if change.action != "set":
                    raise ValueError("session scope not requested")
                scope = "topic"
            if change.key and change.key not in tax["fields"]:
                raise ValueError("unknown filter key")
            if change.action == "clear_scope" and not re.search(r"сброс|очист|сним|убер|удал", norm(query)):
                raise ValueError("clear not requested")
            if (scope, change.filter.key if change.filter else change.key) in known:
                continue
            f = _check_filter(change.filter, tax, safe_query, turn, scope) if change.filter else None
            parsed = FilterChange(action=change.action, scope=scope, key=change.key, filter=f)
            changes[scope, f.key if f else change.key] = parsed
        for ent in entities:
            if ent.usage != "excluded":
                continue
            if not ent.resolved_id:
                unresolved.append(ent.text)
                continue
            key = "vendor_id" if ent.type == "vendor" else "card_id"
            existing = changes.get(("topic", key))
            values = list(existing.filter.values) if existing and existing.filter and existing.filter.strength == "exclude" else []
            f = make_filter(key, "in", list(dict.fromkeys([*values, ent.resolved_id])), ent.text, strength="exclude", turn=turn)
            changes["topic", key] = FilterChange(action="set", scope="topic", key=key, filter=f)
        for key, change in known.items():
            if key not in changes:
                changes[key] = change
        reference = local.reference
        if local.operation in ("more", "reference"):
            operation = local.operation
            question = local.clarification_question or question
        elif local.operation == "refine" and local.filter_changes:
            operation = "refine"
            question = local.clarification_question
        if local.clarification_question and norm(query) in ("сервер", "сзи"):
            question = local.clarification_question
        if wire.reference_ordinal is not None:
            if wire.reference_ordinal < 1:
                raise ValueError("invalid ordinal")
            rid = taxonomy.get("reference_request_id") or (
                session.recent_results[-1].request_id if session and session.recent_results else "")
            if reference and reference.ordinal and reference.ordinal != wire.reference_ordinal:
                raise ValueError("conflicting ordinal")
            reference = Reference(request_id=rid, ordinal=wire.reference_ordinal, card_id=taxonomy.get("selected_card_id"))
        if operation == "reference" or reference:
            if not session or not resolve_reference(session, reference):
                question = "Какую карточку из предыдущей выдачи вы имеете в виду?"
        if mode == "functional" and not roles and not classes and operation == "new_topic":
            unresolved.append("unmapped_need")
            question = question or "Какую функцию должно выполнять решение?"
        return Interpretation(mode=mode, operation=operation, goal=query if mode == "expert" else wire.goal,
                              entities=entities, class_ids=classes,
                              role_ids=roles, search_terms=wire.search_terms, filter_changes=list(changes.values()),
                              reference=reference, unresolved_constraints=list(dict.fromkeys(unresolved)),
                              ambiguity="needs_clarification" if question else "none", clarification_question=question,
                              interpretation_source="llm")

    def plan_expert(self, interpretation, query_state, taxonomy, deadline):
        if interpretation.mode != "expert":
            return None
        tax = _taxonomy(taxonomy)
        goal = query_state.goal
        data = {"goal": goal, "unresolved_constraints": query_state.unresolved_constraints,
                "filters": [f.model_dump(exclude={"filter_id", "source_turn_id"}) for f in query_state.filters],
                "taxonomy": tax}
        def validate(wire, call_id):
            components = []
            for i, part in enumerate(wire.components, 1):
                roles = _check_ids(part.role_ids, tax, "roles")
                cid = f"component-{i}"
                filters = []
                unknowns = list(part.unknowns)
                for f in part.filters:
                    if any(current.key == f.key for current in query_state.filters):
                        continue
                    try:
                        filters.append(_check_filter(f, tax, clean(goal), taxonomy.get("request_id", call_id),
                                                     "component", cid))
                    except ValueError:
                        unknowns.append("Модель предложила условие, которое требуется уточнить.")
                components.append(ExpertComponent(component_id=cid, label=part.label, role_ids=roles, required=part.required,
                                                  search_terms=part.search_terms, filters=filters, rationale=part.rationale,
                                                  unknowns=list(dict.fromkeys(unknowns))))
            if any(f.key.startswith("vram_") for f in query_state.filters) and not any(
                "gpu" in c.role_ids for c in components
            ) and "gpu" in tax["roles"]:
                missing_gpu = ExpertComponent(
                    component_id="gpu-required", label="Видеокарта", role_ids=["gpu"], required=True,
                    search_terms=["видеокарта"], filters=[], rationale="Запрошена видеопамять.", unknowns=[],
                )
                if len(components) == 4:
                    drop = next((i for i in reversed(range(4)) if not components[i].required), 3)
                    components[drop] = missing_gpu
                else:
                    components.append(missing_gpu)
            for component in components:
                if "gpu" in component.role_ids and any(f.key.startswith("vram_") for f in query_state.filters):
                    component.required = True
            if not components:
                raise ValueError("incomplete plan")
            if not any(c.required for c in components):
                components[0].required = True
            question = wire.clarification_question
            if wire.scope_limited and not question:
                question = "Какие компоненты и ограничения обязательны для этого решения?"
            return ExpertPlan(plan_id=str(uuid4()), goal=goal, scope_limited=wire.scope_limited, assumptions=wire.assumptions,
                              clarification_question=question, components=components,
                              knowledge_source="external_llm", model_call_id=call_id)
        return self.call("expert_plan", data, ModelPlan, validate, deadline)
