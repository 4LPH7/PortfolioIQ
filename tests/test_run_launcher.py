"""First-run .env creation and unlock-key lookup in run.py."""

import run


def test_first_run_generates_key_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ROOT_DIR", tmp_path)
    (tmp_path / ".env.example").write_text("DRY_RUN_MODE=true\nPORTFOLIOIQ_API_KEY=change_me\n")

    run.ensure_env_file()
    key = run.read_unlock_key()
    assert key not in ("change_me", run.DEFAULT_API_KEY) and len(key) >= 24
    assert "DRY_RUN_MODE=true" in (tmp_path / ".env").read_text()

    run.ensure_env_file()  # second run keeps the existing .env
    assert run.read_unlock_key() == key


def test_key_appended_when_template_lacks_it(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ROOT_DIR", tmp_path)
    (tmp_path / ".env.example").write_text("DRY_RUN_MODE=true\n")
    run.ensure_env_file()
    assert run.read_unlock_key() != run.DEFAULT_API_KEY


def test_read_unlock_key_strips_quotes_comments_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ROOT_DIR", tmp_path)
    assert run.read_unlock_key() == run.DEFAULT_API_KEY  # no .env at all
    (tmp_path / ".env").write_text('PORTFOLIOIQ_API_KEY="abc123" # mine\n')
    assert run.read_unlock_key() == "abc123"
    (tmp_path / ".env").write_text("PORTFOLIOIQ_API_KEY=\n")
    assert run.read_unlock_key() == run.DEFAULT_API_KEY
