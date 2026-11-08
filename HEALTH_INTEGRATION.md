# Intégration santé multi-sources — Apple Santé & Android Health Connect

> Document de cadrage (recherche, non implémenté). Objet : évaluer comment
> récupérer les données de santé des utilisateurs iPhone/Android alors que
> l'intégration actuelle repose sur un pull cloud Google Health API v4, non
> transposable à Apple.
>
> Dernière mise à jour : 2026-09-29.

## 1. Constat

### 1.1 Ce qui existe aujourd'hui

La page UI **« Santé »** (`/sante`, `frontend/src/pages/Morning.tsx`) synchronise
les métriques de récupération via une **API cloud Google**, pas depuis le
téléphone :

- Client OAuth2 + REST dans `domestique_ai/ingestion/google_health.py`
  (base `https://health.googleapis.com/v4`, `google_health.py:34` ; scopes
  `googlehealth.*.readonly`, `google_health.py:38-47`).
- Endpoints HTTP dans `domestique_ai/api/routers/google_health.py`
  (`/status` `:49`, `/auth` `:72`, `/callback` `:87`, `/sync` `:132`,
  `/disconnect` `:169`).
- Auto-sync planifié dans `domestique_ai/api/scheduler.py`
  (`_google_health_auto_sync_job` `:90-122`, enregistrement `:441-456`),
  intervalle dans `domestique_ai/config.py:434-466`.
- Tokens **par athlète** stockés côté serveur
  (`config.py:594-603`, `data/athletes/<public_id>/.google_health_tokens.json`).

C'est un **pull serveur** : le navigateur ne fait que le consentement OAuth, le
serveur échange/rafraîchit le token et va chercher les données.

### 1.2 Pourquoi ça ne se transpose pas à Apple

**Apple Santé (HealthKit) n'a aucune API serveur.** Les données vivent dans un
store **chiffré, on-device**, accessible uniquement par une **app iOS native**
qui lit HealthKit localement, avec le consentement de l'utilisateur par type de
donnée. Aucun token, aucun endpoint que le serveur pourrait appeler.

Conséquence : pour Apple, le flux s'inverse. Il faut une app sur le téléphone
qui **pousse** vers le backend, au lieu d'un serveur qui **pull**.

### 1.3 Android a le même problème

L'équivalent Android de HealthKit est **Health Connect** : lui aussi
**on-device**, sans API cloud. Or l'intégration actuelle (Google Health API v4)
est un pull cloud qui **ne couvre que les données Fitbit / Pixel Watch** —
c'est-à-dire une minorité d'utilisateurs Android. La majorité (Samsung Health,
Google Fit, montres tierces) publie dans Health Connect, pas dans le cloud
Google/Fitbit.

> Note : l'API Google Fit (y compris REST) est **supportée jusqu'à fin 2026**
> puis dépréciée ; Google pousse vers Health Connect (mobile) ou Google Health
> API (cloud). Détail en §6.

**Donc : une app mobile companion résout les deux OS d'un coup** (HealthKit +
Health Connect). C'est le principal enseignement de ce document.

### 1.4 Le problème n'est pas que technique

Ouvrir l'app à d'autres utilisateurs transforme un usage « perso/self-hosted »
en **produit multi-tenant distribué** : il faut un compte developer Apple, une
distribution (App Store ou TestFlight), une politique de confidentialité, et le
respect de règles Apple spécifiques aux données de santé (§2.3).

## 2. Prérequis Apple

### 2.1 Apple Developer Program

| Point | Détail |
| --- | --- |
| Obligatoire ? | **Oui** pour l'entitlement HealthKit — sans lui, les appels HealthKit échouent silencieusement à l'exécution. Requis aussi pour distribuer. |
| Coût | **99 $/an (~99 €)**. |
| Prérequis compte | Apple Account avec **2FA** activée, âge de la majorité. |
| Compte gratuit (Apple ID sans programme) | Sideload sur *son propre* appareil seulement, expiration **7 jours**, **pas de background delivery HealthKit**, pas de distribution. Inutilisable pour un produit. |
| Distribution | App Store, TestFlight ou Ad Hoc : **tous** exigent le programme payant. |

### 2.2 Individuel vs organisation — et le cas « auto-entrepreneur »

Apple ne demande **ni SIRET ni numéro fiscal**. En revanche :

