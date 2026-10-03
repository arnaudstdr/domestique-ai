# Politique de confidentialité

**Version 2026-10-beta — en vigueur à compter du 3 octobre 2026.**

La présente politique décrit comment DomestiqueAI traite les données
personnelles de ses utilisateurs, conformément au Règlement (UE) 2016/679
(« RGPD ») et à la loi française n° 78-17 du 6 janvier 1978 modifiée.

## 1. Responsable du traitement

- **Responsable** : Arnaud Stadler, 26 rue Charles Grad, 67600 Sélestat, France.
- **Contact pour toute question ou demande relative aux données** :
  contact@domestique-ai.com.

Le Service étant édité à titre personnel et non professionnel, aucun délégué à
la protection des données (DPO) n'a été désigné (non requis) ; le contact
ci-dessus exerce cette fonction.

## 2. Données traitées

### a. Données de compte et d'identification

- adresse e-mail, nom d'affichage, photo de profil (facultative) ;
- mot de passe stocké **haché** (argon2id) — jamais en clair ;
- secret d'authentification en deux étapes (TOTP) et codes de secours hachés ;
- sessions (jetons stockés hachés), date de création du compte, préférences
  (thème, unités), consentements enregistrés (date, version, agent
  utilisateur) ;
- si tu connectes Garmin : identifiants du compte Garmin nécessaires à la
  synchronisation, stockés dans la base locale du Service.

### b. Données d'entraînement

- activités importées (date, type de sport, durée, distance, dénivelé,
  puissance, fréquence cardiaque, cadence, vitesse, calories, tracé GPS,
  températures, notes et ressenti que tu saisis) ;
- charge d'entraînement calculée, plans, séances prescrites et décisions
  d'adaptation.

### c. Données de santé (catégorie particulière — article 9 du RGPD)

- variabilité cardiaque (HRV), fréquence cardiaque de repos, durée et qualité
  du sommeil (dont stades), saturation en oxygène (SpO2), fréquence
  respiratoire, température cutanée, score de stress, body battery, readiness,
  poids, ainsi que les notes libres que tu saisis.

### d. Conversations et mémoire du coach

- messages échangés avec le coach, résumés de session et faits mémorisés
  (préférences, contraintes) utilisés pour personnaliser les réponses.

### e. Retours testeurs

- catégorie et contenu de ton retour, page et version de l'application, agent
  utilisateur.

### f. Données techniques

- journaux d'erreurs et de performance à des fins de sécurité et de
  supervision ; métadonnées d'appels à l'IA (modèle, tokens, durées) pour le
  suivi des coûts et de la qualité.

Le Service **ne réalise aucune mesure d'audience publicitaire**, n'utilise
aucun cookie de suivi et ne revend aucune donnée.

## 3. Finalités et bases légales

| Finalité | Base légale |
| --- | --- |
| Fournir le Service : compte, stockage, calculs, affichage, export | Exécution du contrat (CGU) |
| Traiter les **données de santé** et personnaliser l'entraînement | **Consentement explicite** (art. 9-2-a RGPD), retirable à tout moment |
| Sécurité : authentification, 2FA, lutte contre les abus, journal d'audit | Intérêt légitime |
| Notifications (plan adapté, sync terminée) | Exécution du contrat / consentement selon le canal |
| Amélioration du Service à partir des retours testeurs | Intérêt légitime (retours anonymisés à la suppression du compte) |
| Répondre aux obligations légales et exercer nos droits | Obligation légale / intérêt légitime |

Le consentement au traitement des données de santé est recueilli
séparément, de manière explicite, lors de la création du compte. Tu peux le
**retirer à tout moment** depuis la page Profil : les sources automatiques
(Garmin, Google Health) sont alors déconnectées. Le retrait ne remet pas en
cause la licéité des traitements effectués avant le retrait ; il n'empêche pas
la consultation des données déjà collectées ni leur suppression (voir §7).

## 4. Destinataires et sous-traitants

Tes données ne sont transmises qu'aux destinataires suivants, strictement pour
les besoins du Service :

- **Hébergement** : l'Éditeur lui-même, sur une infrastructure privée située en
  France (aucun hébergeur tiers) ;
- **Intelligence artificielle** : fournisseur de modèles utilisé par le coach,
  qui reçoit les données nécessaires à la réponse (activités, tendances de
  récupération, conversations) **lorsque tu sollicites le coach ou lors des
  analyses automatiques de plan**. Aucun entraînement de modèles sur tes
  données n'est demandé ;
- **Garmin** (si tu connectes ton compte) : pour importer tes activités et
  métriques ;
- **Google Health** (si tu connectes ton compte) : pour importer tes métriques
  santé ;
