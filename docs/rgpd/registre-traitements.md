# Registre des traitements (RGPD art. 30)

> Document **interne** — ne pas publier. Version initiale pour la bêta privée.
> Responsable de traitement : Arnaud Stadler, 26 rue Charles Grad, 67600
> Sélestat — contact@domestique-ai.com.

| # | Traitement | Données | Personnes | Finalité | Base légale | Destinataires | Conservation |
| - | ---------- | ------- | --------- | -------- | ----------- | ------------- | ------------ |
| 1 | Comptes & authentification | email, pseudo, avatar, hash mdp, secret TOTP, sessions, consentements | utilisateurs | fournir l'accès, sécuriser | contrat + intérêt légitime (sécurité) | SMTP (emails), Cloudflare Email Routing (contact@) | vie du compte ; tokens à TTL |
| 2 | Données d'entraînement | activités, streams, notes, RPE, plans | athlètes | suivi de charge, planification | contrat | Ollama Cloud (si analyse IA demandée) | vie du compte |
| 3 | Données de santé (art. 9) | HRV, FC repos, sommeil, SpO2, respiration, température, stress, poids, readiness | athlètes | personnaliser l'entraînement, coach | **consentement explicite** (retirable) | Ollama Cloud (si demande), Sentry (sans PII) | vie du compte ; sources déconnectées au retrait |
| 4 | Conversations & mémoire coach | messages, résumés, faits | athlètes | mémoire du coach, personnalisation | contrat | Ollama Cloud (génération), stockage embeddings | vie du compte |
| 5 | Credentials Garmin / tokens Google | email/mdp Garmin, tokens OAuth | athlètes | synchroniser les données | consentement santé / contrat | Garmin, Google (destinataires des échanges) | jusqu'à déconnexion ou retrait |
| 6 | Retours testeurs | message, catégorie, page, UA, email snapshot | utilisateurs | améliorer le produit | intérêt légitime | email de notification | anonymisés à la suppression du compte |
| 7 | Supervision & sécurité | logs, erreurs (sans PII par défaut), user-agent de consentement | utilisateurs | sécurité, débogage | intérêt légitime | Sentry (PII désactivé) | 90 j (llm_calls), 365 j (audit), logs à rotation |
| 8 | Notifications push | identifiant Pushover, texte de notif | utilisateurs opt-in | informer (plan adapté, sync) | contrat/consentement | Pushover | vie du compte |

## Points d'attention

- Le mot de passe Garmin et le secret TOTP sont stockés **en clair** dans
  `platform.db` (fichier local non chiffré) — mesure de sécurité à renforcer
  (limitation connue, documentée).
- Aucune mesure d'audience, aucun cookie de suivi, aucune revente de données.
- Transferts hors UE possibles (Sentry, Ollama Cloud) — encadrés par les
  garanties des prestataires ; à réévaluer si un DPA est fourni.
- Un coach qui consulte les données d'un athlète agit sous la responsabilité de
  l'athlète : consentement de l'athlète requis (recueilli à l'acceptation de
  l'invitation).