| | Enrôlement **individuel** | Enrôlement **organisation** |
| --- | --- | --- |
| Identifiant demandé | Nom légal personnel | **Numéro D-U-N-S** (Dun & Bradstreet, gratuit) |
| Statut requis | Aucun | Être une **entité juridique** (société) |
| Anti-requis | — | Apple **refuse** DBA, noms commerciaux, succursales, **et les sole proprietorships** |
| Vendeur affiché (App Store) | **Ton nom légal** | Nom de l'entité juridique |
| Cas auto-entrepreneur / EI | **Obligatoire** — c'est ton cas | Refusé |

> « SIRET » ≠ « D-U-N-S ». Même si tu as un SIRET, un auto-entrepreneur est
> classé *sole proprietorship* par Apple : il **doit** s'inscrire en
> **individuel**, et le vendeur affiché sera **son nom légal** (pas de nom de
> marque visible sur la fiche App Store).
>
> Pour apparaître comme « société » : créer une structure (SASU/SARL/EURL) et
> demander un **D-U-N-S** associé à cette entité. Conversion
> individuel → organisation possible ensuite, via support Apple.

### 2.3 Contraintes App Review / privacy

- **Guideline 5.1.3 / 2.5.18** : les données HealthKit **ne peuvent pas** servir
  à la publicité, au marketing ou au data mining tiers. Usage « health &
  fitness » uniquement.
- **Politique de confidentialité obligatoire**, dans App Store Connect **et**
  dans l'app.
- **Privacy Nutrition Label** : déclarer « Health & Fitness » et tout partage
  avec des tiers.
- **Usage strings** (`Privacy - Health Share Usage Description`, et
  `Health Update` si écriture) : obligatoires, affichés dans la demande de
  permission.
- Autorisation **fine par type de donnée** ; l'app **ne peut pas savoir** si un
  type a été refusé (un refus et une absence de donnée sont indiscernables).
  L'UI doit gérer ce flou.
- Beaucoup de types (HR continu, sommeil stadié, SpO2) **exigent une Apple
  Watch**. Un utilisateur iPhone seul n'aura pas tout → dégradation gracieuse
  nécessaire.

## 3. Options d'intégration

Du plus léger au plus engageant. Aucune n'est « la bonne » partout : ça dépend
du budget, du délai et de la tolérance à dépendre d'un tiers.

### Option A — Health Auto Export (validation rapide, 0 compte dev)

L'utilisateur installe une app iOS tierce (**Health Auto Export**, payante) et
configure une *automation* **REST API** : l'app POST du JSON vers une URL. Tu ne
développes **qu'un endpoint d'ingestion** côté backend.

- **Pour** : pas de compte Apple, pas d'app Store, quelques jours de dev. Parfait
  pour valider l'appétit avant d'investir.
- **Contre** : iOS uniquement ; l'utilisateur doit installer/configurer une app
  tierce payante ; UX tout sauf grand public ; dépendance à un tiers (format,
  quotas) ; données de santé historiques envoyées en masse.
- **Verdict** : outil de **validation**, pas une solution produit.

### Option B — App companion maison (recommandé long terme)

Envelopper l'app mobile autour de l'existant (React Native / Expo, ou Capacitor
avec du code natif), lire **HealthKit (iOS)** et **Health Connect (Android)**,
et pousser vers un nouvel endpoint d'ingestion authentifié.

- **Pour** : couvre les **deux OS** ; contrôle total du flux et du format ;
  cohérent avec l'identité/marque ; pas de tiers dans le chemin de données.
- **Contre** : développement natif à maintenir (permissions, background
  delivery, uploads robustes) ; Apple Developer Program + Google Play ;
  distribution à gérer.
- **Verdict** : seule voie **pérenne et multi-plateforme**. À privilégier dès
  que le produit vise plusieurs utilisateurs.

### Option C — Agrégateur (Terra, Vital, Rook, Spike, Open Wearables…)

Un prestataire fournit le SDK on-device + une **API unifiée** branchée sur
HealthKit, Health Connect, Garmin, Whoop, etc.

- **Pour** : mise sur le marché **la plus rapide**, un seul modèle de données
  pour toutes les sources, pas de code natif de santé à écrire.
- **Contre** : **coût par utilisateur** ; les données de santé **transitent par
  un tiers** (à déclarer dans la privacy label ; vigilance avec les règles
  Apple de partage HealthKit) ; dépendance forte au fournisseur.
- **Verdict** : bon compromis vitesse/effort si le coût récurrent et le tiers
  sont acceptables.

### Récapitulatif

