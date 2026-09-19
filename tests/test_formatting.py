import csv
import json

import lbc

from lbc_mcp.formatting import ad_to_dict, price_stats, seller_to_dict, write_export

from .conftest import make_raw_ad


def test_compact_ad_keeps_useful_attributes_only(raw_ad):
    data = ad_to_dict(lbc.Ad._build(raw_ad, client=None))
    assert data["price"] == 150
    assert data["city"] == "Lyon"
    assert data["attributes"] == {"condition": "Très bon état", "console_brand": "Nintendo"}
    assert "description" not in data


def test_full_ad_has_description_images_and_seller(raw_ad):
    data = ad_to_dict(lbc.Ad._build(raw_ad, client=None), full=True)
    assert data["description"].startswith("Très bon état")
    assert len(data["images"]) == 2
    assert data["seller_id"] == "11111111-2222-4333-8444-555555555555"
    assert data["favorites"] == 4
    assert "profile_picture_url" not in data["attributes"]


def test_price_stats_ignore_ads_without_price():
    ads = [
        lbc.Ad._build(make_raw_ad(ad_id=i, price_cents=cents), client=None)
        for i, cents in enumerate([10000, 30000, None, 20000])
    ]
    assert price_stats(ads) == {"count": 3, "min": 100, "median": 200, "mean": 200, "max": 300}
    assert price_stats([]) is None


def test_csv_export_opens_in_french_excel(tmp_path, monkeypatch, raw_ad):
    monkeypatch.setenv("LBC_EXPORT_DIR", str(tmp_path))
    path = write_export([lbc.Ad._build(raw_ad, client=None)], "csv", "Switch OLED")
    assert path.parent == tmp_path
    assert path.name.startswith("leboncoin_switch-oled_")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM : Excel reconnait l'UTF-8
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert rows[0]["title"] == "Nintendo Switch OLED"
    assert rows[0]["attributes"] == "condition: Très bon état | console_brand: Nintendo"
    assert rows[0]["image"] == "https://img.leboncoin.fr/a.jpg"


def test_json_export(tmp_path, monkeypatch, raw_ad):
    monkeypatch.setenv("LBC_EXPORT_DIR", str(tmp_path))
    path = write_export([lbc.Ad._build(raw_ad, client=None)], "json", "")
    ads = json.loads(path.read_text(encoding="utf-8"))
    assert ads[0]["id"] == 1234567890
    assert "leboncoin_recherche_" in path.name


def test_seller_to_dict():
    user = lbc.User._build(
        user_data={
            "user_id": "11111111-2222-4333-8444-555555555555",
            "name": "Camille",
            "account_type": "private",
            "registered_at": "2024-01-15",
            "total_ads": 2,
            "feedback": {"overall_score": 0.96, "received_count": 12},
            "reply": {"rate_text": "100%", "reply_time_text": "en moins d'une heure"},
            "badges": [{"type": "x", "name": "Pièce d'identité vérifiée"}],
        },
        pro_data=None,
    )
    data = seller_to_dict(user)
    assert data["rating"] == {"score_out_of_5": 4.8, "reviews": 12}
    assert data["badges"] == ["Pièce d'identité vérifiée"]
    assert "pro" not in data
