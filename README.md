# Placepick

Find the places mentioned in Naver blog search results without opening each post. Placepick uses Gemini to pull business names out of result titles and snippets, then matches them to Kakao Map places.

[Try the live demo](https://plick.2joon.com/) — a personal deployment on the maintainer's API quota, so it may be slow or unavailable.

![Example result for "성수동 브런치": three places ranked by mention count (12, 7, 5) with region, category, and source counts](docs/images/example-result.png)

<sub>The example result shown on the demo's landing page.</sub>

## What you get

- Places ranked by how often they are mentioned, with links to the source posts, Kakao Map, and Naver Map
- A region → category overview when results are sparse or spread out; pick a category to search that area
- Collection depth that adjusts to how often places repeat

Mention counts come from search results. They do not confirm visits or independent authors.

## Getting started

You need API keys for Gemini, Kakao Local REST, and Naver Search (blog and local). Run from the repository root.

```bash
cp .env.example .env
```

Replace all four placeholders in `.env` (`GEMINI_API_KEY`, `KAKAO_REST_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`) with real keys.

### Docker

```bash
docker compose up -d --build
```

Open <http://127.0.0.1:8001>.

### Without Docker

Python 3.11 and Node.js 20.x (20.19 or later). CI uses Python 3.11 and Node.js 20.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
(cd frontend && npm ci)
.venv/bin/python backend/main.py
```

Open <http://127.0.0.1:8000> and search for something like `성수동 브런치`. The server builds and serves the frontend itself. API docs are at `/docs`.

## Development

```bash
(cd frontend && npm run build)   # backend tests check the built frontend
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
```

For frontend work, keep the backend running and start `npm run dev` in `frontend/`. Vite proxies API calls to port 8000.

[GitHub Actions](.github/workflows/ci-cd.yml) runs frontend, backend, and Docker checks on pushes and pull requests to `main`. Pushes to `main` that pass all checks are deployed over SSH. To deploy your own copy, prepare the server (Git, Docker Compose, a clone with its `.env`), set the `SSH_*` secrets, and change the checkout path hardcoded in the `Deploy` step.

## Learn more

- [Extraction, matching, and collection decisions](docs/decisions.md) (Korean)
- [Visual design spec](docs/design-spec.md) (Korean)
- [Security policy](SECURITY.md)

## License

[MIT](LICENSE) © 2026 jwhitekim
