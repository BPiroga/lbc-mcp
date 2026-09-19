"""Acces a Leboncoin, entierement delegue a la bibliotheque lbc.

Ce module traduit les criteres recus par les outils MCP en appels lbc, et
rien de plus : lbc s'occupe de la session, de l'imitation d'un navigateur et
du format de l'API.

Leboncoin est protege par Datadome, qui filtre d'abord sur l'empreinte TLS du
client. lbc en tire une au hasard parmi quatre ; lors des essais du
19/09/2026, seule chrome_android passait (8 sur 8), les autres etant refusees
des la premiere requete. On la fixe donc, sauf si LBC_IMPERSONATE dit autre
chose. Le seuil de debit tolere n'etant pas connu, toutes les requetes passent
en plus par un client unique, derriere un verrou qui les espace d'au moins
LBC_MIN_INTERVAL secondes, meme quand plusieurs outils sont appeles en parallele.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, TypeVar

import lbc
from curl_cffi.requests.exceptions import RequestException
from lbc.exceptions import DatadomeError, InvalidValue, NotFoundError, RequestError

T = TypeVar("T")

# Les deux tris par prix sont inverses dans lbc 1.1.6 : Sort.CHEAPEST envoie
# price/desc et renvoie les annonces les plus cheres en premier. On choisit donc
# le membre par sa valeur, ce qui reste juste le jour ou lbc corrige les noms.
SORTS: dict[str, lbc.Sort] = {
    "relevance": lbc.Sort(("relevance", None)),
    "newest": lbc.Sort(("time", "desc")),
    "oldest": lbc.Sort(("time", "asc")),
    "price_asc": lbc.Sort(("price", "asc")),
    "price_desc": lbc.Sort(("price", "desc")),
}

# L'API accepte une borne seule, mais lbc exige les deux bornes d'un intervalle.
# Une borne absente est remplacee par une valeur hors d'atteinte.
RANGE_FLOOR = 0
RANGE_CEILING = 999_999_999

DEFAULT_IMPERSONATE = "chrome_android"

BLOCKED_MESSAGE = (
    "Leboncoin (Datadome) a refuse la requete. Attendre quelques minutes avant de "
    "relancer, espacer davantage les requetes (variable LBC_MIN_INTERVAL), essayer "
    "une autre empreinte de navigateur (variable LBC_IMPERSONATE) ou passer par un "
    "proxy residentiel francais (variable LBC_PROXY)."
)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class LeboncoinError(Exception):
    """Echec anticipe, dont le message peut etre montre tel quel a Claude."""


# --- Criteres de recherche -------------------------------------------------


@dataclass
class Criteria:
    """Criteres d'une recherche, tels que les outils MCP les recoivent."""

    query: str | None = None
    url: str | None = None
    category: str = "TOUTES_CATEGORIES"
    city: str | None = None
    radius_km: float = 10
    departments: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    price_min: int | None = None
    price_max: int | None = None
    sort: str = "relevance"
    seller_type: str = "all"
    shippable_only: bool = False
    title_only: bool = False
    ad_type: str = "offer"
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedSearch:
    """Arguments prets pour `lbc.Client.search`, avec ce qu'il faut en dire."""

    kwargs: dict[str, Any]
    first_page: int = 1
    notes: list[str] = field(default_factory=list)