| Critère | A — Health Auto Export | B — App maison | C — Agrégateur |
| --- | --- | --- | --- |
| iOS | oui (via app tierce) | oui | oui |
| Android | non | oui | oui |
| Compte Apple dev | non | oui | oui (le SDK l'exige) |
| Effort initial | très faible | élevé | moyen |
| Coût récurrent | tierce partie app payante | hébergement | **par utilisateur** |
| UX grand public | non | oui | oui |
| Tiers dans le flux de données | oui | non | oui |

## 4. Impact technique (indépendant de l'option)

### 4.1 Le stockage de santé n'est pas source-aware

`morning_metrics` (DDL + migrations : `domestique_ai/ingestion/db.py:214-255`)
est **clé par `date`** et **n'a ni colonne `source` ni `source_uid`**. La source
est donc implicite, et l'écriture
(`save_morning_entry()`, `domestique_ai/processing/morning_metrics.py:63`) fait
un `ON CONFLICT(date) DO UPDATE` avec une préservation manuelle via les flags
`*_computed` (voir `sync_google_health_morning_metrics()`,
`google_health.py:964-1114`).

À l'inverse, les **activités** sont déjà source-agnostiques : table `activities`
avec `source` (`garmin|strava|manual|tcx`) + `source_uid`, et helper générique
`insert_activity()` (`ingestion/db.py`).

**Conséquence** : il n'existe aujourd'hui **aucun point d'accroche** pour une
seconde source de santé. Il faut :

1. ajouter `source` (et `source_uid` par échantillon) à `morning_metrics`, avec
   migration douce (`CREATE TABLE` **et** `_ensure_column()`) ;
2. définir des **règles de précédence / merge** par date et par métrique
   (aujourd'hui « pull cloud écrase sauf saisie manuelle ») ;
3. introduire une couche d'abstraction type `HealthSource` au lieu de la
   fonction Google-spécifique.

### 4.2 Un endpoint d'ingestion « push » à créer

Le scheduler auto-sync (`api/scheduler.py:90-122`) et les tokens par athlète
(`config.py:594-603`) sont **propres au pull cloud** : ils ne s'appliquent pas à
Apple/Health Connect.

Il faut un **endpoint d'ingestion** :

- **authentifié** et **scopé à l'utilisateur** (réutiliser l'infra multi-tenant
  plateforme) ;
- **idempotent** : dédup par **UUID d'échantillon** (re-jouer un batch ne doit
  pas dupliquer) ;
- tolérant aux envois partiels/retry (le téléphone peut être coupé en plein
  upload) ;
- alimentant `save_morning_entry()` après normalisation.

C'est un modèle **inverse** de l'existant : « push device → serveur » au lieu de
« serveur → pull cloud ».

## 5. Décision à trancher

Critères :

| Critère | Question |
| --- | --- |
| Périmètre OS | iOS seul au début, ou iOS **et** Android (donc app mobile) ? |
| Délai vs qualité | Valider vite (A/C) ou construire pérenne (B) ? |
| Budget récurrent | Coût par utilisateur d'un agrégateur acceptable ? |
| Tiers dans le flux | Acceptable que des données de santé transitent par un tiers (RGPD + privacy label) ? |
| Structure | Rester en individuel (nom légal comme vendeur) ou créer une société + D-U-N-S ? |

Recommandation de travail : **option A pour valider**, cible **option B** pour
le produit.

## 6. Sources

- Apple — Inscription au programme développeur :
  <https://developer.apple.com/help/account/membership/program-enrollment>
- Apple — Numéro D-U-N-S :
  <https://developer.apple.com/help/account/membership/D-U-N-S>
- Apple — Vérification d'identité :
  <https://developer.apple.com/help/account/membership/identity-verification>
- Apple — Configuration de l'accès HealthKit :
  <https://developer.apple.com/documentation/xcode/configuring-healthkit-access>
- Apple — App Review Guidelines (5.1.3 Health & Health Research, 2.5.18) :
  <https://developer.apple.com/app-store/review/guidelines/>
- Apple — App Privacy Details (privacy nutrition label) :
  <https://developer.apple.com/app-store/app-privacy-details/>
- Google — Migration depuis les API Fit (Fin 2026) :
  <https://developer.android.com/health-and-fitness/health-connect/migration/fit>
- Google — Health Connect (comparaison) :
  <https://developer.android.com/health-and-fitness/health-connect/comparison-guide>
- Health Auto Export — automation REST API :
  <https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/>
