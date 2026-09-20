"""CLI switching must preserve user settings and use provider limits."""
import json
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from xdog.ai import cli, proxy
from xdog.ai.types import Model

MODEL = Model(
    id="copilot/gpt-6-astra", name="GPT-6 Astra", context_window=1_178_000,
    max_prompt_tokens=1_050_000, max_tokens=128_000, reasoning=True,
    supported_efforts=("low", "medium", "high", "xhigh"),
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-custom"))
    return tmp_path


def test_codex_switch_replaces_stale_limits_preserves_toml_and_auth(home):
    from xdog.ai.cli_switch import configure_codex

    root = home / "codex-custom"
    root.mkdir()
    config = root / "config.toml"
    config.write_text(
        '# keep my comment\nweb_search = "live"\nmodel_context_window = 353346\n'
        'model_auto_compact_token_limit = 9999999\n'
        '[features]\nmulti_agent = true\n'
        '[projects."/some/project"]\ntrust_level = "trusted"\n'
        '[mcp_servers.local]\nargs = ["a", "b"]\n'
    )
    (root / "auth.json").write_text('{"tokens": {"access_token": "keep-me"}}')

    configure_codex(MODEL, [MODEL], "http://localhost:8082/v1/", "test-key")

    result = tomllib.loads(config.read_text())
    assert result["model"] == MODEL.id
    assert result["web_search"] == "live"
    assert result["model_context_window"] == 1_178_000
    assert result["model_auto_compact_token_limit"] == 945_000
    assert result["features"]["multi_agent"] is True
    assert result["mcp_servers"]["local"]["args"] == ["a", "b"]
    assert "# keep my comment" in config.read_text()
    assert result["model_providers"]["xdogproxy"]["base_url"] == "http://localhost:8082/v1"
    auth = json.loads((root / "auth.json").read_text())
    assert auth["tokens"]["access_token"] == "keep-me"
    assert "OPENAI_API_KEY" not in auth
    assert result["model_providers"]["xdogproxy"]["experimental_bearer_token"] == "test-key"
    catalog = json.loads(Path(result["model_catalog_json"]).read_text())
    assert catalog["models"][0]["slug"] == MODEL.id
    assert catalog["models"][0]["context_window"] == 1_178_000
    assert (root / "config.toml.bak").exists()


def test_unknown_limit_rejects_codex_switch_without_writes(home):
    from xdog.ai.cli_switch import configure_codex

    with pytest.raises(ValueError, match="context"):
        configure_codex(replace(MODEL, context_window=0), [MODEL], "http://localhost:8082", "key")
    assert not (home / "codex-custom").exists()


def test_catalog_refresh_replaces_provider_rows_preserves_other_providers(home):
    from xdog.ai.cli_switch import configure_codex
    from xdog.ai.codex_catalog import catalog_path, refresh_catalog

    other = Model(id="antigravity/gemini", context_window=1_000_000)
    configure_codex(MODEL, [MODEL, other], "http://localhost:8082", "key")
    updated = replace(MODEL, context_window=1_200_000)
    refresh_catalog("copilot", [updated])
    rows = {m["slug"]: m for m in json.loads(catalog_path().read_text())["models"]}
    assert rows[MODEL.id]["context_window"] == 1_200_000
    assert rows[other.id]["context_window"] == 1_000_000
    config = tomllib.loads((home / "codex-custom" / "config.toml").read_text())
    assert config["model_context_window"] == 1_200_000
    refresh_catalog("copilot", [])
    assert [m["slug"] for m in json.loads(catalog_path().read_text())["models"]] == [other.id]


def test_refresh_leaves_config_alone_after_switching_provider(home):
    from xdog.ai.cli_switch import configure_codex
    from xdog.ai.codex_catalog import refresh_catalog

    configure_codex(MODEL, [MODEL], "http://localhost:8082", "key")
    config = home / "codex-custom" / "config.toml"
    text = config.read_text().replace('model_provider = "xdogproxy"', 'model_provider = "openai"')
    config.write_text(text)
    refresh_catalog("copilot", [replace(MODEL, context_window=1_200_000)])
    assert config.read_text() == text


@pytest.mark.parametrize("provider_id", ["copilot", "antigravity"])
async def test_provider_sync_refreshes_registered_catalog(home, provider_id):
    from xdog.ai.cli_switch import configure_codex
    from xdog.ai.codex_catalog import catalog_path
    from xdog.ai.providers.antigravity import AntigravityProvider
    from xdog.ai.providers.copilot import CopilotProvider

    model = replace(MODEL, id=f"{provider_id}/model")
    configure_codex(model, [model], "http://localhost:8082", "key")

    class Vendor:
        async def sync_models(self, ttl, force):
            return (replace(model, context_window=1_300_000),)

    provider = CopilotProvider() if provider_id == "copilot" else AntigravityProvider()
    provider._vendor = Vendor()
    await provider.sync_models(force=True)
    assert json.loads(catalog_path().read_text())["models"][0]["context_window"] == 1_300_000


def test_fetch_models_roundtrips_proxy_metadata(monkeypatch):
    import httpx
    from xdog.ai.cli_switch import fetch_models

    def respond(url, **kwargs):
        assert url == "http://localhost:8082/v1/models"
        assert kwargs["headers"]["Authorization"] == "Bearer key"
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [proxy._model_metadata(MODEL)]})

    monkeypatch.setattr(httpx, "get", respond)
    model = fetch_models("http://localhost:8082/v1/", "key")[0]
    assert (model.context_window, model.max_prompt_tokens, model.max_tokens) == (1_178_000, 1_050_000, 128_000)
    assert model.supported_efforts == ("low", "medium", "high", "xhigh")


