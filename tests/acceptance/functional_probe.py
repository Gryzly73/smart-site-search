"""Exercise acceptance cases against the local read-only catalog and an isolated runtime."""
import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi.testclient import TestClient

from smart_site_search.api.app import create_app
from smart_site_search.catalog import CatalogRepository
from smart_site_search.settings import Settings

SCENARIOS = {
    "A01a": ["Astra Linux Special Edition"],
    "A01b": ["астра линукс"],
    "A02a": ["касперсикй"],
    "A02b": ["фыекф дштгч"],
    "A02c": ["AnyDesk 7.3"],
    "A03a": ["антивирь на 50 компов"],
    "A03b": ["СЗИ"],
    "A04": ["вернуть рабочую среду после потери файлов"],
    "A05": ["сервер", "именно ПО для сервера"],
    "A06": ["Astra Linux Special Edition", "на год для юрлица", "на два года", "срок неважен"],
    "A07": ["Astra Linux", ("второй вариант", -1), ("ещё без этого вендора", -1), "теперь ноутбук"],
    "A08_R04": ["Кибер Бэкап для платформы виртуализации, коммерческому юрлицу, электронная бессрочная лицензия до 90 тысяч", "подними до 110 тысяч"],
    "A09_R02": ["касперсикй, антивирь на 50 компов", "обязательно ФСТЭК"],
    "A10": ["NVIDIA L40S", "антивирь на 50 компов", "INFERIT RS212 с 48 ГБ видеопамяти"],
    "A12_R06": ["Локальная LLM для сотрудников, доступный GPU от 48 ГБ"],
    "A12b": ["Запустить языковую модель внутри компании для коллег, нужен доступный ускоритель с видеопамятью от 48 ГБ"],
    "A12c": ["Развернуть инфраструктуру локальной языковой модели для сотрудников с доступным GPU от 48 ГБ"],
    "A13_R05": ["Стойковый INFERIT RS212 и что к нему докупить", ("Ещё без вендора второй карточки", -1), "теперь ноутбук"],
    "A13b": ["Стойковый сервер INFERIT RS212"],
    "A14": ["ноутбук"],
    "A15a": ["Продукт Невозможный Сапсан ZZQX 76539"],
    "A15b": ["Astra Linux Special Edition до 1 рубля"],
    "A16a": ["кибер бэкап"],
    "A16b": ["мтс"],
    "R01": ["alt server 11 x86, бессрочно"],
    "R03": ["Хочу восстанавливать виртуалки после сбоя", "на год для юрлица"],
}


def search(client, query, previous=None, reference=None, selected=None):
    body = {
        "schema_version": "1.0", "client_request_id": str(uuid4()), "query": query,
        "expected_revision": previous["revision"] if previous else 0,
    }
    if previous:
        body["session_id"] = previous["session_id"]
    if reference:
        body["reference_request_id"] = reference["request_id"]
    if selected:
        body["selected_card_id"] = selected
    response = client.post("/api/v1/search", json=body)
    return response.status_code, response.json()


def compact(item, repo):
    offer = repo.get_offer(item["selected_offer_id"])
    card = repo.get_card(item["card_id"])
    amount = offer.price.amount_minor if offer and offer.price else None
    rendered_amount = item["price"]["amount_minor"] if item["price"] else None
    return {
        **{key: item[key] for key in (
            "card_id", "name", "vendor_names", "kind", "class_id", "group", "selected_offer_id",
            "price", "quantity_total", "price_label", "available", "url_kind", "component_ids",
            "compatibility_status", "relation_id", "matched_conditions", "unmet_conditions",
        )},
        "snapshot_agreement": bool(
            offer and card and offer.card_id == item["card_id"] and amount == rendered_amount
            and offer.available == item["available"] and (offer.url or card.card_url) == item["url"]
        ),
        "source_fields": sorted({str(ref.get("field")) for ref in item["source_refs"]}),
    }


def compact_event(event):
    kind = event["event_type"]
    payload = event["payload"]
    if kind == "ranking_completed":
        payload = {"ordered_card_ids": payload["ordered_card_ids"][:12],
                   "has_rank_factors": bool(payload["rank_factors"])}
    if kind == "retrieval_completed":
        payload = {key: val for key, val in payload.items() if key != "candidate_ids"}
    if kind == "llm_call_started":
        payload = {key: val for key, val in payload.items() if key != "request_fingerprint"}
    return {"seq": event["seq"], "event_type": kind, "payload": payload, "provenance": event["provenance"]}


def run_scenario(client, repo, turns):
    previous = None
    responses = []
    for turn in turns:
        query, reference = turn if isinstance(turn, tuple) else (turn, None)
        source = previous if reference else None
        code, result = search(client, query, previous, source)
        row = {"query": query, "http": code}
        if code != 200:
            row["response"] = result
        else:
            trace = client.get(result["trace_url"]).json()
            row["response"] = {**result, "items": [compact(item, repo) for item in result["items"]]}
            row["trace_counts"] = {kind: sum(event["event_type"] == kind for event in trace)
                                   for kind in dict.fromkeys(event["event_type"] for event in trace)}
            visible = {item["card_id"] for item in result["items"]}
            row["trace"] = [compact_event(event) for event in trace if (
                event["event_type"] != "offer_verification_completed" or event["payload"]["card_id"] in visible
            )]
            previous = result
        responses.append(row)
    return responses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="sss-functional-") as tmp:
        settings = replace(Settings.from_env(), runtime_path=Path(tmp) / "state.sqlite",
                           demo_mode=True, external_llm_enabled=args.live)
        if args.live and (settings.llm_state != "configured" or settings.llm_provider != "yandex"
                          or settings.llm_model != "yandexgpt-5.1"):
            parser.error("Live run requires configured YandexGPT 5.1.")
        catalog = CatalogRepository(settings.catalog_path)
        start = datetime.now(UTC) - timedelta(seconds=1)
        with TestClient(create_app(settings)) as client:
            data = {
                "readiness": client.get("/readyz").json(),
                "scenarios": {key: run_scenario(client, catalog, SCENARIOS[key])
                              for key in (args.scenario or SCENARIOS)},
                "analytics": client.get("/api/v1/analytics", params={
                    "from": start.isoformat(), "to": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
                    "cohort": "all",
                }).json(),
            }
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
