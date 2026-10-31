# Contributing to PortfolioIQ

Thanks for wanting to help! Every contribution counts: code, docs, translations, bug reports.

## Quick start

1. Fork the repo and clone your fork.
2. Set it up:

```bash
pip install -r requirements-dev.txt
python run.py   # needs Docker Desktop running
```

3. Make your change on a new branch (`git checkout -b fix/short-name`).
4. Check it:

```bash
pytest tests/
ruff check . && ruff format --check .
```

5. Open a pull request against `main` and describe what changed and why.

## Where to start

- Issues labelled [`good first issue`](https://github.com/4LPH7/PortfolioIQ/labels/good%20first%20issue) are small and well-scoped.
- Issues labelled [`help wanted`](https://github.com/4LPH7/PortfolioIQ/labels/help%20wanted) are bigger and need someone to own them.
- **Never** add code that places a real order from an AI output. Orders must stay dry-run by default.
- Have a question or an idea? Open a [Discussion](https://github.com/4LPH7/PortfolioIQ/discussions).

## Ground rules

- Keep pull requests small and focused; one change per PR.
- Never commit secrets, API keys or `.env` files.
- Be kind. Comment on an issue before starting so two people don't build the same thing.