def prepare_search(criteria: Criteria) -> PreparedSearch:
    """Traduit des criteres en arguments lbc. Peut interroger l'API geo (ville)."""
    if criteria.url:
        url, page, notes = clean_search_url(criteria.url)
        return PreparedSearch(kwargs={"url": url}, first_page=page, notes=notes)

    try:
        category = lbc.Category[criteria.category.upper()]
    except KeyError:
        raise LeboncoinError(f"Categorie inconnue : {criteria.category!r}.") from None

    kwargs: dict[str, Any] = {
        "text": (criteria.query or "").strip() or None,
        "category": category,
        "sort": SORTS[criteria.sort],
        "ad_type": lbc.AdType(criteria.ad_type),
        "shippable": criteria.shippable_only or None,
        "search_in_title_only": criteria.title_only,
    }
    if criteria.seller_type != "all":
        kwargs["owner_type"] = lbc.OwnerType(criteria.seller_type)

    notes: list[str] = []
    locations: list[Any] = [parse_region(r) for r in criteria.regions]
    locations += [parse_department(d) for d in criteria.departments]
    if criteria.city:
        name, dept, lat, lng = geocode_city(criteria.city)
        radius_m = max(1, round(criteria.radius_km * 1000))
        locations.append(lbc.City(lat=lat, lng=lng, radius=radius_m, city=name))
        notes.append(f"Ville retenue : {name} ({dept}), rayon {criteria.radius_km:g} km.")
    if locations:
        kwargs["locations"] = locations

    filters = dict(criteria.filters)
    if criteria.price_min is not None or criteria.price_max is not None:
        filters["price"] = {"min": criteria.price_min, "max": criteria.price_max}
    for key, value in filters.items():
        kwargs[key] = normalize_filter(key, value)

    return PreparedSearch(kwargs=kwargs, notes=notes)


def normalize_filter(key: str, value: Any) -> list[Any]:
    """Met un filtre avance sous la forme attendue par lbc.

    lbc range une liste d'entiers dans les intervalles et une liste de chaines
    dans les enumerations. On accepte en plus {"min": .., "max": ..} et les
    bornes nulles, que lbc refuse.
    """
    if isinstance(value, dict):
        unknown = set(value) - {"min", "max"}
        if unknown:
            raise LeboncoinError(f"Filtre {key!r} : cles inconnues {sorted(unknown)}.")
        return _range(key, value.get("min"), value.get("max"))

    if isinstance(value, (str, int, float)):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        raise LeboncoinError(f"Filtre {key!r} : liste ou {{min, max}} attendu.")

    if all(isinstance(v, str) for v in value):
        return list(value)
    if len(value) == 2 and all(v is None or isinstance(v, (int, float)) for v in value):
        return _range(key, value[0], value[1])
    if all(isinstance(v, (int, str)) for v in value):
        return [str(v) for v in value]
    raise LeboncoinError(
        f"Filtre {key!r} : utiliser [min, max] pour un intervalle ou une liste de "
        "codes texte pour une enumeration."
    )


def _range(key: str, low: Any, high: Any) -> list[int]:
    if low is None and high is None:
        raise LeboncoinError(f"Filtre {key!r} : au moins une borne est requise.")
    try:
        low = RANGE_FLOOR if low is None else int(low)
        high = RANGE_CEILING if high is None else int(high)
    except (TypeError, ValueError):
        raise LeboncoinError(f"Filtre {key!r} : les bornes doivent etre des nombres.") from None
    if low > high:
        raise LeboncoinError(f"Filtre {key!r} : le minimum depasse le maximum.")
    return [low, high]


# --- URL de recherche ------------------------------------------------------

# Ordre impose par lbc : "search_in" modifie le filtre cree par "text", et
# "shippable" celui cree par "locations". Dans le desordre, lbc plante.
_URL_KEY_ORDER = {"text": 0, "search_in": 1, "locations": 2}
# Parametres de navigation du site, qui ne sont pas des filtres.
_URL_IGNORED_KEYS = {"page", "kst", "from"}


