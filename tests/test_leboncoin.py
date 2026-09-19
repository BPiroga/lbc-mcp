import time

import lbc
import pytest
from lbc.exceptions import DatadomeError, NotFoundError
from lbc.utils import build_search_payload_with_args, build_search_payload_with_url

from lbc_mcp.leboncoin import (
    RANGE_CEILING,
    SORTS,
    Criteria,
    Leboncoin,
    LeboncoinError,
    clean_search_url,
    normalize_filter,
    parse_ad_id,
    parse_department,
    parse_region,
    prepare_search,
    proxy_from_env,
)


def test_price_sorts_follow_the_real_order_not_lbc_names():
    assert SORTS["price_asc"].value == ("price", "asc")
    assert SORTS["price_desc"].value == ("price", "desc")


def test_clean_search_url_decodes_values_and_extracts_page():
    url, page, notes = clean_search_url(
        "https://www.leboncoin.fr/recherche?page=3&category=2&text=clio%204&price=5000-12000&kst=r"
    )
    assert page == 3
    assert notes == []
    payload = build_search_payload_with_url(url)
    assert payload["filters"]["keywords"] == {"text": "clio 4"}
    assert payload["filters"]["category"] == {"id": "2"}
    assert payload["filters"]["ranges"]["price"] == {"min": 5000, "max": 12000}
    assert "page" not in payload["filters"].get("enums", {})


def test_clean_search_url_puts_dependent_keys_after_their_parent():
    url, _, _ = clean_search_url(
        "https://www.leboncoin.fr/recherche?shippable=1&search_in=subject"
        "&locations=d_69&text=velo"
    )
    payload = build_search_payload_with_url(url)
    assert payload["filters"]["keywords"] == {"text": "velo", "type": "subject"}
    assert payload["filters"]["location"]["shippable"] is True


def test_clean_search_url_drops_shippable_without_location():
    url, _, notes = clean_search_url("https://www.leboncoin.fr/recherche?text=velo&shippable=1")
    assert "shippable" not in url
    assert notes
    build_search_payload_with_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/recherche?text=velo", "https://www.leboncoin.fr/recherche"],
)
def test_clean_search_url_rejects_other_pages(url):
    with pytest.raises(LeboncoinError):
        clean_search_url(url)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("69", lbc.Department.RHONE),
        ("05", lbc.Department.HAUTES_ALPES),
        ("Gironde", lbc.Department.GIRONDE),
        ("Côtes-d'Armor", lbc.Department.COTES_DARMOR),
        ("val d'oise", lbc.Department.VAL_DOISE),
    ],
)
def test_parse_department(value, expected):
    assert parse_department(value) is expected


def test_parse_department_explains_missing_corsica():
    with pytest.raises(LeboncoinError, match="CORSE"):
        parse_department("2A")


def test_parse_region_accepts_accents():
    assert parse_region("Île-de-France") is lbc.Region.ILE_DE_FRANCE


@pytest.mark.parametrize(
    "value",
    [
        "1234567890",
        "https://www.leboncoin.fr/ad/consoles/1234567890",
        "https://www.leboncoin.fr/consoles/1234567890.htm?ac=1",
    ],
)
def test_parse_ad_id(value):
    assert parse_ad_id(value) == 1234567890


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([40, 80], [40, 80]),
        ([None, 100000], [0, 100000]),
        ({"min": 3}, [3, RANGE_CEILING]),
        (["1", "2"], ["1", "2"]),
        ([1, 2, 3], ["1", "2", "3"]),
        ("1", ["1"]),
    ],
)
def test_normalize_filter(value, expected):
    assert normalize_filter("key", value) == expected


@pytest.mark.parametrize("value", [[None, None], {"min": 9, "max": 1}, {"low": 1}, []])
def test_normalize_filter_rejects(value):
    with pytest.raises(LeboncoinError):
        normalize_filter("key", value)


def test_prepare_search_builds_a_payload_lbc_accepts():
    prepared = prepare_search(
        Criteria(
            query="maison",
            category="immobilier_ventes_immobilieres",
            departments=["33"],
            regions=["BRETAGNE"],
            price_max=300000,
            sort="price_asc",
            seller_type="private",
            filters={"square": [80, None], "real_estate_type": ["1"]},
        )
    )
    payload = build_search_payload_with_args(**prepared.kwargs)
    filters = payload["filters"]
    assert filters["category"] == {"id": "9"}
    assert filters["keywords"] == {"text": "maison"}
    assert filters["ranges"]["price"] == {"min": 0, "max": 300000}
    assert filters["ranges"]["square"] == {"min": 80, "max": RANGE_CEILING}
    assert filters["enums"]["real_estate_type"] == ["1"]
    assert payload["owner_type"] == "private"
    assert (payload["sort_by"], payload["sort_order"]) == ("price", "asc")
    location_types = [loc["locationType"] for loc in filters["location"]["locations"]]
    assert location_types == ["region", "department"]


def test_prepare_search_with_url_ignores_other_criteria():
    prepared = prepare_search(
        Criteria(query="ignored", url="https://www.leboncoin.fr/recherche?text=velo&page=2")
    )
    assert prepared.kwargs == {"url": "https://www.leboncoin.fr/recherche?text=velo"}
    assert prepared.first_page == 2


def test_proxy_from_env(monkeypatch):
    monkeypatch.setenv("LBC_PROXY", "http://user:p%40ss@10.0.0.1:8080")
    proxy = proxy_from_env()
    assert proxy.url == "http://user:p@ss@10.0.0.1:8080"
    monkeypatch.delenv("LBC_PROXY")
    assert proxy_from_env() is None


def test_calls_are_spaced():
    gateway = Leboncoin(min_interval=0.2)
    gateway._client = object()  # pas de vraie session
    start = time.monotonic()
    gateway._call(lambda client: None)
    gateway._call(lambda client: None)
    assert time.monotonic() - start >= 0.18  # horloge Windows : ~15 ms de resolution


def test_datadome_block_is_explained_and_resets_the_session():
    gateway = Leboncoin(min_interval=0)
    gateway._client = object()

    def blocked(client):
        raise DatadomeError("403")

    with pytest.raises(LeboncoinError, match="Datadome"):
        gateway._call(blocked)
    assert gateway._client is None


def test_not_found_is_explained():
    gateway = Leboncoin(min_interval=0)
    gateway._client = object()

    def missing(client):
        raise NotFoundError("404")

    with pytest.raises(LeboncoinError, match="Introuvable"):
        gateway._call(missing)
