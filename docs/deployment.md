# Free public demo deployment

Faultline uses one [Render Free web service](https://render.com/docs/free). The root [Blueprint](../render.yaml) builds the container from the repository Dockerfile and starts one FastAPI process serving the React UI, API, and run worker. No paid database, volume, frontend host, or OpenAI API key is needed.

## What the free demo provides

- Visitors can run all five scenarios with the deterministic baseline, inspect state-based scorecards and traces, and compare compatible runs. Payments are simulated.
- `FAULTLINE_PUBLIC_DEMO=1` makes the API list and accept only the baseline agent, even if an `OPENAI_API_KEY` is accidentally configured. The UI labels the instance as a free demo.
- SQLite lives at `/tmp/faultline.db`. Run history is temporary. Render spins a free service down after 15 minutes without traffic; local files are lost on spin-down, restart, or redeploy. The next request may take about a minute while the service wakes. [Render documents these limits](https://render.com/docs/free).
- The service uses Render's free `onrender.com` HTTPS address. Keep the instance count at one; the queue and SQLite database are local to that process.

## Publish

1. Push the reviewed source and `render.yaml` to your GitHub repository. Wait for the GitHub Actions workflow to pass, including its container build. Do not commit `.env` files, keys, or local databases.
2. Sign in to Render, choose **New > Blueprint**, connect the GitHub repository, and select the branch containing `render.yaml`. Review the service configuration before applying it: `type: web`, `runtime: docker`, `plan: free`, one service, no database or disk. Do not add `OPENAI_API_KEY`.
3. Wait for the deployment to become healthy. Record the actual `https://…onrender.com` address. Render checks `/api/health`; the container binds to `0.0.0.0:$PORT`.
4. Open the URL and check the demo banner. Start `normal` and `response_loss` baseline runs; each should finish with a passing score and an inspectable trace. Confirm `/api/agents` lists only baseline and `/api/config` reports `public_demo: true`.
5. Add the verified public URL to the README and resume. Label it a **live demo with temporary history**, so visitors understand why earlier runs can disappear.

Do not add a payment method if the requirement is to avoid any possible overage charge. Render says services without a payment method are suspended or new builds are disabled when the relevant free monthly quotas are exhausted. [Usage and free-tier terms](https://render.com/docs/free) can change, so check the dashboard before publishing.

## Runtime contract

| Setting | Free demo value | Purpose |
| --- | --- | --- |
| `PORT` | `10000` | Public HTTP listener |
| `FAULTLINE_DB` | `/tmp/faultline.db` | Temporary SQLite state |
| `FAULTLINE_PUBLIC_DEMO` | `1` | Baseline-only API and UI disclosure |
| `FAULTLINE_STATIC_DIR` | `/app/frontend/dist` in the Dockerfile | Serve the built UI on the API origin |
| Health check | `/api/health` | Process availability |

Migrations run at startup. A queued run can resume after restart only when its database remains available; on this free deployment the database is normally lost on restart, so visitors should not expect recovery or durable history. With a persistent database, interrupted active runs are marked inconclusive and never blindly replayed after a possible side effect.

The deployment and hosted CI have not yet been verified. Local tests and an actual hosted run are separate release evidence.