- **Supervision des erreurs** (Sentry) : remontée d'incidents techniques, sans
  adresse IP ni en-têtes d'authentification (identifiants personnels désactivés
  dans la configuration) ;
- **Notification push** (Pushover, si tu l'actives) : envoi de notifications ;
- **Messagerie** (serveur SMTP) : envoi des e-mails transactionnels
  (vérification d'adresse, réinitialisation de mot de passe, retours testeurs) ;
- **Routage e-mail** (Cloudflare Email Routing) : réception des messages
  adressés à contact@domestique-ai.com ;
- **Fonds de carte** (OpenStreetMap) : lorsque tu affiches la carte d'une
  activité, ton navigateur contacte directement les serveurs de tuiles
  d'OpenStreetMap, qui reçoivent ton adresse IP et la zone consultée.

Tes données ne sont jamais vendues, louées ni cédées à des fins publicitaires.

## 5. Transferts hors Union européenne

Certains prestataires (notamment le fournisseur de modèles d'IA et la
supervision des erreurs) peuvent être établis en dehors de l'Union européenne
ou y traiter des données. Dans ce cas, les transferts s'appuient sur les
garanties prévues par le RGPD (clauses contractuelles types de la Commission
européenne ou décision d'adéquation), et se limitent aux données nécessaires
aux finalités décrites.

## 6. Durées de conservation

- **Compte, activités, données de santé, conversations** : conservés tant que
  le compte existe. La suppression du compte efface la base de données de
  l'espace athlète (activités, santé, conversations, mémoire), les jetons
  d'accès aux sources et les fichiers de profil.
- **Sessions et jetons** : durée de validité puis suppression (30 jours
  maximum pour une session, liens à usage unique à durée courte).
- **Métadonnées d'appels IA** : 90 jours (suivi des coûts et de la qualité).
- **Journal d'audit administratif et de sécurité** : 12 mois.
- **Retours testeurs** : conservés pour l'amélioration du Service ; le contenu
  est **anonymisé** lors de la suppression du compte.
- **Sauvegardes** : les sauvegardes techniques peuvent contenir des données
  pendant une durée limitée avant écrasement automatique.

## 7. Tes droits

Conformément aux articles 15 à 22 du RGPD, tu disposes des droits suivants :

- **accès** : consulter les données traitées ;
- **rectification** : corriger des données inexactes ;
- **effacement** : supprimer ton compte et les données associées, directement
  depuis la page Profil ou sur demande ;
- **portabilité** : télécharger l'ensemble de tes données depuis la page
  Profil (archive ZIP : identité, profil, activités, métriques de santé,
  conversations) ;
- **limitation** et **opposition** aux traitements ;
- **retrait du consentement** santé à tout moment (page Profil) ;
- **réclamation** auprès de la CNIL (https://www.cnil.fr) si tu estimes que
  tes droits ne sont pas respectés.

Pour exercer un droit ou poser une question : **contact@domestique-ai.com**.
Une réponse est apportée dans un délai d'un mois.

## 8. Mineurs

Le Service est accessible à partir de **15 ans révolus**. Pour les
utilisateur·rices mineur·es, une autorisation du représentant légal est exigée
lors de la création du compte, et le représentant légal peut exercer
l'ensemble des droits décrits au §7 en contactant l'adresse ci-dessus.

## 9. Cookies et stockage local

Le Service **ne dépose aucun cookie** (ni publicitaire, ni de mesure
d'audience). Il utilise uniquement le stockage local de ton navigateur
(`localStorage`) pour des éléments strictement nécessaires :

- jeton de session (authentification) ;
- préférences d'affichage (thème clair/sombre, athlète consulté par un coach) ;
- cache applicatif du service worker (certaines données de charge et
  d'activités sont mises en cache localement pour un affichage hors-ligne).

Ces éléments sont nécessaires au fonctionnement du Service et ne sont jamais
transmis à des tiers.

## 10. Sécurité

Des mesures techniques sont mises en œuvre : hachage des mots de passe
(argon2id), hachage des jetons de session, authentification en deux étapes
obligatoire, chiffrement des échanges (HTTPS/HSTS via le reverse proxy),
limitation du taux de requêtes, journalisation des actions sensibles,
désactivation du suivi des identifiants personnels dans la supervision.

Le Service étant auto-hébergé sur une infrastructure privée, aucune
certification de sécurité tierce n'est revendiquée ; l'Éditeur s'efforce de
maintenir un niveau de sécurité proportionné à la nature des données traitées
et invite à signaler toute vulnérabilité à l'adresse de contact.

## 11. Modification de la politique

La politique peut être mise à jour pour refléter l'évolution du Service ou de
la réglementation. La version en vigueur et sa date sont affichées en tête de
cette page. En cas de modification substantielle, une nouvelle acceptation est
demandée lors de la connexion.
