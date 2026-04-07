# Stock_Linbotv1 Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-04-01

## Active Technologies

- Python 3.11.9 runtime in `myenv` (repo convention remains Python 3.10+) + Flask, SQLAlchemy, pandas, line-bot-sdk v3, feedparser, Gemini SDK integration, legacy `requests`, planned `httpx`, planned LangChain tool/agent dependencies for `BaseTool` integration (001-modernize-twse-dataflow)

## Project Structure

```text
backend/
frontend/
tests/
```

## Commands

cd src; pytest; ruff check .

## Code Style

Python 3.11.9 runtime in `myenv` (repo convention remains Python 3.10+): Follow standard conventions

## Recent Changes

- 001-modernize-twse-dataflow: Added Python 3.11.9 runtime in `myenv` (repo convention remains Python 3.10+) + Flask, SQLAlchemy, pandas, line-bot-sdk v3, feedparser, Gemini SDK integration, legacy `requests`, planned `httpx`, planned LangChain tool/agent dependencies for `BaseTool` integration

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
