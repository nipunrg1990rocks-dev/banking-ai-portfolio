# Public demo on Back4app Containers (free) with a Neon (free) database

> Not used any more: the free plan only gives a temporary URL. The demo went live here on 2026-11-16 and was checked
> from GitHub Actions (run 43835610967), but about two hours later Back4app showed "Your App temporary URL has expired.
> Please upgrade to a paid plan to continue developing, or redeploy your app". The demo now uses
> [Vercel + Neon](deploy_vercel_neon.md). The Neon database it loaded is reused.

Render's dashboard asked for a card when this was tried on 2026-11-16, both through the Blueprint and through a
manual web service, so the demo uses Back4app Containers instead. Its pricing page says the free tier needs no credit card.
The database stays on Neon Free (no card, never billed).

Free-tier facts, checked 2026-11-16 against back4app.com/pricing/container-as-a-service and back4app.com/docs-containers:

| | Back4app Containers free | Neon Free |
|---|---|---|
| Size | 0.25 CPU, 256 MB, shared CPU, US region | up to 2 CU, scales to zero after 5 minutes idle |
| Limits | 100 GB transfer a month; deploys this folder's Dockerfile from GitHub | 1 GB storage, 100 CU-hours a month per project |
| Card | not required (pricing FAQ) | not required |

Tested here on 2026-11-16 with the same limits (`--cpus 0.25 --memory 256m`, no swap) and a fresh PostgreSQL 16: the
baseline load took 94 seconds, memory peaked at about 150 MB and settled at about 117 MB, there was no out-of-memory
kill, and login, 500 trades and the UI all answered. Against Neon over the internet the first load will be slower.

## Steps

1. **Neon.** Sign in at https://console.neon.tech with GitHub. Create a project named `capitalflow360` in an AWS US region.
   Open **Connect** and copy the connection string (it starts with `postgresql://`).
2. **Back4app.** Sign in at https://containers.back4app.com with GitHub. Choose **Create new app > Containers as a Service**,
   give it access to `banking-ai-portfolio`, then set:
   - Branch `main`, root directory `./projects/p7_capitalflow360`
   - Port `8000`
   - Environment variables: `CF360_DATABASE_URL` (the Neon string), `CF360_DEMO_PASSWORD` (the password visitors use),
     `CF360_JWT_SECRET` (any random 32+ characters)
3. Create the app. The first deploy builds the image and then loads the baseline. If the first deploy is marked failed
   during that load, redeploy once: the start-up detects a half-finished load and redoes it (`cf360.bootstrap`).

The site is at the `*.b4a.run` address Back4app shows. Health check: `/api/health`.
