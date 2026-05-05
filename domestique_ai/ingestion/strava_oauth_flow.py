"""
Flow OAuth2 Strava interactif.

Usage : `python -m domestique_ai.ingestion.strava_oauth_flow`

Étapes :
1. Lit STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REDIRECT_URI depuis .env.
2. Affiche l'URL d'autorisation à ouvrir dans le navigateur.
3. Demande le `code` retourné dans l'URL de redirection.
4. Échange le code contre access_token + refresh_token et persiste data/.strava_tokens.json.
5. Synchronise les activités existantes.
"""

from __future__ import annotations

import sys

from domestique_ai.config import get_strava_credentials, get_tokens_path
from domestique_ai.ingestion.strava import StravaClient, init_db, sync_activities


def main() -> int:
    client_id, client_secret, redirect_uri = get_strava_credentials()
    if not (client_id and client_secret):
        print(
            "ERREUR : STRAVA_CLIENT_ID et STRAVA_CLIENT_SECRET doivent être renseignés dans .env.\n"
            "Copiez .env.example vers .env et remplissez les valeurs depuis "
            "https://www.strava.com/settings/api",
            file=sys.stderr,
        )
        return 1

    auth_url = StravaClient.get_authorization_url(client_id, redirect_uri)
    print("\n=== Authentification Strava ===")
    print(f"Ouvrez cette URL dans votre navigateur et autorisez l'accès :\n{auth_url}\n")
    print(
        "Après autorisation, vous serez redirigé vers une URL contenant `?code=XXX&...`. "
        "Copiez la valeur de `code`."
    )
    code = input("\nCollez le code d'autorisation : ").strip()

    token_data = StravaClient.exchange_code_for_token(client_id, client_secret, code, redirect_uri)

    client = StravaClient(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=token_data.get("expires_at"),
        client_id=client_id,
        client_secret=client_secret,
    )
    client.save_tokens()
    print(f"Tokens persistés dans {get_tokens_path()}")

    init_db()
    inserted = sync_activities(client)
    print(f"{inserted} nouvelle(s) activité(s) sauvegardée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
