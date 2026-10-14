"""codex-truly tests.

Unit coverage for the pure logic, plus an integration pass that drives Hermes'
real context-resolution chain against a throwaway ``HERMES_HOME``. The premise
the whole plugin rests on — ``model_overrides`` winning over the Codex catalog
clamp — is only meaningful when exercised through Hermes itself, so that test
asserts the behaviour rather than the config file.

The network boundary is stubbed in tests: ``credentialed_provider_request`` is
replaced with a fake catalog, which is also how the credential contract is
checked (the plugin must ask Hermes to send, and must never resolve a token
itself).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
HERMES_SRC = Path(os.environ.get("HERMES_SRC", "/usr/local/lib/hermes-agent"))
CODEX_AUTH = Path(
    os.environ.get("CODEX_TRULY_TEST_AUTH", Path.home() / ".hermes" / "auth.json"))

if HERMES_SRC.is_dir() and str(HERMES_SRC) not in sys.path:
    sys.path.insert(0, str(HERMES_SRC))


def _hermes_importable() -> bool:
    """Whether Hermes itself is importable here.

    Much of this suite drives Hermes' own code (model-override resolution, the
    credentialed request route), so those tests must SKIP when Hermes is absent
    rather than fail: the plugin only ever runs inside Hermes, and a bare
    interpreter is the normal state of a CI runner.
    """
    try:
        import agent.model_metadata  # noqa: F401
        import hermes_cli.config  # noqa: F401
        return True
    except Exception:
        return False


HERMES_AVAILABLE = _hermes_importable()
requires_hermes = pytest.mark.skipif(
    not HERMES_AVAILABLE, reason="Hermes is not installed; this test drives its code")

# A realistic account catalog: gpt-6.1-sol advertises 500K (the raise that
# disabled Hermes' 272K->900K bump), everything else advertises 272K, and the
# ceiling is 872K everywhere.
FAKE_CATALOG = {
    "models": [
        {"slug": "gpt-6.1-sol", "context_window": 500_000, "max_context_window": 872_000},
        {"slug": "gpt-6-sol", "context_window": 272_000, "max_context_window": 872_000},
        {"slug": "gpt-6-luna", "context_window": 272_000, "max_context_window": 872_000},
        {"slug": "gpt-5.6-sol", "context_window": 272_000, "max_context_window": 872_000},
        {"slug": "gpt-5.5", "context_window": 272_000, "max_context_window": 272_000},
        {"slug": "gpt-reserve", "context_window": 272_000, "max_context_window": 872_000,
         "visibility": "hide"},
    ]
}


def _load_plugin():
    """Import the hyphenated plugin dir the same way Hermes' capability probe does."""
    name = "codex_truly_plugin"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "__init__.py", submodule_search_locations=[str(REPO)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plugin():
    if HERMES_SRC.is_dir() and str(HERMES_SRC) not in sys.path:
        sys.path.insert(0, str(HERMES_SRC))
    return _load_plugin()


@pytest.fixture
def temp_home(monkeypatch):
    home = tempfile.mkdtemp(prefix="codex-truly-test-")
    monkeypatch.setenv("HERMES_HOME", home)
    (Path(home) / "config.yaml").write_text("")
    yield Path(home)
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def fake_catalog(plugin, monkeypatch):
    """Serve FAKE_CATALOG instead of calling the live backend."""
    entries = FAKE_CATALOG["models"]

    def fetch(timeout: float = 20.0):
        return entries, None

    monkeypatch.setattr(plugin.codex_api, "fetch_catalog", fetch)
    monkeypatch.setattr(plugin.report.codex_api, "fetch_catalog", fetch)
    return entries


def _manifest() -> dict:
    return yaml.safe_load((REPO / "plugin.yaml").read_text())


def _write_override(path: Path, model_id: str, window: int) -> None:
    """The canonical entry shape, written the way the plugin writes it."""
    data = yaml.safe_load(path.read_text() or "{}") or {}
    bucket = data.setdefault("model_overrides", {}).setdefault("openai-codex", {})
    bucket[model_id] = {"context_window": window}
    path.write_text(yaml.safe_dump(data))


def _resolved_offline(model_id: str) -> int | None:
    """Hermes' answer *without* a credential — the static fallback table."""
    from agent.model_metadata import get_model_context_length

    return int(get_model_context_length(
        model_id, base_url="https://chatgpt.com/backend-api/codex", api_key="",
        provider="openai-codex"))


def _resolved(model_id: str) -> int | None:
    """Hermes' own answer for a model, with the profile credential attached."""
    from agent.model_metadata import get_model_context_length
    from hermes_cli.auth import resolve_codex_runtime_credentials

    creds = resolve_codex_runtime_credentials(read_only=True)
    token = str(creds.get("api_key") or "")
    if not token:
        return None
    return int(get_model_context_length(
        model_id, base_url=str(creds.get("base_url") or ""), api_key=token,
        provider="openai-codex"))


# -- schema and config ---------------------------------------------------

class TestManifest:
    def test_manifest_and_python_schema_agree(self, plugin):
        assert set(_manifest()["config_schema"]) == set(plugin.CONFIG_SCHEMA)

    def test_declared_tool_matches_registration(self, plugin):
        assert _manifest()["provides_tools"] == ["codex_truly"]

    def test_defaults_agree(self, plugin):
        for key, spec in _manifest()["config_schema"].items():
            assert plugin.CONFIG_SCHEMA[key]["default"] == spec["default"], key

    def test_declares_the_sign_in_it_uses(self):
        # Hermes refuses credentialed_provider_request() for a provider the
        # manifest does not list, so this is load-bearing, not documentation.
        assert "openai-codex" in _manifest().get("requires_auth", [])


class TestConfig:
    def test_out_of_range_is_clamped_not_rejected(self, plugin):
        config = plugin.CodexTrulyConfig
        assert config(target_context_window=5_000_000).target_context_window == 918_000
        assert config(target_context_window=10).target_context_window == 272_000

    def test_garbage_values_degrade_to_defaults(self, plugin):
        config = plugin.CodexTrulyConfig(
            enabled="nonsense", target_context_window="wide", provider_keys="", models="a, b")
        assert config.enabled is True
        assert config.target_context_window == 900_000
        assert config.provider_key_list == ("openai-codex",)
        assert config.model_list == ("a", "b")

    def test_string_booleans(self, plugin):
        assert plugin.CodexTrulyConfig(apply_to_base_slugs="TRUE").apply_to_base_slugs is True
        assert plugin.CodexTrulyConfig(apply_to_base_slugs="0").apply_to_base_slugs is False


# -- model selection -----------------------------------------------------

class TestTargets:
    """Pure helpers — no Hermes needed."""

    def test_measurement_is_recorded_not_assumed(self, plugin):
        entry = plugin.targets.measured_ceiling("gpt-6.1-sol")
        assert entry["accepted"] < entry["rejected"]

    def test_catalog_promise_is_the_larger_of_the_two_windows(self, plugin):
        assert plugin.codex_api.promised_window(
            {"context_window": 272_000, "max_context_window": 872_000}) == 872_000
        assert plugin.codex_api.promised_window({"context_window": 500_000}) == 500_000
        assert plugin.codex_api.promised_window({}) == 0

    def test_the_default_stays_under_the_safety_ceiling(self, plugin):
        assert plugin.config.DEFAULT_CONTEXT_WINDOW <= plugin.config.MAX_CONTEXT_WINDOW


@requires_hermes
class TestModelSelection:
    """Eligibility comes from Hermes' own rules, so these need Hermes importable."""

    def test_only_hermes_eligible_slugs_are_managed(self, plugin):
        slugs = ["gpt-6.1-sol", "gpt-5.5", "gpt-reserve", "not-a-model"]
        managed = plugin.targets.managed_ids(slugs, apply_to_base_slugs=False)
        assert managed == [("gpt-6.1-sol", "gpt-6.1-sol-900k")]

    def test_base_slugs_opt_in(self, plugin):
        managed = plugin.targets.managed_ids(["gpt-6-sol"], apply_to_base_slugs=True)
        assert managed == [("gpt-6-sol", "gpt-6-sol-900k"), ("gpt-6-sol", "gpt-6-sol")]

    def test_explicit_model_filter_is_case_insensitive(self, plugin):
        managed = plugin.targets.managed_ids(
            ["gpt-6-sol", "gpt-6-luna"], apply_to_base_slugs=False, only=("GPT-6-LUNA",))
        assert managed == [("gpt-6-luna", "gpt-6-luna-900k")]


# -- the credential contract --------------------------------------------

@requires_hermes
class TestCredentialHandling:
    """Rule: the plugin asks Hermes to attach the sign-in; it never holds one."""

    def test_catalog_goes_through_the_credentialed_route(self, plugin, monkeypatch):
        calls = []

        class FakeResponse:
            status = 200
            text = ""

            def json(self):
                return FAKE_CATALOG

        def fake_request(provider, method, url, *, json=None, headers=None, timeout=30.0):
            calls.append((provider, method, url))
            return FakeResponse()

        monkeypatch.setattr(
            "hermes_cli.plugin_provider_requests.credentialed_provider_request", fake_request)
        entries, error = plugin.codex_api.fetch_catalog()
        assert error is None and len(entries) == len(FAKE_CATALOG["models"])
        assert calls and calls[0][0] == "openai-codex"
        assert calls[0][2].startswith("https://chatgpt.com/")

    def test_plugin_does_not_resolve_a_token_directly(self, plugin):
        # Behavioural guard for the catalog path: it must not import the
        # credential resolver at all.
        import inspect

        source = inspect.getsource(plugin.codex_api)
        assert "resolve_codex_runtime_credentials" not in source
        assert "agent.codex_headers" not in source


# -- apply / revert ------------------------------------------------------

@requires_hermes
class TestApplyPlan:
    def test_plan_targets_every_widenable_model(self, plugin, fake_catalog):
        planned = plugin.overrides.plan(plugin.CodexTrulyConfig(target_context_window=900_000))
        ids = {row["model"] for row in planned["rows"]}
        assert ids == {
            "gpt-6.1-sol-900k", "gpt-6-sol-900k", "gpt-6-luna-900k", "gpt-5.6-sol-900k"}
        # gpt-5.5 is not -900k eligible, gpt-reserve is hidden-but-unlisted.
        assert "gpt-5.5-900k" not in ids

    def test_plan_skips_a_model_the_catalog_already_promises(self, plugin, monkeypatch):
        entries = [{"slug": "gpt-6-sol", "context_window": 950_000,
                    "max_context_window": 950_000}]
        monkeypatch.setattr(plugin.codex_api, "fetch_catalog", lambda timeout=20.0: (entries, None))
        planned = plugin.overrides.plan(plugin.CodexTrulyConfig(target_context_window=900_000))
        assert planned["rows"] == []
        assert planned["skipped"][0]["reason"].startswith("catalog already promises")

    def test_apply_writes_the_canonical_entry_shape(self, plugin, temp_home, fake_catalog):
        payload = plugin.overrides.apply_overrides(
            plugin.CodexTrulyConfig(target_context_window=900_000))
        assert payload["ok"], payload
        written = yaml.safe_load((temp_home / "config.yaml").read_text() or "{}")
        entry = written["model_overrides"]["openai-codex"]["gpt-6-sol-900k"]
        assert entry == {"context_window": 900_000}
        # and it selects the Codex image provider when none is set
        assert written["image_gen"]["provider"] == "openai-codex"

    def test_an_existing_image_provider_is_never_overwritten(
        self, plugin, temp_home, fake_catalog
    ):
        (temp_home / "config.yaml").write_text(
            yaml.safe_dump({"image_gen": {"provider": "fal"}}))
        payload = plugin.overrides.apply_overrides(plugin.CodexTrulyConfig())
        assert payload["ok"], payload
        written = yaml.safe_load((temp_home / "config.yaml").read_text() or "{}")
        assert written["image_gen"]["provider"] == "fal"

    def test_apply_refuses_without_a_catalog(self, plugin, temp_home, monkeypatch):
        monkeypatch.setattr(
            plugin.codex_api, "fetch_catalog", lambda timeout=20.0: ([], "not signed in"))
        monkeypatch.setattr(
            plugin.report.codex_api, "fetch_catalog", lambda timeout=20.0: ([], "not signed in"))
        payload = plugin.overrides.apply_overrides(plugin.CodexTrulyConfig())
        assert payload["ok"] is False
        assert "not signed in" in payload["error"]

    def test_revert_removes_only_its_own_values(self, plugin, temp_home, fake_catalog):
        config = plugin.CodexTrulyConfig(target_context_window=900_000)
        plugin.overrides.apply_overrides(config)
        _write_override(temp_home / "config.yaml", "hand-edited", 123_456)

        payload = plugin.overrides.revert_overrides(config)
        assert payload["ok"], payload
        written = yaml.safe_load((temp_home / "config.yaml").read_text() or "{}")
        assert written["model_overrides"]["openai-codex"] == {
            "hand-edited": {"context_window": 123_456}}

    def test_purge_all_clears_managed_ids_regardless_of_value(
        self, plugin, temp_home, fake_catalog
    ):
        config = plugin.CodexTrulyConfig(target_context_window=900_000)
        plugin.overrides.apply_overrides(config)
        _write_override(temp_home / "config.yaml", "gpt-6-sol-900k", 111_111)

        payload = plugin.overrides.revert_overrides(config, purge_all=True)
        assert payload["ok"], payload
        written = yaml.safe_load((temp_home / "config.yaml").read_text() or "{}")
        assert "model_overrides" not in written


# -- tool + report surfaces ---------------------------------------------

@requires_hermes
class TestToolSurface:
    def test_unknown_action_returns_json_error(self, plugin):
        payload = json.loads(plugin.tool.codex_truly({"action": "nope"}))
        assert payload["ok"] is False

    def test_handler_never_raises(self, plugin, fake_catalog, temp_home):
        for args in ({}, {"action": None}, {"action": "apply", "target_context_window": "x"}):
            assert isinstance(plugin.tool.codex_truly(args), str)

    def test_status_lists_the_managed_models(self, plugin, fake_catalog, temp_home):
        status = plugin.report.collect(plugin.CodexTrulyConfig())
        assert status["signed_in"] is True
        assert any(row["model"] == "gpt-6.1-sol-900k" for row in status["models"])
        summary = plugin.report.summary(status)
        assert "900,000" in summary or "target" in summary

    def test_summary_reports_a_not_signed_in_profile(self, plugin):
        summary = plugin.report.summary({"signed_in": False, "auth_error": "nope"})
        assert "not signed in" in summary


# -- end to end ----------------------------------------------------------

@pytest.mark.skipif(not HERMES_SRC.is_dir(), reason="Hermes source not on this host")
class TestEndToEnd:
    """Proves the override changes what Hermes actually resolves."""

    def _authorize(self, temp_home: Path) -> bool:
        if not CODEX_AUTH.is_file():
            return False
        target = temp_home / "auth.json"
        target.write_text(CODEX_AUTH.read_text())
        target.chmod(0o600)
        return True

    def test_override_beats_the_catalog_clamp(self, plugin, temp_home):
        if not self._authorize(temp_home):
            pytest.skip(f"no Codex credential at {CODEX_AUTH} to exercise the live route")
        model = "gpt-6.1-sol-900k"
        before = _resolved(model)
        if not before:
            pytest.skip("Codex credential is not usable in this environment")
        assert before < 918_000, f"precondition: expected a clamped window, got {before}"
        # Guard against a hollow pass: the credentialless fallback also resolves
        # below the target, so equality here would mean no live catalog was read.
        assert before != _resolved_offline(model), (
            "the live catalog was not consulted — this run proves nothing")

        _write_override(temp_home / "config.yaml", model, 918_000)
        assert _resolved(model) == 918_000, "model_overrides did not win over the catalog"

        (temp_home / "config.yaml").write_text("")
        assert _resolved(model) == before, "removing the override did not restore the window"

    def test_apply_then_revert_round_trips_the_real_chain(self, plugin, temp_home, fake_catalog):
        if not self._authorize(temp_home):
            pytest.skip(f"no Codex credential at {CODEX_AUTH}")
        model = "gpt-6.1-sol-900k"
        before = _resolved(model)
        if not before:
            pytest.skip("Codex credential is not usable in this environment")
        assert before != _resolved_offline(model), (
            "the live catalog was not consulted — this run proves nothing")

        config = plugin.CodexTrulyConfig(target_context_window=918_000)
        assert plugin.overrides.apply_overrides(config)["ok"]
        assert _resolved(model) == 918_000, "plugin-written override did not take effect"

        assert plugin.overrides.revert_overrides(config)["ok"]
        assert _resolved(model) == before, "revert did not restore the original window"
