import pytest


def make_raw_ad(ad_id: int = 1234567890, price_cents: int | None = 15000, **overrides) -> dict:
    """Annonce au format de l'API Leboncoin, reduite a ce que lbc lit."""
    raw = {
        "list_id": ad_id,
        "first_publication_date": "2026-09-01 10:00:00",
        "expiration_date": "2026-10-31 10:00:00",
        "index_date": "2026-09-01 10:00:00",
        "status": "active",
        "category_id": "43",
        "category_name": "Consoles",
        "subject": "Nintendo Switch OLED",
        "body": "Très bon état, peu servie.",
        "brand": "leboncoin",
        "ad_type": "offer",
        "url": f"https://www.leboncoin.fr/ad/consoles/{ad_id}",
        "price_cents": price_cents,
        "images": {"urls_large": ["https://img.leboncoin.fr/a.jpg", "https://img.leboncoin.fr/b.jpg"]},
        "attributes": [
            {"key": "condition", "value": "3", "value_label": "Très bon état", "generic": True},
            {"key": "console_brand", "value": "nintendo", "value_label": "Nintendo", "generic": True},
            {"key": "is_bundleable", "value": "true", "value_label": "true", "generic": False},
            {"key": "profile_picture_url", "value": "https://x", "value_label": "https://x"},
            {"key": "negotiation_cta_visible", "value": "true", "value_label": "true"},
        ],
        "location": {
            "region_name": "Rhône-Alpes",
            "department_name": "Rhône",
            "city": "Lyon",
            "zipcode": "69003",
            "lat": 45.76,
            "lng": 4.85,
        },
        "has_phone": False,
        "counters": {"favorites": 4},
        "owner": {"user_id": "11111111-2222-4333-8444-555555555555", "type": "private"},
    }
    raw.update(overrides)
    return raw


@pytest.fixture
def raw_ad():
    return make_raw_ad()
