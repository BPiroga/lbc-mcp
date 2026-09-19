"""Essai reel de bout en bout : lance le serveur comme le ferait Claude.

Le serveur tourne dans un sous-processus et on lui parle en MCP sur
stdin/stdout. Chaque outil est appele une fois contre le vrai Leboncoin, soit
une demi-douzaine de requetes espacees : assez pour tout verifier, trop peu
pour se faire bloquer par Datadome.

    .venv\\Scripts\\python scripts\\smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import Client, StdioServerParameters


def payload(result) -> dict:
    text = result.content[0].text
    if result.is_error:
        raise SystemExit(f"ECHEC : {text}")
    return json.loads(text)


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    server = StdioServerParameters(command=sys.executable, args=["-m", "lbc_mcp"])
    async with Client(server) as client:
        tools = [tool.name for tool in (await client.list_tools()).tools]
        print("outils :", tools)

        search = payload(
            await client.call_tool(
                "search_ads",
                {
                    "query": "nintendo switch",
                    "category": "ELECTRONIQUE_CONSOLES",
                    "city": "Lyon",
                    "radius_km": 30,
                    "price_max": 250,
                    "sort": "price_asc",
                    "limit": 10,
                },
            )
        )
        prices = [ad["price"] for ad in search["ads"]]
        print(f"\nrecherche : {search['total']} au total, {search['returned']} recues")
        print("notes :", search.get("notes"))
        print("prix croissants :", prices, "->", prices == sorted(p for p in prices if p))
        for ad in search["ads"][:3]:
            print(f"  {ad['price']:>6} €  {ad['title'][:50]:50}  {ad['city']}  {ad.get('attributes')}")

        ad = payload(await client.call_tool("get_ad", {"ad": search["ads"][0]["url"]}))
        print(f"\nannonce {ad['id']} : {ad['title']} — {len(ad['images'])} photos, "
              f"{ad['favorites']} favoris, vendeur {ad['seller_id']}")

        seller = payload(await client.call_tool("get_seller", {"seller": ad["seller_id"]}))
        print(f"vendeur : {seller['name']} ({seller['account_type']}), {seller['total_ads']} annonces, "
              f"note {seller['rating']}, inscrit le {seller['registered_at']}")

        url_search = payload(
            await client.call_tool(
                "search_ads",
                {
                    "url": "https://www.leboncoin.fr/recherche?category=2&text=clio%204"
                    "&locations=d_33&price=3000-12000&mileage=min-150000",
                    "limit": 100,
                    "pages": 2,
                    "export_format": "csv",
                },
            )
        )
        print(f"\nexport : {url_search['returned']} annonces sur {url_search['total']} "
              f"-> {url_search['export_file']}")
        print("stats prix :", url_search["price_stats"])
        print("page suivante :", url_search["next_page"])


if __name__ == "__main__":
    asyncio.run(main())