def clean_search_url(url: str) -> tuple[str, int, list[str]]:
    """Rend une URL de recherche Leboncoin digeste pour lbc.

    lbc ne decode pas les valeurs (text=clio%204 part tel quel), traite
    `page` comme un filtre et plante sur certains ordres de parametres. On
    reconstruit donc l'URL avec des valeurs decodees, dans un ordre sur, et on
    rend la page demandee a part.
    """
    parts = urllib.parse.urlsplit(url.strip())
    if not parts.netloc.endswith("leboncoin.fr") or not parts.query:
        raise LeboncoinError(
            "URL attendue : une page de recherche leboncoin.fr avec ses filtres, "
            "par exemple https://www.leboncoin.fr/recherche?category=2&text=clio"
        )

    page = 1
    kept: dict[str, str] = {}
    for key, value in urllib.parse.parse_qsl(parts.query):
        if key == "page":
            page = int(value) if value.isdigit() and int(value) > 0 else 1
        elif key not in _URL_IGNORED_KEYS:
            # lbc decoupe la requete sur & et = : ils ne doivent pas survivre au decodage.
            kept[key] = value.replace("&", " ").replace("=", " ")

    notes: list[str] = []
    if "search_in" in kept and "text" not in kept:
        del kept["search_in"]
    if "shippable" in kept and "locations" not in kept:
        del kept["shippable"]
        notes.append(
            "Filtre 'livraison possible' ignore : lbc ne le gere dans une URL "
            "qu'accompagne d'une localisation. Utiliser shippable_only a la place."
        )
    if not kept:
        raise LeboncoinError("Cette URL ne contient aucun filtre de recherche.")

    ordered = sorted(kept, key=lambda k: _URL_KEY_ORDER.get(k, len(_URL_KEY_ORDER)))
    query = "&".join(f"{k}={kept[k]}" for k in ordered)
    return f"https://www.leboncoin.fr/recherche?{query}", page, notes


# --- Localisation ----------------------------------------------------------


def _name_key(text: str) -> str:
    """Cle de comparaison sans accents ni ponctuation : lbc ecrit VAL_DOISE,
    l'utilisateur "Val-d'Oise"."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", text.upper())


_DEPARTMENTS_BY_CODE = {d.value[2]: d for d in lbc.Department}
_DEPARTMENTS_BY_NAME = {_name_key(d.name): d for d in lbc.Department}
_REGIONS_BY_NAME = {_name_key(r.name): r for r in lbc.Region}


def parse_department(value: str) -> lbc.Department:
    """Accepte un numero ("69", "05") ou un nom ("Rhone", "Cotes-d'Armor")."""
    raw = str(value).strip()
    code = raw.lstrip("0") if raw.isdigit() else raw.upper()
    department = _DEPARTMENTS_BY_CODE.get(code) or _DEPARTMENTS_BY_NAME.get(_name_key(raw))
    if department is None:
        raise LeboncoinError(
            f"Departement inconnu : {value!r}. lbc ne connait que la metropole hors "
            "Corse ; pour la Corse et l'outre-mer, passer par regions "
            "(CORSE, GUADELOUPE, MARTINIQUE, GUYANE, REUNION)."
        )
    return department


def parse_region(value: str) -> lbc.Region:
    region = _REGIONS_BY_NAME.get(_name_key(value))
    if region is None:
        raise LeboncoinError(f"Region inconnue : {value!r}.")
    return region


_CITY_WITH_DEPT = re.compile(r"^(?P<name>.+?)\s*\((?P<dept>\d{2,3}|2[AB])\)$", re.I)


@lru_cache(maxsize=256)
def geocode_city(query: str) -> tuple[str, str, float, float]:
    """Trouve le centre d'une commune francaise : (nom, departement, lat, lng).

    lbc attend des coordonnees et ne sait pas les trouver. On interroge l'API
    publique geo.api.gouv.fr, sans cle. Accepte "Lyon", "69003" ou, pour lever
    une homonymie, "Saint-Denis (974)". A nom egal, la plus peuplee l'emporte.
    """
    text = query.strip()
    params = {"fields": "nom,centre,codeDepartement", "limit": "1"}
    if re.fullmatch(r"\d{5}", text):
        params["codePostal"] = text
    else:
        match = _CITY_WITH_DEPT.match(text)
        if match:
            text = match["name"]
            params["codeDepartement"] = match["dept"].upper().zfill(2)
        params |= {"nom": text, "boost": "population"}

    url = "https://geo.api.gouv.fr/communes?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            communes = json.load(response)
    except OSError as exc:
        raise LeboncoinError(f"Localisation de {query!r} impossible : {exc}.") from None

    if not communes or not communes[0].get("centre"):
        raise LeboncoinError(
            f"Commune introuvable : {query!r}. Donner un nom de commune francaise, "
            "un code postal, ou 'Nom (departement)'."
        )
    commune = communes[0]
    lng, lat = commune["centre"]["coordinates"]
    return commune["nom"], commune.get("codeDepartement", "?"), lat, lng


# --- Identifiants ----------------------------------------------------------


def parse_ad_id(value: str | int) -> int:
    """Accepte un identifiant d'annonce ou son URL."""
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    path = urllib.parse.urlsplit(text).path
    numbers = re.findall(r"\d{6,}", path)
    if not numbers:
        raise LeboncoinError(f"Identifiant ou URL d'annonce invalide : {value!r}.")
    return int(numbers[-1])


