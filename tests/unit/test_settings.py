from awesome_agent.settings import Settings


def test_settings_use_confirmed_concurrency_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.max_teammates == 6
    assert settings.max_subagents_per_teammate == 3
    assert settings.max_model_concurrency == 8
    assert settings.max_tool_concurrency == 12
    assert settings.max_sandbox_concurrency == 6
    assert not settings.builtin_memory_enabled
    assert not settings.mem0_enabled
    assert settings.leader_model == "deepseek-v4-pro"
    assert settings.teammate_model == "deepseek-v4-flash"
    assert settings.verifier_model == "deepseek-v4-flash"
    assert settings.subagent_model == "deepseek-v4-flash"
    assert settings.deepseek_thinking_enabled
