from smart_site_search.catalog import CatalogRepository
from smart_site_search.catalog.build import build_catalog
from smart_site_search.contracts.intent import Filter, QueryState


def test_import_and_retrieve(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<yml_catalog><shop><categories>
        <category id="1">Astra Linux Special Edition</category>
        </categories><offers><offer id="7" group_id="1" available="true">
        <name>Astra Linux Special Edition</name><vendor>Astra</vendor>
        <vendorCode>AST7</vendorCode><price>0</price><currencyId>RUR</currencyId>
        <param name="Параметр">one</param><param name="Параметр">two</param>
        </offer></offers></shop></yml_catalog>""", encoding="utf-8",
    )
    database = tmp_path / "catalog.sqlite"
    manifest = build_catalog(feed, database)
    repo = CatalogRepository(database)
    assert manifest["catalog_version"] == repo.catalog_version
    card = repo.get_card("group:1")
    offer = repo.get_offer("7")
    assert card.offer_count == 1
    assert offer.available and offer.price is None and offer.price_state == "on_request"
    assert [atom.value for atom in offer.raw_params] == ["one", "two"]
    assert repo.resolve_entities("астра линукс") == [
        {"text": "астра линукс", "type": "product", "resolved_id": "group:1", "exact": True}
    ]
    assert repo.lookup("AST7")[0].card_id == card.card_id
    state = QueryState(goal="Астра", mode="direct", search_terms=["астра линукс"],
                       excluded_vendor_ids=card.vendor_ids)
    assert repo.retrieve(state).candidates == []
    assert repo.relations()


def test_retrieve_fills_pages_without_repeating_cards(tmp_path):
    feed = tmp_path / "feed.xml"
    offers = "".join(
        f'<offer id="{i}" available="true"><name>ИБП Demo {i}</name>'
        '<price>1599.10</price><currencyId>RUB</currencyId></offer>'
        for i in range(1, 5)
    )
    feed.write_text(f"<yml_catalog><shop><offers>{offers}</offers></shop></yml_catalog>", encoding="utf-8")
    database = tmp_path / "catalog.sqlite"
    build_catalog(feed, database)
    repo = CatalogRepository(database)
    state = QueryState(goal="резервное питание", mode="functional", role_ids=["ups"])
    first = repo.retrieve(state, card_budget=2)
    second = repo.retrieve(state, first.next_cursor, card_budget=2)
    assert not first.exhausted and second.exhausted
    assert len({c.card_id for c in [*first.candidates, *second.candidates]}) == 4
    assert all(c.relevance == "confirmed" for c in first.candidates)
    assert repo.get_offer("1").price.amount_minor == 159910


def test_numeric_channel_reaches_past_low_card_ids(tmp_path):
    offers = []
    for offer_id in range(1, 61):
        offers.append(
            f'<offer id="{offer_id}" available="true"><name>Видеокарта Demo {offer_id} 2 ГБ</name>'
            '<price>1000</price><currencyId>RUB</currencyId></offer>'
        )
    offers.append(
        '<offer id="900" available="true"><name>Видеокарта Demo 12 ГБ</name>'
        '<price>9000</price><currencyId>RUB</currencyId></offer>'
    )
    offers.append(
        '<offer id="901" available="false"><name>Видеокарта Demo 80 ГБ</name>'
        '<price>80000</price><currencyId>RUB</currencyId></offer>'
    )
    feed = tmp_path / "feed.xml"
    feed.write_text(
        f"<yml_catalog><shop><offers>{''.join(offers)}</offers></shop></yml_catalog>",
        encoding="utf-8",
    )
    database = tmp_path / "catalog.sqlite"
    build_catalog(feed, database)
    repo = CatalogRepository(database)
    state = QueryState(goal="видеокарта от 48 ГБ", mode="expert", role_ids=["gpu"], filters=[
        Filter(filter_id="vram", key="vram_gb", op="gte", values=[48], strength="require",
               scope="topic", source_turn_id="1", source_text="от 48 ГБ"),
    ])
    found = [candidate.card_id for candidate in repo.retrieve(state, card_budget=50).candidates]
    assert "offer:900" in found
    assert "offer:901" in found


def test_offer_url_prefers_an_available_license(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<yml_catalog><shop><categories>
        <category id="9">Dr.Web Desktop Security Suite</category>
        </categories><offers>
        <offer id="1" group_id="9" available="false">
        <url>https://store.softline.ru/drweb/unavailable-1/</url>
        <name>Dr.Web unavailable</name><price>100</price><currencyId>RUB</currencyId>
        </offer>
        <offer id="2" group_id="9" available="true">
        <url>https://store.softline.ru/drweb/available-2/</url>
        <name>Dr.Web available</name><price>200</price><currencyId>RUB</currencyId>
        </offer>
        </offers></shop></yml_catalog>""", encoding="utf-8",
    )
    database = tmp_path / "catalog.sqlite"
    build_catalog(feed, database)
    repo = CatalogRepository(database)
    card = repo.get_card("group:9")
    assert card.card_url is None
    assert repo.offer_url("group:9") == "https://store.softline.ru/drweb/available-2/"
