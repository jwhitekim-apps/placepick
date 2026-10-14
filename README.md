# Placepick

**Languages:** English | [한국어](README_KO.md)

**Live demo:** <https://plick.2joon.com/> — a personal deployment; it may go
down or slow down under heavy traffic since it runs on the maintainer's own
API keys/quota.

Naver blog search for any place turns up dozens of nearly identical,
SEO-optimized posts — reading through them to find out where people
*actually* keep going is the real cost. Placepick reads them for you, and
instead of a star rating (arbitrary, easy to game), its trust signal is the
one thing that's hard to fake: **how many independent posts, by different
people, mention the same place**.

Type a keyword and Placepick collects Naver blog posts about it, uses Gemini
to pull out candidate business names, and cross-checks every candidate
against Kakao Map — a name only survives if it resolves to a real, existing
place. What's left is a short list, ranked by genuine repeat mentions, each
one backed by links to the actual posts that mentioned it.

Not every keyword resolves to one neat list, though. "Busan trip" spans
neighborhoods that have nothing to do with each other — a hotel in Haeundae,
a temple in Gijang, a market in Nampo-dong. Flattening that into one ranked
list would hide more than it reveals, so when a query is genuinely broad,
Placepick shows a region → category breakdown instead, and you drill into
the one area you actually care about.

Collection depth scales with the keyword automatically: narrow, specific
searches stop as soon as the signal is clear; broad ones keep collecting
more blog posts as long as the number of genuinely repeated candidates keeps
growing. There's nothing to configure — you only ever type a keyword.

## How it works

1. Type a keyword (e.g. "성수동 브런치").
2. The app collects Naver blog posts about it — automatically pulling more if
   the signal looks thin, fewer if it's already clear.
3. Gemini extracts candidate business names from the posts; each candidate is
   checked against Kakao Map, so only names that resolve to a real, existing
   place survive.
4. If the results cluster in one area, you get a ranked list with how many
   independent posts mentioned each place. If the keyword is broad enough to
   span several distinct neighborhoods, you get a region/category breakdown
   instead — click a category to drill into detailed results for that area.
5. Follow the Kakao Map / Naver Map links to check hours, photos, and reviews,
   and save your own favorites there.

## Getting started

**Prerequisites:** Python 3.9+, Node.js 20.19+, and API keys for
[Gemini](https://ai.google.dev/), [Kakao REST](https://developers.kakao.com/),
and the [Naver Search API](https://developers.naver.com/apps/#/register)
(client ID + secret).

```bash
cp .env.example .env          # fill in your API keys

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && cd ..

.venv/bin/python backend/main.py
```

Open <http://127.0.0.1:8000>. FastAPI docs are at
<http://127.0.0.1:8000/docs>. `backend/main.py` builds the frontend
automatically if the Svelte source changed or no build exists yet, then
serves both the API and the built frontend from the same port — no separate
frontend dev server needed. Your API keys are used server-side only and
never sent to the browser.

## Commands

```bash
cd backend && ../.venv/bin/python -m pytest tests/ -q   # backend tests
cd frontend && npm run build                             # production frontend build
.venv/bin/python backend/main.py --reload                 # combined server, auto-reload on backend changes
cd frontend && npm run dev                                 # frontend dev server only
```

## CI/CD

Pushes and PRs to `main` run backend tests, a frontend build, and a Docker
build via GitHub Actions (`.github/workflows/ci-cd.yml`). A direct push to
`main` that passes all three also deploys over SSH to the configured server.

To enable deployment, add these repo secrets (Settings → Secrets and
variables → Actions):

| Secret | Description |
| --- | --- |
| `SSH_PRIVATE_KEY` | Private key for the deploy server (OpenSSH format) |
| `SSH_HOST` (or `SERVER_HOST`) | Deploy server host/IP |
| `SSH_PORT` | Deploy server SSH port |
| `SSH_USER` (or `SERVER_USER`) | Deploy server SSH user |

The deploy path is hardcoded in the workflow — edit the `Deploy` step in
`.github/workflows/ci-cd.yml` if yours differs. The repo must already be
cloned there with `git`, `docker`, and the `docker compose` plugin installed.

## Learn more

- [docs/decisions.md](docs/decisions.md) — design notes on why the extraction,
  matching, and scaling logic works the way it does, including approaches
  that were tried and rejected.
- [docs/design-spec.md](docs/design-spec.md) — the visual design system
  (palette, typography, layout principles) and the reasoning behind it.