def test_legacy_switch_config_is_used_without_printing_secret(home, monkeypatch, capsys):
    legacy = home / ".config" / "switch-cli" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"proxy_url": "http://localhost:9000", "api_key": "secret-value"}')
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", "config"])
    cli.main()
    output = capsys.readouterr().out
    assert "http://localhost:9000" in output
    assert "secret-value" not in output


def test_sync_does_not_create_catalog_before_opt_in(home):
    from xdog.ai.codex_catalog import catalog_path, refresh_catalog

    refresh_catalog("copilot", [MODEL])
    assert not catalog_path().exists()


def test_switch_cli_direct_model_uses_proxy_metadata(home, monkeypatch, capsys):
    from xdog.ai import cli_switch

    monkeypatch.setattr(cli_switch, "fetch_models", lambda *_: [MODEL])
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", "codex", "copilot", "--model", MODEL.id])
    cli.main()
    config = tomllib.loads((home / "codex-custom" / "config.toml").read_text())
    assert config["model_context_window"] == 1_178_000
    assert "xdog-proxy-key" not in capsys.readouterr().out


def test_proxy_model_endpoints_expose_limits():
    class Provider:
        def models(self):
            return [MODEL]

        def model(self, model_id):
            return MODEL if model_id == MODEL.id else None

    provider = Provider()
    for row in [proxy._list_models(provider)["data"][0], proxy._get_model(provider, MODEL.id)]:
        assert row["context_window"] == 1_178_000
        assert row["max_prompt_tokens"] == 1_050_000
        assert row["max_output_tokens"] == 128_000


@pytest.mark.parametrize("agent", ["claude", "gemini"])
def test_switch_other_agents_preserves_settings(home, monkeypatch, agent):
    from xdog.ai import cli_switch

    monkeypatch.setattr(cli_switch, "fetch_models", lambda *_: [MODEL])
    if agent == "claude":
        settings = home / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text('{"permissions": {"allow": ["Read"]}, "env": {"CUSTOM": "keep"}}')
        answers = iter(["1", "0", "0"])
    else:
        settings = home / ".gemini" / ".env"
        settings.parent.mkdir()
        settings.write_text('# keep comment\nCUSTOM="keep"\n')
        answers = iter(["1"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", agent, "copilot"])
    cli.main()
    text = settings.read_text()
    assert "keep" in text
    assert MODEL.id in text
    if agent == "claude":
        assert json.loads(text)["permissions"] == {"allow": ["Read"]}
    else:
        assert "# keep comment" in text


def test_claude_switch_sets_context_and_replaces_stale_model(home, monkeypatch):
    from xdog.ai import cli_switch

    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "model": "old-model",
        "permissions": {"allow": ["Read"]},
        "env": {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "200000", "CUSTOM": "keep"},
    }))
    monkeypatch.setattr(cli_switch, "fetch_models", lambda *_: [MODEL])
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", "claude", "copilot", "--model", MODEL.id])
    cli.main()

    result = json.loads(settings.read_text())
    assert result["model"] == MODEL.id
    assert result["env"]["ANTHROPIC_MODEL"] == MODEL.id
    assert result["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "1178000"
    assert result["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "945000"
    assert result["env"]["CUSTOM"] == "keep"
    assert result["permissions"] == {"allow": ["Read"]}


def test_claude_unknown_context_clears_previous_model_limits(home):
    from xdog.ai.cli_switch import _configure_claude

    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"env": {
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1178000", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "945000",
    }}))
    model = replace(MODEL, id="copilot/unknown", context_window=0)
    _configure_claude([model], model, "http://localhost:8082", "key", True)
    result = json.loads(settings.read_text())
    assert result["model"] == model.id
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in result["env"]
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in result["env"]
