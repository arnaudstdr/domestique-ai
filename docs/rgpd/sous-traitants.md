# Sous-traitants & destinataires

> Document **interne** — version initiale pour la bêta privée. À mettre à jour à
> chaque ajout de service. Politique publiée : `/confidentialite`.

| Service | Rôle | Données transmises | Localisation / garanties |
| ------- | ---- | ------------------ | ------------------------ |
| Hébergement auto-géré (Raspberry Pi) | hébergement | toutes | France, chez l'éditeur |
| Ollama Cloud | IA coach, embeddings | activités, tendances santé, conversations (à la demande) | hors UE — vérifier conditions/DPA |
| Garmin Connect | import activités/santé | credentials, échanges de données | API non officielle ; conditions Garmin |
| Google Health | import santé | tokens OAuth, échanges de données | UE/US — conditions Google |
| Sentry | supervision erreurs | pile d'erreur, route, sans PII par défaut (`SENTRY_SEND_PII=0`) | hors UE — DPA Sentry |
| Pushover | notifications push | identifiant utilisateur, texte de notification | US — conditions Pushover |
| Serveur SMTP | emails transactionnels | email, contenu transactionnel | selon fournisseur choisi |
| Cloudflare Email Routing | réception contact@ | emails entrants | UE/US — conditions Cloudflare |
| OpenStreetMap (tuiles) | carte des activités | IP + zone consultée (navigateur → OSM) | requête directe côté navigateur |

## À faire si la bêta s'élargit

- Vérifier que chaque prestataire accepte un **DPA** (art. 28) et l'archiver.
- Documenter les transferts hors UE (art. 44+) : clauses contractuelles types,
  décisions d'adéquation.
- Réévaluer l'hébergement si les volumes / la charge dépassent le Raspberry Pi.
