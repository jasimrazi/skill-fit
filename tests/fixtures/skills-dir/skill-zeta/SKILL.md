---
name: api-testing
description: Tests REST APIs using pytest and httpx. Run when writing or debugging HTTP endpoint tests for FastAPI, Flask, or Django REST Framework applications.
---
# API Testing

Instructions for writing HTTP endpoint tests with pytest and httpx.

## When to use
- Writing tests for new API endpoints
- Debugging a failing integration test
- Setting up test fixtures and async test clients

## Steps
1. Install `pytest` and `httpx`
2. Use `TestClient` for sync or `AsyncClient` for async endpoints
3. Assert status codes, response bodies, and headers
4. Use `pytest.fixture` for shared test state
