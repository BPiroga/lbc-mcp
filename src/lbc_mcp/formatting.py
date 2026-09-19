"""Mise en forme des objets lbc pour Claude, et export sur disque.

Une annonce complete pese lourd (description, dizaines d'attributs, URLs
d'images). Pour qu'une recherche de cent annonces reste lisible par Claude, la
forme compacte ne garde que l'essentiel ; la forme complete est reservee a
get_ad et aux exports.
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import lbc

# Attributs techniques du site (drapeaux d'affichage, logistique interne) qui
# n'apprennent rien sur l'objet vendu.
_NOISE_ATTRIBUTES = {
    "country_isocode3166",
    "payment_methods",
    "shipping_type",
}
_NOISE_PREFIXES = ("is_", "estimated_", "stored_", "profile_")
_NOISE_SUFFIXES = ("_visible", "_url")

EXPORT_COLUMNS = [
    "id",
    "title",
    "price",
    "published",
    "city",
    "zipcode",
    "department",
    "region",
    "category",
    "url",
    "attributes",
    "description",
    "image",
    "latitude",
    "longitude",
]


def _attribute_value(attribute: Any) -> str:
    if attribute.values_label and len(attribute.values_label) > 1:
        return ", ".join(attribute.values_label)
    return attribute.value_label or attribute.value


def _is_noise(key: str) -> bool:
    return key in _NOISE_ATTRIBUTES or key.startswith(_NOISE_PREFIXES) or key.endswith(_NOISE_SUFFIXES)


def ad_attributes(ad: lbc.Ad, full: bool = False) -> dict[str, str]:
    """Attributs d'une annonce, par cle d'API.

    Les cles (mileage, regdate, square...) sont celles des filtres de recherche :
    Claude peut s'en servir telles quelles pour affiner une recherche.
    """
    return {
        key: _attribute_value(attribute)
        for key, attribute in (ad.attributes or {}).items()
        if key and (full or not _is_noise(key)) and not key.endswith("_url")
    }


def ad_to_dict(ad: lbc.Ad, full: bool = False) -> dict[str, Any]:
    location = ad.location
    data: dict[str, Any] = {
        "id": ad.id,
        "title": ad.subject,
        "price": ad.price,
        "published": ad.first_publication_date,
        "city": location.city,
        "zipcode": location.zipcode,
        "department": location.department_name,
        "category": ad.category_name,
        "url": ad.url,
    }
    attributes = ad_attributes(ad, full=full)
    if attributes:
        data["attributes"] = attributes
    if full:
        data |= {
            "description": ad.body,
            "images": ad.images or [],
            "region": location.region_name,
            "latitude": location.lat,
            "longitude": location.lng,
            "status": ad.status,
            "expires": ad.expiration_date,
            "has_phone": ad.has_phone,
            "favorites": ad.favorites,
            # Prive dans lbc, mais c'est la seule facon de relier une annonce a son vendeur.
            "seller_id": ad._user_id,
        }
    return data


def price_stats(ads: list[lbc.Ad]) -> dict[str, Any] | None:
    prices = sorted(ad.price for ad in ads if ad.price)
    if not prices:
        return None
    return {
        "count": len(prices),
        "min": prices[0],
        "median": statistics.median(prices),
        "mean": round(statistics.fmean(prices), 2),
        "max": prices[-1],
    }


def seller_to_dict(user: lbc.User) -> dict[str, Any]:
    feedback = user.feedback
    data: dict[str, Any] = {
        "id": user.id,
        "name": user.name,
        "account_type": user.account_type,
        "registered_at": user.registered_at,
        "location": user.location,
        "total_ads": user.total_ads,
        "rating": {
            "score_out_of_5": round(feedback.score, 2) if feedback.score else None,
            "reviews": feedback.received_count,
        },
        "reply": {
            "rate": user.reply.rate_text,
            "time": user.reply.reply_time_text,
        },
        "last_activity": user.presence.last_activity,
        "badges": [badge.name for badge in user.badges],
        "description": user.description,
        "profile_url": f"https://www.leboncoin.fr/profile/{user.id}",
    }
    if user.pro:
        pro = user.pro
        data["pro"] = {
            "store_name": pro.online_store_name,
            "activity": pro.activity_sector,
            "siren": pro.siren,
            "siret": pro.siret,
            "active_since": pro.active_since,
            "address": pro.location.label or pro.location.address,
            "website": pro.website_url,
            "opening_hours": pro.opening_hours,
            "description": pro.description,
            "external_rating": {
                "value": pro.rating.rating_value,
                "count": pro.rating.user_ratings_total,
                "source": pro.rating.source_display,
            }
            if pro.rating and pro.rating.rating_value
            else None,
        }
    return data


# --- Export ----------------------------------------------------------------


def export_dir() -> Path:
    configured = os.environ.get("LBC_EXPORT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    downloads = Path.home() / "Downloads"
    return (downloads if downloads.is_dir() else Path.home()) / "leboncoin"


def export_filename(label: str, extension: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "recherche"
    return f"leboncoin_{slug}_{datetime.now():%Y%m%d_%H%M%S}.{extension}"


def _export_row(ad: lbc.Ad) -> dict[str, Any]:
    data = ad_to_dict(ad, full=True)
    data["attributes"] = " | ".join(f"{k}: {v}" for k, v in ad_attributes(ad).items())
    data["image"] = ad.images[0] if ad.images else ""
    return {column: data.get(column) for column in EXPORT_COLUMNS}


def write_export(ads: list[lbc.Ad], fmt: str, label: str) -> Path:
    """Ecrit les annonces dans un fichier et renvoie son chemin.

    Le CSV utilise le point-virgule et un BOM UTF-8 : c'est ce qu'Excel en
    francais ouvre directement, accents compris, sans assistant d'import.
    """
    directory = export_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / export_filename(label, fmt)

    if fmt == "json":
        payload = [ad_to_dict(ad, full=True) for ad in ads]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS, delimiter=";")
            writer.writeheader()
            writer.writerows(_export_row(ad) for ad in ads)
    return path
