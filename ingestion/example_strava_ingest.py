"""
Exemple d'utilisation du module d'ingestion Strava :
- Authentification OAuth2
- Récupération des activités
- Sauvegarde dans SQLite
"""
from strava import StravaClient, init_db, save_activity
import os

# Paramètres à renseigner (remplacer par vos propres valeurs)
CLIENT_ID = "161274"
CLIENT_SECRET = "fbfdcf22e8eaadf5661c651993f2653a4210d6bc"
REDIRECT_URI = "http://localhost/exchange_token"

if __name__ == "__main__":
    # 1. Initialiser la base de données
    init_db()

    # 2. Générer l'URL d'autorisation et demander à l'utilisateur de l'ouvrir
    print("\n=== Authentification Strava ===")
    client = StravaClient(access_token="")
    auth_url = client.get_authorization_url(CLIENT_ID, REDIRECT_URI)
    print(f"Veuillez ouvrir cette URL dans votre navigateur et autoriser l'accès :\n{auth_url}")
    code = input("\nCollez ici le code d'autorisation obtenu : ")

    # 3. Échanger le code contre un access_token
    token_data = StravaClient.exchange_code_for_token(CLIENT_ID, CLIENT_SECRET, code, REDIRECT_URI)
    access_token = token_data["access_token"]
    print("Token d'accès obtenu !")

    # 4. Récupérer et sauvegarder les activités
    client = StravaClient(access_token)
    activities = client.fetch_activities()
    print(f"{len(activities)} activités récupérées.")
    for act in activities:
        data = client.extract_activity_data(act)
        save_activity(data)
    print("Toutes les activités ont été sauvegardées dans la base SQLite.")
