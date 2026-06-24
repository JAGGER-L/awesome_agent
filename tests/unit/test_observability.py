from awesome_agent.observability.setup import configure_observability


def test_observability_configures_service_resource() -> None:
    provider = configure_observability(
        service_name="awesome-agent-test",
        console_exporter=False,
    )

    assert provider.resource.attributes["service.name"] == "awesome-agent-test"
