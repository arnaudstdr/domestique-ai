# Procédure en cas de violation de données (RGPD art. 33-34)

> Document **interne**. À appliquer en cas d'accès non autorisé, perte,
> altération ou divulgation de données personnelles.

## 1. Détection et confinement

1. Identifier la source : alerte Sentry, logs (`X-Request-ID`), signalement
   utilisateur, anomalie d'accès.
2. Confiner sans détruire les preuves : révoquer les sessions concernées
   (`revoke_all_sessions`, panneau admin), couper l'exposition (stop conteneur,
   fermeture d'accès réseau), invalider les tokens/feed/secrets si nécessaire.
3. Préserver les journaux (`admin_audit`, logs applicatifs) pour l'analyse.

## 2. Qualification (dans les 24 h)

- Nature de la violation, catégories et volume de données concernées,
  personnes concernées (comptes identifiés via `public_id`/email).
- Risque pour les personnes : les données de santé sont sensibles — un risque
  est présumé dès qu'elles sont concernées.

## 3. Notification CNIL (sous 72 h)

- Si un risque est probable : notifier la CNIL via
  https://notifications.cnil.fr (téléservice).
- Contenu : nature, catégories/volume, conséquences probables, mesures prises,
  coordonnées du contact (contact@domestique-ai.com).

## 4. Information des personnes concernées

- Si un risque élevé : informer individuellement (email) — description claire,
  conséquences, mesures, recommandations (changer de mot de passe, révoquer
  2FA, etc.).

## 5. Registre et retour d'expérience

- Consigner l'incident dans un registre interne : dates, faits, décisions,
  notifications, remédiation.
- Mettre à jour le [registre des traitements](registre-traitements.md) et les
  mesures techniques si nécessaire.
