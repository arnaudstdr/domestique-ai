"""
Assistant IA DomestiqueAI utilisant Mistral Large via API.
- Résume l'état de forme
- Répond à des questions personnalisées
- Propose des plans d'entraînement
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

class MistralAssistant:
    """
    Assistant IA basé sur Mistral Large pour le coaching cycliste.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or MISTRAL_API_KEY
        if not self.api_key:
            raise ValueError("Clé API Mistral manquante. Ajoutez MISTRAL_API_KEY dans le .env.")

    def ask(self, prompt: str, context: str = "") -> str:
        """
        Envoie une requête à l'API Mistral Large avec un prompt et un contexte facultatif.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "mistral-large-latest",
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7
        }
        response = requests.post(MISTRAL_API_URL, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
