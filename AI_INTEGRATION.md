# AI Integration — Gemini Foundation

This document covers the **Gemini AI provider foundation** for Extractly.
It does **not** cover full invoice extraction (that comes in a later phase).

## 1. Architecture

```text
app/services/ai/
├── base.py              # ExtractionProvider (existing invoice extraction)
├── provider.py          # Mock + OpenAI extraction providers
├── foundation.py        # AIProvider: health / models / generate_text
├── gemini_provider.py   # GeminiProvider (google-genai SDK)
├── factory.py           # get_ai_provider() for foundation routes
├── exceptions.py        # Safe AI errors (no secrets)
└── prompts.py           # Extraction prompts (unchanged)
```

| Layer | Purpose |
|-------|---------|
| `get_ai_provider()` | Connectivity, model discovery, smoke tests |
| `get_extraction_provider()` | Document extraction (`mock` / `openai`; Gemini extraction **not yet**) |

Routes: `GET/POST /api/v1/ai/*` (development environments only).

## 2. Environment variables

| Variable | Required when | Description |
|----------|---------------|-------------|
| `AI_PROVIDER` | always | `mock`, `openai`, or `gemini` |
| `GEMINI_API_KEY` | `AI_PROVIDER=gemini` | Gemini API key from Google AI Studio / Cloud |
| `GEMINI_MODEL` | `AI_PROVIDER=gemini` | Model id, e.g. `gemini-2.0-flash` |
| `AI_MODEL` / `AI_API_KEY` | OpenAI extraction | Existing OpenAI settings |

Example `.env`:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.0-flash
```

**Never commit real keys.** Prefer `.env` (gitignored) and leave `.env.example` empty.

## 3. Configure Gemini

1. Create an API key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Set `GEMINI_API_KEY` in `backend/.env`.
3. Set `AI_PROVIDER=gemini`.
4. Install dependency: `pip install google-genai` (listed in `requirements.txt`).
5. Restart uvicorn.
6. Call `GET /api/v1/ai/health`.

Startup validates that the key/model env vars exist when `AI_PROVIDER=gemini`.
It does **not** call Gemini on startup (avoids paid/list traffic).

## 4. Discover available models

```bash
curl -s http://127.0.0.1:8000/api/v1/ai/models | jq
```

Use the returned `name` values as candidates for `GEMINI_MODEL`.

## 5. Select `GEMINI_MODEL`

1. List models via `/api/v1/ai/models`.
2. Prefer a **Flash** multimodal model for future invoice vision (cost/latency).
3. Prefer a **Pro** model if you need maximum accuracy later.
4. Put the short id in `.env` (without the `models/` prefix), e.g.:

```env
GEMINI_MODEL=gemini-2.5-flash
```

Verified accessible models on a working key include (non-exhaustive):
`gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-3.5-flash`, `gemini-3-flash-preview`, …

**Recommended for future invoice vision extraction:** `gemini-2.5-flash` (strong multimodal / cost balance).  
`gemini-3.5-flash` is also valid if you prefer the newer Flash line.

If health reports the configured model is missing, update `GEMINI_MODEL` to a listed name.

## 6. Test endpoints (development only)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/ai/health` | Config + connectivity |
| `GET` | `/api/v1/ai/models` | List accessible models |
| `POST` | `/api/v1/ai/test` | Simple text generation smoke test |

```bash
curl -s http://127.0.0.1:8000/api/v1/ai/health

curl -s http://127.0.0.1:8000/api/v1/ai/models

curl -s -X POST http://127.0.0.1:8000/api/v1/ai/test \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Reply with exactly: Gemini connection successful"}'
```

These return **404** when `ENVIRONMENT` is not development/dev/local/test.

## 7. Security notes

- API keys are loaded only from settings / environment.
- Keys are **never** returned from endpoints or OpenAPI examples.
- Error sanitization redacts key-shaped strings before responses/logs.
- Do not log `settings.GEMINI_API_KEY` or raw SDK auth headers.
- Foundation routes are development-gated.

## 8. Adding future providers

1. Implement `AIProvider` in `foundation.py` (`health_check`, `list_models`, `generate_text`).
2. Register in `factory.py` based on `AI_PROVIDER`.
3. For document extraction, implement `ExtractionProvider.extract_document` and register in `get_extraction_provider()`.

## 9. Invoice extraction (Gemini Vision)

With `AI_PROVIDER=gemini`, `POST /api/v1/documents/{id}/extract` uses
`GeminiExtractionProvider`:

1. Load processed page images from storage  
2. Send all pages to Gemini Vision (`response_mime_type=application/json`)  
3. Parse + sanitize (invalid bboxes → null)  
4. Pydantic validate + normalize  
5. Save `extractions` / `field_values`  
6. Run validation rules → `ready` or `needs_review`

Mock (`AI_PROVIDER=mock`) and OpenAI providers remain available.

Recommended extraction model: `GEMINI_MODEL=gemini-2.5-flash` or `gemini-3.5-flash`.

## SDK

Official package: **`google-genai`** (`from google import genai`).

Do **not** use the deprecated `google-generativeai` package.
