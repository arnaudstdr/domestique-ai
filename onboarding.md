# Bienvenue sur DomestiqueAI — guide de démarrage

Ce document te guide pas à pas, de la création de ton compte jusqu'à ton premier
plan d'entraînement. Il s'adresse aux **athlètes** et aux **coachs**.

- **Athlète** : suis les étapes 1 à 7. Les étapes **3 (profil)** et **4 (Garmin)**
  sont **indispensables** — sans elles, les calculs de charge restent vides et le
  plan est mal calibré.
- **Coach** : suis les étapes 1 et 2, puis la section [Parcours coach](#parcours-coach).

Compte environ **10 minutes** pour un athlète (hors synchronisation Garmin).

---

## Avant de commencer

| Ce qu'il te faut | Pour qui | Obligatoire ? |
| --- | --- | --- |
| Un lien d'invitation reçu par email ou par message **ou** l'inscription publique ouverte | Tous | Oui |
| Une application d'authentification (Google Authenticator, Authy, 1Password…) | Tous | Oui (2FA obligatoire) |
| Tes identifiants Garmin Connect | Athlète | Oui pour les activités |
| Un compte Google Health (optionnel) | Athlète | Non |

---

## Étape 1 — Créer ton compte

### A. Tu as reçu un lien d'invitation

1. Clique sur le lien d'invitation que tu as reçu (email ou message). Il peut
   être **à usage unique** (un lien personnel) ou **réutilisable** (le lien
   partagé par ton coach à tous ses athlètes).
2. L'écran « **Rejoindre un coach** » s'affiche. Reste sur l'onglet
   **« Créer un compte »**.
3. Renseigne :
   - **Nom d'affichage (optionnel)**
   - **Email**
   - **Mot de passe** (10 caractères minimum) puis confirmation
4. Clique sur **« Créer mon compte »**.
5. Tu es connecté et relié à ton coach ; passe directement à l'**étape 2**.

> **Tu as déjà un compte ?** Choisis l'onglet **« J'ai déjà un compte »**,
> connecte-toi (mot de passe + code 2FA), puis clique sur
> **« Se connecter et rejoindre »**. Ton historique est conservé.

> **« Cet email est déjà utilisé » ?** Un compte existe déjà avec cette adresse :
> utilise l'onglet « J'ai déjà un compte ».

### B. Tu t'inscris seul (inscription publique)

> Disponible uniquement si l'inscription publique est activée. Sinon, demande une
> invitation à ton coach.

1. Depuis l'écran de connexion, clique sur **« Créer un compte »**.
2. Choisis ton rôle : **Athlète** ou **Coach**.
3. Renseigne **Nom d'affichage (optionnel)**, **Email**, **Mot de passe**
   (10 caractères minimum) et confirmation.
4. Clique sur **« Créer mon compte »**.
5. **Coach** : tu arrives sur la page **Roster** (tes athlètes).
   **Athlète** : tu arrives sur le **Dashboard** (ton accueil).

---

## Étape 2 — Activer la double authentification (obligatoire)

La 2FA est **obligatoire** pour tous les comptes. Elle se configure juste après la
création du compte, et sera demandée à **chaque connexion** (code à 6 chiffres ou
code de secours).

1. **Scanne le QR code** affiché avec ton application d'authentification
   (Google Authenticator, Authy, 1Password…). Pas de scanner ? Utilise la
   **clé manuelle** affichée sous le QR code.
2. Saisis le **code de vérification** à 6 chiffres généré par l'application, puis
   clique sur **« Activer »**.
3. **L'écran « Codes de secours » s'affiche : c'est la seule et unique fois.**
   - Clique sur **« Copier »** ou **« Télécharger »** pour conserver les
     10 codes.
   - Coche **« J'ai noté mes codes de secours. »** puis **« Terminer »**.

> ⚠️ **Sans tes codes de secours**, la perte ou le changement de téléphone te
> bloquera hors de ton compte. Chaque code n'est utilisable qu'une fois. En cas de
> perte totale, contacte l'administrateur (réinitialisation de la 2FA).

> Tu recevras aussi un email de vérification d'adresse. Ce n'est **pas
> bloquant** : un bandeau te propose de renvoyer le lien si besoin.

---

## Étape 3 — Remplir « Infos perso » (athlète — indispensable)

**C'est l'étape la plus importante.** Sans ces valeurs, les calculs de charge
(hr-TSS, zones cardiaques, W/kg) sont vides ou faussés et le plan n'est pas
calibré.

1. Clique sur **ton avatar en haut à droite**, puis ouvre la page **Profil**.
2. Section **« Infos perso »**, renseigne les champs dans cet ordre :

| Champ | Unité | Exemple | Ce qu'il pilote |
| --- | --- | --- | --- |
| **FTP (W)** | watts | 250 | TSS puissance et rapport W/kg (sans lui, une valeur par défaut de 250 W est utilisée) |
| **Sexe** | M / F | M | Calcul de la charge cardiaque |
| **FC repos (bpm)** | bpm | 50 | hr-TSS et zones HR — **sans FC repos + FC max, aucun hr-TSS n'est calculable** |
| **FC max (bpm)** | bpm | 190 | hr-TSS et zones HR |
| **Poids (kg)** | kg | 70 | Rapport W/kg (et historique consultable sur la page Santé) |
| **% LTHR** | curseur 50–100 % | 88 % | Échelle du hr-TSS |
| **Niveau / expérience** | liste | Intermédiaire | Calibrage de la reprise et de l'intensité du plan |

3. Clique sur **« Enregistrer le profil »**. Le recalcul des charges est
   **automatique** (message « Recalcul de la charge en cours… ») : tu n'as rien
   d'autre à lancer.

> **Note — % LTHR.** Ce n'est pas un pourcentage de FC max mais la fraction de la
> **réserve cardiaque (HRR)** au seuil lactique. Il ne sert qu'à caler l'échelle
> du hr-TSS (1 h à ce %HRR = 100 points).
>
> - **Sans test : laisse 88 %** (valeur par défaut, raisonnable au départ).
> - **Avec un test** (30 min à fond, protocole Friel — la FC moyenne des
>   20 dernières minutes ≈ seuil) :
>   `% LTHR = (FC_seuil − FC_repos) / (FC_max − FC_repos)`.
>   Ex. repos 50, max 190, seuil 165 → `(165−50)/(190−50) ≈ 0.82`.
> - Valeurs typiques : débutant ~0.78–0.84, intermédiaire ~0.85–0.89,
>   avancé ~0.90–0.93.
> - Trop haut → hr-TSS sous-estimé ; trop bas → surestimé.

Le **poids** saisi ici est enregistré dans l'historique de la page **Santé**, où
tu peux aussi le mettre à jour au quotidien.

---

## Étape 4 — Connecter Garmin (athlète — indispensable pour les activités)

Toujours sur la page **Profil**, section **« Garmin Connect »**.

1. Saisis ton **email Garmin Connect** et ton **mot de passe**, puis clique sur
   **« Connecter Garmin »**.
2. Si Garmin demande un code, l'écran affiche un champ **« Code MFA »** : saisis
   le code reçu par email/SMS (valable ~5 minutes) puis **« Valider le code »**.
3. Clique sur **« Synchroniser maintenant »** : le premier import peut récupérer
   **jusqu'à 3 ans d'historique**.
4. Ensuite, la synchronisation est **automatique toutes les 30 minutes**.

| À savoir | Détail |
| --- | --- |
| Garmin ne remonte que les **activités** | Sommeil, HRV, poids, SpO2 viennent de **Google Health** ou de la saisie manuelle |
| Pas de push vers Garmin | L'export de ton plan se fait par téléchargement ZIP (.FIT) ou calendrier (.ICS) depuis la page Plan |
| « Connexion Garmin expirée ou rejetée » | Clique sur **« Reconnecter Garmin »** et refais la connexion |
| Pas de compte Garmin ? | Ajoute une activité manuellement ou importe un fichier d'activité (.tcx) depuis la page **Activités** |

---

## Étape 5 — Données de santé (athlète — recommandé)

Page **Santé** (icône cœur en bas).

- **Avec Google Health** : clique sur **« Connecter Google Health »**, autorise
  l'accès, puis **« Sync maintenant »** (7 jours). Tu récupères le sommeil, le
  HRV, la FC repos, le poids, les pas, la SpO2, etc. — qui alimentent le score
  de forme du jour (readiness).
- **Sans Google Health** : carte **« Saisie du jour »**, renseigne la **Date** et
  les valeurs utiles (**HRV**, **FC repos**, **Sommeil**, **Score sommeil**,
  **Stress**, **Poids**), puis **« Enregistrer »**. Tout est optionnel, mais plus
  tu remplis, meilleures sont les recommandations du matin.

---

## Étape 6 — Disponibilité hebdo (athlète — recommandé)

Toujours sur **Profil**, section **« Disponibilité hebdo »**. Elle sert à générer
un plan réaliste (sinon une grille par défaut Lun/Mer/Ven/Dim est utilisée).

1. Coche les jours où tu peux t'entraîner.
2. Pour chaque jour coché : **Durée max (min)** (minimum 20) et **Contexte**
   (Indoor / Outdoor).
3. Facultatif : choisis ton **Jour endurance longue** et ton **Jour intervalles**
   préférés.
4. Clique sur **« Enregistrer la disponibilité »**.

---

## Étape 7 — Objectif et premier plan (athlète — recommandé)

Page **Plan**.

1. Cartes **« Objectif courant »** → **« Modifier »**, puis renseigne :
   - **Type** : Cyclosportive, Course, Cyclo (loisir), Retour en forme / base ou
     Maintenance ;
   - **Date cible**, **Distance (km)**, **Dénivelé (m)** ;
   - **FTP cible (optionnel)** et **Notes**.
2. Clique sur **« Enregistrer l'objectif »**.
   > Sans objectif, le plan est généré en mode **« maintenance »** : volume
   > régulier, ni pic ni décharge.
3. Dans **« Générer un plan »** : choisis **« Périodisation classique »** ou
   **« Coach IA (bêta) »**, le nombre de **Séances / semaine** (4 par défaut) et
   un **Focus** éventuel, puis lance la génération.
4. Une fois le plan créé :
   - **« ZIP (.fit) »** pour l'envoyer vers Garmin/TrainingPeaks ;
   - **« Calendrier (.ics) »** ou **« Abonnement calendrier »** (Apple Calendrier,
     Google Calendar, QR code) pour le voir dans ton agenda ;
   - **« Adapter le plan »** pour la revue hebdomadaire (analyse la semaine
     écoulée et régénère la suite).

---

## Parcours coach

1. **Crée ton compte** (inscription publique ou invitation de l'administrateur)
   et **active la 2FA** (étape 2). Après une inscription avec le rôle Coach, tu
   arrives directement sur **Roster**.
2. Ouvre **Roster** (icône en haut de l'écran, « Roster — mes athlètes »).
3. Invite tes athlètes, au choix :
   - **« Mon lien d'invitation »** → **« Copier »** : lien **réutilisable**, à
     partager avec tous tes athlètes. **« Régénérer (révoque l'ancien) »**
     invalide l'ancien lien ;
   - **« Inviter un athlète »** → **« Générer un lien d'invitation »** : lien à
     **usage unique**, pour un athlète précis (tu peux le révoquer tant qu'il
     n'est pas utilisé).
4. Suis tes athlètes dans la section **« Athlètes »** :
   - **« Consulter »** : vue athlète en **lecture seule** (bandeau « Vue athlète ·
     … (lecture seule) »), avec accès à l'onglet **Prescrire** pour prescrire
     séances et plans. **« Quitter »** pour revenir à ta vue ;
   - **« Reconnexion »** : génère un lien valable **24 h** à transmettre à un
     athlète qui n'arrive plus à se connecter.
5. Conseille à chaque athlète de faire les **étapes 3 et 4** (profil + Garmin) :
   sans elles, tu ne verras ni charges, ni zones, ni plan pertinent.

**Message type à envoyer à un athlète :**

> Salut ! Je t'invite sur DomestiqueAI pour suivre ton entraînement.
> 1. Ouvre ce lien : `<lien d'invitation>`
> 2. Crée ton compte (mot de passe : 10 caractères minimum).
> 3. Active la 2FA et **conserve tes codes de secours**.
> 4. Va dans **Profil → Infos perso** : FTP, sexe, FC repos/max, poids,
>    % LTHR, niveau — puis **Enregistrer le profil**.
> 5. Dans **Profil → Garmin Connect**, connecte ton compte Garmin puis
>    synchronise.

---

## Se connecter les fois suivantes

1. Sur l'écran de connexion : **Email** + **Mot de passe** →
   **« Se connecter »**.
2. **« Vérification en 2 étapes »** : saisis le code à 6 chiffres affiché par ton
   application, ou clique sur **« Utiliser un code de secours »** et saisis l'un
   de tes codes enregistrés.

| À savoir | Détail |
| --- | --- |
| Session | 30 jours ; tu restes connecté sur ton appareil |
| Mot de passe oublié | **« Mot de passe oublié ? »** → lien par email (valable 30 min). Le changement déconnecte toutes tes sessions |
| Compte verrouillé | 5 échecs consécutifs → verrouillage 15 minutes |
| Nouvel appareil | La 2FA est demandée à chaque connexion |

---

## Récapitulatif

**Athlète (dans l'ordre) :**

- [ ] 1. Créer mon compte (invitation ou inscription)
- [ ] 2. Activer la 2FA et **noter les codes de secours**
- [ ] 3. **Profil → Infos perso** : FTP, sexe, FC repos, FC max, poids,
      % LTHR, niveau → **Enregistrer le profil**
- [ ] 4. **Profil → Garmin Connect** : connecter puis **Synchroniser maintenant**
- [ ] 5. Santé : Google Health ou saisie manuelle
- [ ] 6. Profil → Disponibilité hebdo
- [ ] 7. Plan : objectif puis génération du plan

**Coach :**

- [ ] 1. Créer mon compte
- [ ] 2. Activer la 2FA et **noter les codes de secours**
- [ ] 3. Roster : copier mon lien d'invitation ou générer un lien par athlète
- [ ] 4. Rappeler aux athlètes de remplir leur profil et de connecter Garmin

---

## Dépannage rapide

| Problème | Solution |
| --- | --- |
| « Invitation invalide, expirée ou déjà utilisée » | Demande un nouveau lien à ton coach (l'invitation est à usage unique) |
| « Cet email est déjà utilisé » | Utilise l'onglet **« J'ai déjà un compte »** du lien d'invitation |
| Je n'ai rien sur le tableau de bord | Vérifie **Profil → Garmin Connect** puis **« Synchroniser maintenant »** |
| Pas de hr-TSS ni de zones HR | Vérifie **FC repos** et **FC max** dans **Infos perso** |
| W/kg à « — » | Renseigne **FTP** et **Poids** |
| « Connexion Garmin expirée ou rejetée » | **« Reconnecter Garmin »** dans Profil |
| Plus d'accès à mon application 2FA | Utilise un **code de secours**, sinon contacte l'administrateur |
| Aucun email reçu (vérification, reset) | Regarde tes spams ; en environnement de test, l'envoi d'emails peut être désactivé |
| La page Plan affiche un message parlant d'un fichier à renseigner | Ignore ce message : clique sur **« Modifier »** dans **« Objectif courant »** |

---

*Le coach IA est une IA : il peut faire des erreurs. Ses conseils ne remplacent
pas l'avis d'un médecin ou d'un professionnel de santé.*
