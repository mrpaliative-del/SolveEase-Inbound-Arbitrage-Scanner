
# SolveEase Inbound Arbitrage Scanner

A lightweight document-to-structured-data MVP.

## Architecture

- Frontend: static HTML on Cloudflare Pages.
- Backend: Python 3.11, FastAPI and Uvicorn on Render.
- Extraction: configurable OpenAI-compatible inference provider.
- Storage: SQLite daily request counters.
- Tests: pytest and FastAPI TestClient with mocked inference.

Cloudflare Pages serves the frontend. It does not execute `src/app.py`.
The Python backend runs separately on Render.

## Requirements

- Python 3.11
- An API key for a provider/model that supports the requested JSON Schema output
- A deployed frontend origin and backend origin for production use

## Local setup

Clone the repository and enter its directory:

    cd inbound-arbitrage-scanner

Create a virtual environment:

    python -m venv .venv

Activate it on Linux/macOS:

    source .venv/bin/activate

On Windows:

    .venv\Scripts\activate

Install dependencies:

    python -m pip install -r requirements.txt

Set environment variables using your shell or a local `.env` loader.
Python does not automatically load `.env` files. Never commit real keys.

Configure at least:

    INFERENCE_PROVIDER_API_KEY=your_real_provider_key
    INFERENCE_TARGET_MODEL=gpt-4o-mini
    DATABASE_PATH=data/inbound_arbitrage.db
    CORS_ORIGINS=http://localhost:8000

If your provider uses a non-default API endpoint, also configure:

    INFERENCE_PROVIDER_BASE_URL=https://your-provider-api.example

Run the backend:

    uvicorn src.app:app --reload --host 127.0.0.1 --port 8000

API documentation:

    http://127.0.0.1:8000/docs

Health check:

    http://127.0.0.1:8000/health

## Test without spending API credits

The tests mock the extraction provider:

    python -m pytest -v tests/

The tests verify validation, successful processing, failed extraction,
daily quota enforcement and usage reporting.

A passing test suite does not prove that a real provider key, deployment,
CORS configuration or production inference model works.

## Deploy the backend to Render

1. Push this repository to GitHub.
2. Create a Render Web Service from the repository.
3. Build command:

       pip install -r requirements.txt

4. Start command:

       uvicorn src.app:app --host 0.0.0.0 --port $PORT

5. Set these environment variables in Render:
   - `INFERENCE_PROVIDER_API_KEY`: your real provider key
   - `INFERENCE_TARGET_MODEL`: a compatible model name
   - `DATABASE_PATH`: `data/inbound_arbitrage.db`
   - `CORS_ORIGINS`: your exact deployed frontend origin
   - `DAILY_REQUEST_LIMIT`: `100`
   - `PER_CLIENT_PER_MINUTE`: `5`

6. For a non-default provider endpoint, set
   `INFERENCE_PROVIDER_BASE_URL` in the Render dashboard.

7. Deploy and verify `/health`.

The free filesystem may be ephemeral. Daily counters can reset after
a replacement or redeploy. Use persistent storage or a managed database
before relying on quotas for paid production service.

## Deploy the frontend to Cloudflare Pages

1. Create a Pages project connected to the GitHub repository.
2. Set the output directory to `public`.
3. No frontend build command is required for the static HTML page.
4. Replace the placeholder in `public/index.html`:

       const BACKEND_API_URL = "https://YOUR-RENDER-SERVICE.onrender.com";

   Use your actual Render service origin.
5. Update Render's `CORS_ORIGINS` with the exact deployed Pages origin,
   such as `https://your-project.pages.dev`.
6. Redeploy the backend after changing CORS settings.
7. Open the frontend and submit a harmless sample document.

The frontend must use HTTPS when calling a production HTTPS backend.

## Generate the sitemap

After deploying to a real domain:

Linux/macOS:

    SITE_DOMAIN=your-real-domain.com python scripts/build_sitemap.py

PowerShell:

    $env:SITE_DOMAIN="your-real-domain.com"
    python scripts/build_sitemap.py

The generator includes only HTML files present under `public/`.
Submit `/sitemap.xml` in Google Search Console after deployment.

## API request example

Base64-encoded text request:

    POST /api/v1/process-task
    Content-Type: application/json

    {
      "body": "U3VwcGxpZXI6IEV4YW1wbGU=",
      "isBase64Encoded": true
    }

The API returns a JSON response with `status`, `payload` and `usage`.
Provider errors return an HTTP error rather than fabricated extraction data.

## Security and limitations

- Never put provider API keys in browser JavaScript or Git.
- The frontend does not ask for an email address or account registration.
- The API currently has no user accounts or JWT authentication.
- The included rate limiter is in-memory and is not a distributed abuse
  prevention system.
- Hosting infrastructure may process client IP addresses in request logs.
- Do not upload sensitive or confidential documents.
- Extracted values may be wrong or incomplete; verify them independently.
- The parser accepts UTF-8 text. PDF extraction is not implemented.
- The service extracts document fields; it does not independently prove
  an arbitrage opportunity, profit, or transaction authenticity.
- The inference provider must support the JSON Schema response format
  used in `src/constraint_engine.py`.

## Project status

This repository is a deployment-ready starting point, not a claim that
the live service has already been deployed or independently verified.
