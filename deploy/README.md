# Production deployment to a public IPv4 (Ubuntu 24.04)

The production stand does not require a domain. Subjects open `https://PUBLIC_IP/` directly. Caddy terminates TLS on ports 80/443 and requests a short-lived, publicly trusted IP-address certificate from Let's Encrypt using the ACME `shortlived` profile. The frontend continues to proxy `/api` to the internal backend.

Because TLS clients normally omit SNI for a literal IP address, the Caddy global `default_sni` is set from `APP_PUBLIC_IP`; without it Caddy can obtain the IP certificate successfully but still fail the client handshake with `tlsv1 alert internal error`.

`deploy/remote-deploy.sh` materializes `https://APP_PUBLIC_IP` into that release's `backend/config.json`; the committed development config remains `http://localhost:3033`.

## Deployment model

GitHub Actions connects directly as `root`. There is no deployment user and no bootstrap script to run manually. The workflow installs Docker Engine/Compose on Ubuntu when they are missing, creates `/opt/article-interaction-automation`, materializes the runtime environment and deploys the release.

Docker Compose always uses the fixed project name `article-interaction-automation`, so the SQLite experiment volume survives ordinary releases. Caddy certificate/config data also uses named volumes so the short-lived IP certificate can be renewed automatically.

`INVITATION_SIGNING_KEY` is generated on the server on the first deployment and retained in `/etc/article-interaction-automation.env` across later deployments. `ROUTERAI_API_KEY` is refreshed from the GitHub secret on every deployment. The runtime environment file is mode `0600` and owned by root.

## GitHub configuration

Two GitHub Actions secrets are required:

- secret `DEPLOY_SSH_KEY`: private Ed25519 key accepted by the server's `root` account;
- secret `ROUTERAI_API_KEY`: RouterAI API key used by M3-M5.

One non-secret repository variable is required:

- variable `DEPLOY_HOST`: public IPv4 address of the server.

The workflow is `.github/workflows/deploy.yml` and runs on pushes to `master` or manually via `workflow_dispatch`.

## One-time server preparation

The only prerequisites outside GitHub are:

1. root SSH access using the key stored in `DEPLOY_SSH_KEY`;
2. inbound TCP 22, 80 and 443 to the public IPv4 (UDP 443 is optional for HTTP/3).

No DNS record or domain registration is required. Docker does not have to be preinstalled; the workflow installs it when necessary.

If this is a dedicated experiment server and all Docker data from the previous project may be discarded, run this once directly on the server before the first deployment:

```bash
ids=$(docker ps -aq 2>/dev/null || true); [ -z "$ids" ] || docker rm -f $ids; docker system prune -af --volumes; rm -rf /opt/article-interaction-automation; rm -f /etc/article-interaction-automation.env /tmp/release.tar.gz /tmp/routerai.key
```

Do not run that command after collecting experiment data: it removes Docker volumes, including the experiment database, and deletes the invitation signing key.

## M4 ASR model in backend image

M4 uses self-hosted `ai-sage/GigaAM-v3` revision `e2e_rnnt` on CPU. The ~449 MB model is downloaded **during the backend Docker build** into `/opt/asr-model`; runtime sets `HF_HUB_OFFLINE=1`, so participant requests never download model files. The deploy script removes the obsolete faster-whisper cache volume before the first GigaAM build to avoid storing both model copies on the small server disk. No additional GitHub secret is required.

The first GigaAM build therefore needs outbound HTTPS access to Hugging Face. The selected `e2e_rnnt` repository snapshot is about 449 MB, substantially smaller than the previous ~1.6 GB `large-v3-turbo` cache. Build cache and the obsolete faster-whisper model volume are removed before the migration build to preserve headroom on the ~10 GB host. Later runtime requests do not need Hugging Face. Do not run `docker system prune --volumes` after deployment because it can delete the experiment-data volume.
