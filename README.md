# lbc-mcp

Serveur [MCP](https://modelcontextprotocol.io) qui permet à Claude de chercher des annonces Leboncoin : recherche multi-critères, pages enchaînées, export CSV/JSON, détail d'une annonce et profil du vendeur.

Tout l'accès à Leboncoin repose sur [lbc](https://github.com/etienne-hd/lbc) d'etienne-hd, qui interroge l'API mobile du site. Ce serveur se contente d'exposer lbc à Claude, en y ajoutant ce qui manque pour un usage conversationnel : localisation par nom de ville, filtres tolérants, pagination, espacement des requêtes, export.

## Les outils

| Outil | Rôle |
|---|---|
| `search_ads` | Recherche par mots-clés, catégorie, ville + rayon, départements, régions, prix, type de vendeur, livraison, filtres avancés (surface, kilométrage, année…), ou directement à partir d'une URL de recherche leboncoin.fr. Récupère jusqu'à 50 pages d'un coup et peut tout écrire dans un fichier CSV ou JSON. |
| `get_ad` | Détail complet d'une annonce (description, photos, attributs, favoris) à partir de son identifiant ou de son URL. |
| `get_seller` | Profil d'un vendeur : note, nombre d'avis, ancienneté, taux de réponse et, pour un professionnel, sa boutique (SIRET, adresse, site). |

Quelques demandes qu'on peut faire à Claude une fois le serveur branché :

- « Trouve des Nintendo Switch OLED à moins de 200 € autour de Lyon, particuliers seulement, les plus récentes d'abord. »
- « Exporte en CSV toutes les annonces de Clio 4 diesel de moins de 100 000 km en Gironde et donne-moi le prix médian. »
- « Voici une recherche que j'ai faite sur le site : https://www.leboncoin.fr/recherche?… Récupère les 5 premières pages et repère les meilleures affaires. »
- « Le vendeur de cette annonce est-il fiable ? https://www.leboncoin.fr/ad/… »

## Installation

Il faut Python 3.10 ou plus récent.

```bash
git clone https://github.com/BPiroga/lbc-mcp.git
cd lbc-mcp
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

(Sous macOS ou Linux, remplacer `.venv\Scripts\python` par `.venv/bin/python`.)

### Brancher le serveur sur Claude Desktop

Ouvrir `%APPDATA%\Claude\claude_desktop_config.json` (menu Paramètres → Développeur → Modifier la configuration) et ajouter le serveur, avec le chemin réel du dossier :

```json
{
  "mcpServers": {
    "leboncoin": {
      "command": "C:\\Users\\<vous>\\lbc-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "lbc_mcp"]
    }
  }
}
```

Puis redémarrer Claude Desktop.

### Brancher le serveur sur Claude Code

```bash
claude mcp add leboncoin -- C:\Users\<vous>\lbc-mcp\.venv\Scripts\python.exe -m lbc_mcp
```

### Sans cloner le dépôt, avec uv

```bash
uvx --from git+https://github.com/BPiroga/lbc-mcp lbc-mcp
```

## Réglages

Tous facultatifs, à passer en variables d'environnement (clé `"env"` dans la configuration de Claude Desktop).

| Variable | Défaut | Effet |
|---|---|---|
| `LBC_MIN_INTERVAL` | `2` | Secondes minimales entre deux requêtes à Leboncoin. |
| `LBC_PROXY` | aucun | Proxy à utiliser, forme `http://utilisateur:motdepasse@hote:port`. |
| `LBC_IMPERSONATE` | `chrome_android` | Navigateur dont lbc imite l'empreinte TLS (voir ci-dessous). |
| `LBC_EXPORT_DIR` | `Téléchargements\leboncoin` | Dossier des exports CSV/JSON. |

## Gros volumes et blocages

Leboncoin est protégé par Datadome, qui trie d'abord les clients d'après leur empreinte TLS. lbc en choisit une au hasard parmi quatre navigateurs ; lors des essais (septembre 2026), seule `chrome_android` passait (8 fois sur 8), les trois autres étant refusées dès la première requête. Utilisé tel quel, lbc échoue donc environ trois fois sur quatre. Le serveur fixe `chrome_android` par défaut ; si Datadome change de règles, `LBC_IMPERSONATE` permet d'en essayer une autre (`chrome`, `edge`, `safari`, `safari_ios`, `firefox`…).

Le débit toléré, lui, n'est pas connu. Par prudence, le serveur espace ses requêtes d'au moins deux secondes, et s'il est bloqué au milieu d'une récupération de plusieurs pages, il rend ce qu'il a déjà obtenu au lieu de tout perdre.

Pour de gros volumes :

- demander un export (`export_format`) plutôt que d'afficher les annonces dans la conversation : 100 annonces par page, jusqu'à la limite de pagination du site (environ 3 500 annonces par recherche) ;
- augmenter `LBC_MIN_INTERVAL` si les blocages se répètent ;
- passer par un proxy résidentiel situé en France (`LBC_PROXY`), comme le recommande lbc.

## Particularités

- **Villes.** lbc attend des coordonnées GPS. Le serveur les obtient auprès de l'API publique [geo.api.gouv.fr](https://geo.api.gouv.fr) : on peut donner un nom de commune, un code postal, ou `Saint-Denis (974)` pour lever une homonymie.
- **Départements.** Les listes de lbc couvrent la métropole hors Corse ; pour la Corse et l'outre-mer, il faut passer par les régions.
- **Export CSV.** Séparateur point-virgule, UTF-8 avec BOM et virgule décimale : le fichier s'ouvre directement dans Excel en français, accents et nombres compris.
- **Tri par prix.** Dans lbc 1.1.6, `Sort.CHEAPEST` et `Sort.EXPENSIVE` sont inversés. Le serveur choisit le tri d'après sa valeur réelle (`price`/`asc` ou `desc`), ce qui restera juste quand lbc sera corrigé.
- **URL de recherche.** Le serveur décode l'URL et remet ses paramètres dans un ordre que lbc accepte, avant de la lui passer. Le paramètre `page` de l'URL est respecté.

## Développement

```bash
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

Les tests n'interrogent pas Leboncoin. Pour un essai réel de bout en bout (quelques requêtes, espacées) : `.venv\Scripts\python scripts\smoke_test.py`.

## Avertissement

Projet personnel, sans lien avec Leboncoin. Il interroge une API non documentée qui peut changer à tout moment ; l'usage doit rester raisonnable et conforme aux conditions d'utilisation du site.

## Licence

MIT. lbc est également sous licence MIT.