# --- Client ----------------------------------------------------------------


def proxy_from_env() -> lbc.Proxy | None:
    """Lit LBC_PROXY, de la forme http://utilisateur:motdepasse@hote:port."""
    raw = os.environ.get("LBC_PROXY", "").strip()
    if not raw:
        return None
    parts = urllib.parse.urlsplit(raw if "://" in raw else f"http://{raw}")
    if not parts.hostname or not parts.port:
        raise LeboncoinError("LBC_PROXY invalide : forme attendue http://user:pass@hote:port.")
    return lbc.Proxy(
        host=parts.hostname,
        port=parts.port,
        username=urllib.parse.unquote(parts.username) if parts.username else None,
        password=urllib.parse.unquote(parts.password) if parts.password else None,
        scheme=parts.scheme,
    )


class Leboncoin:
    """Client lbc partage, dont les requetes sont espacees et serialisees."""

    def __init__(self, min_interval: float | None = None):
        if min_interval is None:
            min_interval = float(os.environ.get("LBC_MIN_INTERVAL", "2"))
        self.min_interval = min_interval
        self._client: lbc.Client | None = None
        self._lock = threading.Lock()
        self._last_request = 0.0

    def _call(self, action: Callable[[lbc.Client], T]) -> T:
        with self._lock:
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                if self._client is None:
                    # Une seule relance : lbc la fait sans attendre, apres avoir
                    # recree sa session (une requete de plus). Les enchainer
                    # contre Datadome ne fait que prolonger le blocage.
                    self._client = lbc.Client(
                        proxy=proxy_from_env(),
                        impersonate=os.environ.get("LBC_IMPERSONATE") or DEFAULT_IMPERSONATE,
                        max_retries=1,
                    )
                return action(self._client)
            except DatadomeError:
                self._client = None  # repartir d'une session neuve la prochaine fois
                raise LeboncoinError(BLOCKED_MESSAGE) from None
            except NotFoundError:
                raise LeboncoinError("Introuvable : annonce retiree ou identifiant errone.") from None
            except InvalidValue as exc:
                raise LeboncoinError(f"Critere refuse par lbc : {exc}") from None
            except RequestError as exc:
                raise LeboncoinError(f"Leboncoin a refuse la requete : {exc}") from None
            except RequestException as exc:
                self._client = None
                raise LeboncoinError(f"Leboncoin injoignable : {exc}") from None
            finally:
                self._last_request = time.monotonic()

    def search_page(self, prepared: PreparedSearch, limit: int, page: int) -> lbc.Search:
        return self._call(lambda client: client.search(**prepared.kwargs, limit=limit, page=page))

    def get_ad(self, ad: str | int) -> lbc.Ad:
        ad_id = parse_ad_id(ad)
        return self._call(lambda client: client.get_ad(ad_id))

    def get_user(self, user_id: str) -> lbc.User:
        return self._call(lambda client: client.get_user(user_id))

    def get_seller(self, reference: str) -> tuple[lbc.User, lbc.Ad | None]:
        """Profil d'un vendeur, a partir de son identifiant ou d'une de ses annonces."""
        reference = reference.strip()
        if UUID_RE.match(reference):
            return self.get_user(reference), None
        ad = self.get_ad(reference)
        if not ad._user_id:
            raise LeboncoinError("Cette annonce n'indique pas son vendeur.")
        return self.get_user(ad._user_id), ad
