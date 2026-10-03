# SETUP_NOTES — Exposition publique via Cloudflare Tunnel

Ce document décrit l'exposition publique de l'app `domestique-ai` sur
**https://domestique-ai.com** via un Cloudflare Tunnel (locally-managed),
tout en conservant Tailscale pour l'accès administrateur (SSH / tailnet).

Mise en place : **2026-10-03**.

## Architecture retenue

```
Internet ──443──► Cloudflare edge (proxy, TLS) ──► Cloudflare Tunnel (sortant)
                                                        │
                              /home/ernesto/.cloudflared/a9e8401c-...json
                                                        ▼
                                          http://localhost:8501 (conteneur domestique-ai)
```

- **Aucun port ouvert sur la box / pas de NAT.** Le tunnel est une connexion
  **sortante** de `cloudflared` vers le edge Cloudflare (443). Rien à rediriger.
- **HTTPS géré par Cloudflare** (certificat edge) — pas de certificat à installer.
- **Tailscale reste en place** pour l'admin (SSH + tailnet), `tailscaled` non
  reconfiguré. Le *Funnel* a d'abord été coupé, puis **réactivé le 2026-10-03**
  pour exposer `open-webui` sur `https://ai-stack.tail68aa7e.ts.net` (cible
  `http://127.0.0.1:3000`). Les deux expositions coexistent (hostnames
  différents) :
  - `domestique-ai.com` → conteneur `domestique-ai` (8501) via **Cloudflare Tunnel**
  - `ai-stack.tail68aa7e.ts.net` → conteneur `open-webui` (3000) via **Tailscale Funnel** (`sudo tailscale funnel --bg 3000`)
- ⚠️ **Piège de port** : l'app `domestique-ai` écoute sur **8501** (host network),
  **pas 3000**. Le port `3000` de l'hôte est occupé par `open-webui`. Le tunnel
  cible donc `http://localhost:8501`.

## Identifiants

| Élément | Valeur / chemin |
| --- | --- |
| Tunnel name | `domestique-ai` |
| Tunnel UUID | `a9e8401c-0762-47b6-a23a-a50fbd823498` |
| Credentials file | `/home/ernesto/.cloudflared/a9e8401c-0762-47b6-a23a-a50fbd823498.json` (root:root 400) |
| Origin cert | `/home/ernesto/.cloudflared/cert.pem` (utilisé pour créer/ router les tunnels) |
| Config utilisateur | `/home/ernesto/.cloudflared/config.yml` |
| Config service (root) | `/etc/cloudflared/config.yml` |
| Unit systemd | `/etc/systemd/system/cloudflared.service` |
| Domaine | `domestique-ai.com` (apex, aucune sous-domaine) |
| DNS | CNAME apex `domestique-ai.com` → `a9e8401c-....cfargotunnel.com`, **proxifié** (nuage orange) |

### `/etc/cloudflared/config.yml` (copie utilisée par le service)

```yaml
tunnel: a9e8401c-0762-47b6-a23a-a50fbd823498
credentials-file: /home/ernesto/.cloudflared/a9e8401c-0762-47b6-a23a-a50fbd823498.json

ingress:
  - hostname: domestique-ai.com
    service: http://localhost:8501
  - service: http_status:404
```

## Installation (rappel)

`cloudflared` installé via le dépôt APT officiel Cloudflare. Le dépôt n'expose
pas encore la distribution `trixie` ; on utilise **`bookworm`** (binaire Go
statique, compatible Raspberry Pi OS trixie / arm64).

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared bookworm main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install -y cloudflared
cloudflared --version   # 2026.9.3
```

Service :

```bash
sudo cloudflared --config /home/ernesto/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
```

## Diagnostic

```bash
# État du service
systemctl status cloudflared
systemctl is-enabled cloudflared
journalctl -u cloudflared -f          # logs en direct

# État / infos du tunnel
cloudflared tunnel list
cloudflared tunnel info domestique-ai
cloudflared tunnel ingress validate   # vérifie le config.yml

# Test applicatif
curl -I https://domestique-ai.com
curl -s https://domestique-ai.com/api/health   # {"status":"ok"}

# Test côté hôte (avant Cloudflare)
curl -I http://localhost:8501/api/health

# Résolution DNS (côté client externe)
curl -s -H 'accept: application/dns-json' \
  'https://cloudflare-dns.com/dns-query?name=domestique-ai.com&type=A'
```

Redémarrer après modif de config :

```bash
sudo systemctl restart cloudflared
```

## Rollback

### 1) Tunnel Cloudflare HS → revenir au Funnel Tailscale (temporaire)

⚠️ Le Funnel sert actuellement **open-webui (3000)**. Pour basculer
`domestique-ai` sur le Funnel, il faut le repointer sur **8501** (ce qui coupe
l'exposition d'open-webui) :

```bash
# Repointer le Funnel vers l'app domestique-ai
sudo tailscale funnel --bg 8501

# Vérifier
tailscale funnel status
```

> `tailscale funnel --bg 8501` expose `https://ai-stack.tail68aa7e.ts.net`.
> L'accès tailnet direct (`http://ai-stack:8501`) reste disponible de toute façon.
> Pour revenir à open-webui ensuite : `sudo tailscale funnel --bg 3000`.
> Pour tout couper : `sudo tailscale funnel off`.

### 2) Arrêter / désinstaller le tunnel

```bash
# Stop + désactivation au boot
sudo systemctl disable --now cloudflared

# (optionnel) désinstaller le service
sudo cloudflared service uninstall

# (optionnel, définitif) supprimer le tunnel + son DNS
cloudflared tunnel route dns --overwrite-dns domestique-ai domestique-ai.com
cloudflared tunnel delete domestique-ai
```

### 3) Revenir sur l'ancienne base URL

Dans `/home/ernesto/domestique-ai/.env` :

```
DOMESTIQUE_AI_APP_BASE_URL=https://ai-stack.tail68aa7e.ts.net
GOOGLE_HEALTH_REDIRECT_URI=https://ai-stack.tail68aa7e.ts.net/api/google-health/callback
```

puis `docker compose up -d` dans `/home/ernesto/domestique-ai`.

## Points de vigilance / suivi

- **`GOOGLE_HEALTH_REDIRECT_URI`** (`.env`, ligne ~177) a été basculé sur
  `https://domestique-ai.com/api/google-health/callback` (2026-10-03).
  Côté Google Cloud Console, il faut que ce même URI soit présent dans les
  *Authorized redirect URIs* du client OAuth, sinon l'OAuth Google Health
  échoue (`redirect_uri_mismatch`).
- **Version Tailscale** : warning cosmétique `client 1.102.3 != tailscaled
  1.102.2` — sans impact. Ne pas reconfigurer `tailscaled` au-delà du funnel.
- **Reprise du conteneur** : `docker compose up -d` (base URL) recrée le
  conteneur ; rollback fichier `.env` possible, `restart: unless-stopped` déjà
  en place.
- **DNS apex** : avant création, aucun enregistrement A/AAAA n'existait sur
  l'apex ; les MX/TXT/sous-domaines n'ont pas été touchés.
- **Cache DNS négatif** : juste après la création du CNAME, le résolveur local
  du Pi peut renvoyer vide pendant la durée du SOA minimum (~30 min, 1800 s).
  C'est transitoire ; les clients externes résolvent immédiatement.
