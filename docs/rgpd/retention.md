# Durées de conservation

> Document **interne**. Politique publiée : section 6 de `/confidentialite`.

| Donnée | Durée | Mécanisme |
| ------ | ----- | --------- |
| Compte (email, pseudo, avatar, hash mdp, TOTP) | vie du compte | suppression compte (`DELETE /api/auth/me`) ou admin/coach |
| Activités, données de santé, conversations, mémoire, plans | vie du compte | effacement du dossier athlète à la suppression |
| Sessions | 30 jours max / révocation | `revoke_session`, `revoke_all_sessions` |
| Tokens (vérif email, reset, reconnexion, invitations) | TTL court + usage unique | `auth_tokens`, `reconnect_tokens`, `invitations` |
| Retours testeurs | anonymisés à la suppression du compte | `delete_user` (email/public_id/UA → NULL) |
| `llm_calls` (métadonnées IA) | **90 jours** (`DOMESTIQUE_AI_LLM_CALLS_RETENTION_DAYS`, 0 = illimité) | job `retention_purge` (24 h) |
| `admin_audit` (journal sécurité) | **365 jours** (`DOMESTIQUE_AI_AUDIT_RETENTION_DAYS`, 0 = illimité) | job `retention_purge` (24 h) |
| Logs applicatifs | rotation système | hors applicatif |
| Sauvegardes `data/` | écrasement à la prochaine sauvegarde | manuel (`tar`) |

## Vérifications

- La purge est un job APScheduler `retention_purge` (voir `api/scheduler.py`),
  lancé toutes les 24 h, best-effort (ne lève jamais).
- Les exports RGPD (`account_export`) et les suppressions de comptes sont
  tracés dans `admin_audit`.
