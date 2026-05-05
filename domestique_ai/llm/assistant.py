"""
Assistant IA DomestiqueAI basé sur Mistral Large.

Ce module est actuellement **isolé** : il n'est pas branché au dashboard Streamlit.
TODO (roadmap) : intégrer ce module au dashboard pour produire :
- un résumé de l'état de forme à partir de CTL/ATL/TSB,
- des réponses à des questions personnalisées sur les dernières activités,
- des plans d'entraînement adaptés à la charge récente.
"""

from __future__ import annotations

import requests

from domestique_ai.config import get_mistral_api_key

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-large-latest"


class MistralAssistant:
    """Wrapper minimal autour de l'endpoint chat completions de Mistral."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.api_key = api_key or get_mistral_api_key()
        if not self.api_key:
            raise ValueError(
                "Clé API Mistral manquante. Renseignez MISTRAL_API_KEY dans .env."
            )
        self.model = model

    def ask(self, prompt: str, context: str = "", temperature: float = 0.7) -> str:
        """Envoie un prompt utilisateur (avec contexte système optionnel) et retourne la réponse."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
        }
        response = requests.post(MISTRAL_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
