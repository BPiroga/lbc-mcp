"""Serveur MCP : les outils Leboncoin exposes a Claude."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

import anyio.to_thread
import lbc
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .formatting import ad_to_dict, price_stats, seller_to_dict, write_export
from .leboncoin import Criteria, Leboncoin, LeboncoinError, prepare_search

T = TypeVar("T")

# Construits depuis lbc pour suivre ses mises a jour : Claude voit la liste
# exacte des valeurs admises dans le schema de l'outil.
CategoryName = Literal[tuple(category.name for category in lbc.Category)]  # type: ignore[valid-type]
RegionName = Literal[tuple(region.name for region in lbc.Region)]  # type: ignore[valid-type]

# Au-dela, la reponse ne tient plus confortablement dans le contexte de Claude.
MAX_INLINE_RESULTS = 300

INSTRUCTIONS = """\
Recherche d'annonces Leboncoin (France), via la bibliotheque Python lbc.

- search_ads cherche des annonces ; get_ad donne le detail d'une annonce ;
  get_seller le profil et la note d'un vendeur.
- Pour un filtre que search_ads n'expose pas directement, le plus sur est de
  demander a l'utilisateur l'URL d'une recherche faite sur leboncoin.fr et de
  la passer dans `url` : elle est reproduite a l'identique.
- Les requetes sont espacees (2 s par defaut) : Leboncoin bloque temporairement
  l'adresse IP (Datadome) apres une rafale. Eviter les appels inutiles, et pour
  un gros volume utiliser `pages` avec `export_format` plutot que d'enchainer
  les appels.
"""

mcp = MCPServer(name="leboncoin", instructions=INSTRUCTIONS)
gateway = Leboncoin()

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


async def _run(function: Callable[..., T], *args: Any) -> T:
    """Execute un appel lbc (bloquant) hors de la boucle d'evenements."""
    try:
        return await anyio.to_thread.run_sync(function, *args)
    except LeboncoinError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(title="Rechercher des annonces Leboncoin", annotations=READ_ONLY)
