# Public demo on Vercel (Hobby, free) with a Neon (free) database

Hosting history, 2026-11-16: Render asked for a card; Back4app's free plan gave only a temporary URL that expired after
about two hours. Vercel's Hobby plan is free and is used with a GitHub sign-in, and Neon Free needs no card either.

How it runs here: Vercel's FastAPI support picks up `app.py` in this folder (a module-level `app`), installs
`requirements.txt` (kept identical to `backend/requirements.txt` by `tests/test_deploy_files.py`) on Python 3.13
(`.python-version`), and runs the build command in `vercel.json`, which builds the React UI into `frontend/dist`.
The FastAPI app serves that UI exactly as in the Docker image. `.vercelignore` leaves out the lakehouse and test kit.

The API runs as a serverless function, so it does not load the baseline itself: it uses the Neon database that the
Back4app start-up loaded on 2026-11-16 (all 500 trades and the reconciliation run). A fresh database would be loaded
once with `python -m cf360.bootstrap` from any machine that can reach it. Sign-in uses the users stored in that
database, so the demo password stays the one chosen for the first load.

Free-tier facts, checked 2026-11-16 against vercel.com/docs/plans/hobby and vercel.com/docs/functions/runtimes/python:
1,000,000 function invocations, 4 active CPU hours and 100 GB transfer a month; functions up to 300 seconds; Python 3.12
(default), 3.13 and 3.14; 500 MB bundle limit. Going over a Hobby limit pauses the feature until the 30 days pass;
there is no billing on Hobby.

## Steps

1. Sign in at https://vercel.com with GitHub (Hobby plan).
2. **Add New → Project**, import `banking-ai-portfolio`.
3. **Root Directory → Edit →** `projects/p7_capitalflow360`.
4. **Environment Variables:** `CF360_DATABASE_URL` (the Neon connection string) and `CF360_JWT_SECRET` (32+ random characters).
5. **Deploy.** The address is `https://<project>.vercel.app`.

Then run the "CapitalFlow 360 live demo check" workflow with that address, and record it in `docs/deployment.json`.