async def search_ads(
    ctx: Context,
    query: Annotated[
        str | None,
        Field(description="Mots-cles. 'velo OR trottinette' pour l'un ou l'autre."),
    ] = None,
    category: Annotated[
        CategoryName,
        Field(description="Categorie ; les sous-categories sont prefixees par leur parent."),
    ] = "TOUTES_CATEGORIES",
    city: Annotated[
        str | None,
        Field(
            description="Commune francaise autour de laquelle chercher : nom ('Lyon'), "
            "code postal ('69003') ou 'Saint-Denis (974)' pour lever une homonymie."
        ),
    ] = None,
    radius_km: Annotated[float, Field(ge=1, le=200, description="Rayon autour de city.")] = 10,
    departments: Annotated[
        list[str] | None,
        Field(description="Numeros ('69', '5') ou noms ('Gironde') de departements."),
    ] = None,
    regions: Annotated[list[RegionName] | None, Field(description="Regions.")] = None,
    price_min: Annotated[int | None, Field(ge=0, description="Prix minimum en euros.")] = None,
    price_max: Annotated[int | None, Field(ge=0, description="Prix maximum en euros.")] = None,
    sort: Literal["relevance", "newest", "oldest", "price_asc", "price_desc"] = "relevance",
    seller_type: Annotated[
        Literal["all", "private", "pro"],
        Field(description="Particuliers, professionnels ou les deux."),
    ] = "all",
    shippable_only: Annotated[bool, Field(description="Seulement avec livraison.")] = False,
    title_only: Annotated[bool, Field(description="Chercher les mots-cles dans le titre seul.")] = False,
    ad_type: Annotated[
        Literal["offer", "demand"],
        Field(description="offer : annonces de vente/offre ; demand : demandes."),
    ] = "offer",
    filters: Annotated[
        dict[str, dict[str, int | None] | list[str | int | None]] | None,
        Field(
            description="Filtres avances, par cle d'API Leboncoin. Intervalle : [min, max] ou "
            "{'min': x, 'max': y}, une borne pouvant etre null. Enumeration : liste de codes "
            "texte. Exemples : {'square': [40, 80], 'rooms': [3, null], "
            "'real_estate_type': ['1', '2']} (1 maison, 2 appartement), "
            "{'mileage': [null, 100000], 'regdate': [2016, null]}. Les cles des attributs "
            "renvoyes avec chaque annonce sont des cles de filtre valides."
        ),
    ] = None,
    url: Annotated[
        str | None,
        Field(
            description="URL d'une recherche faite sur leboncoin.fr. Si fournie, elle remplace "
            "tous les autres criteres (seuls limit, page, pages et export_format comptent)."
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Annonces par page.")] = 35,
    page: Annotated[int, Field(ge=1, description="Premiere page a recuperer.")] = 1,
    pages: Annotated[
        int,
        Field(ge=1, le=50, description="Nombre de pages consecutives a recuperer."),
    ] = 1,
    export_format: Annotated[
        Literal["csv", "json"] | None,
        Field(
            description="Ecrit toutes les annonces trouvees dans un fichier (description "
            "complete comprise) et ne renvoie qu'un resume et un echantillon. Indispensable "
            f"au-dela de {MAX_INLINE_RESULTS} annonces."
        ),
    ] = None,
) -> dict[str, Any]:
    """Recherche des annonces sur Leboncoin.

    Renvoie les totaux du site, des statistiques de prix sur les annonces
    recuperees et, pour chacune : id, titre, prix, date, lieu, categorie, URL
    et attributs utiles (etat, kilometrage, surface...). next_page indique la
    page a demander pour continuer. Pour le detail d'une annonce (description,
    photos, vendeur), utiliser get_ad.
    """
    if export_format is None and limit * pages > MAX_INLINE_RESULTS:
        raise ToolError(
            f"{limit * pages} annonces demandees : au-dela de {MAX_INLINE_RESULTS}, "
            "utiliser export_format='csv' ou 'json'."
        )

    criteria = Criteria(
        query=query,
        url=url,
        category=category,
        city=city,
        radius_km=radius_km,
        departments=departments or [],
        regions=regions or [],
        price_min=price_min,
        price_max=price_max,
        sort=sort,
        seller_type=seller_type,
        shippable_only=shippable_only,
        title_only=title_only,
        ad_type=ad_type,
        filters=filters or {},
    )
    prepared = await _run(prepare_search, criteria)
    start = page if page > 1 else prepared.first_page
    notes = list(prepared.notes)

    ads: list[lbc.Ad] = []
    seen: set[int] = set()
    first_result: lbc.Search | None = None
    next_page: int | None = start
    for current in range(start, start + pages):
        try:
            result = await _run(gateway.search_page, prepared, limit, current)
        except ToolError as exc:
            if not ads:
                raise
            notes.append(f"Arret a la page {current} : {exc}")
            break
        first_result = first_result or result
        # Les annonces glissent d'une page a l'autre quand de nouvelles arrivent.
        for ad in result.ads:
            if ad.id not in seen:
                seen.add(ad.id)
                ads.append(ad)
        next_page = current + 1
        if pages > 1:
            await ctx.report_progress(current - start + 1, pages, f"{len(ads)} annonces")
        if not result.ads or (result.max_pages and current >= result.max_pages):
            next_page = None
            break

    assert first_result is not None
    response: dict[str, Any] = {
        "total": first_result.total,
        "total_private": first_result.total_private,
        "total_pro": first_result.total_pro,
        "total_shippable": first_result.total_shippable,
        "returned": len(ads),
        "next_page": next_page,
        "price_stats": price_stats(ads),
    }
    if notes:
        response["notes"] = notes

    if export_format:
        label = query or (category if category != "TOUTES_CATEGORIES" else "recherche")
        path = await anyio.to_thread.run_sync(write_export, ads, export_format, label)
        response["export_file"] = str(path)
        response["sample"] = [ad_to_dict(ad) for ad in ads[:5]]
    else:
        response["ads"] = [ad_to_dict(ad) for ad in ads]
    return response


@mcp.tool(title="Detail d'une annonce Leboncoin", annotations=READ_ONLY)
async def get_ad(
    ad: Annotated[str, Field(description="Identifiant numerique ou URL de l'annonce.")],
) -> dict[str, Any]:
    """Detail complet d'une annonce : description, photos, tous les attributs,
    coordonnees, nombre de favoris et identifiant du vendeur (seller_id)."""
    return ad_to_dict(await _run(gateway.get_ad, ad), full=True)


@mcp.tool(title="Profil d'un vendeur Leboncoin", annotations=READ_ONLY)
async def get_seller(
    seller: Annotated[
        str,
        Field(description="seller_id renvoye par get_ad, ou identifiant/URL d'une annonce du vendeur."),
    ],
) -> dict[str, Any]:
    """Profil d'un vendeur : anciennete, note sur 5 et nombre d'avis, taux et
    delai de reponse, nombre d'annonces en ligne, et pour un professionnel sa
    boutique (SIRET, adresse, site web)."""
    user, ad = await _run(gateway.get_seller, seller)
    data = seller_to_dict(user)
    if ad is not None:
        data["from_ad"] = {"id": ad.id, "title": ad.subject}
    return data


def main() -> None:
    mcp.run()
