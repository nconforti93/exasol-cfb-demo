"""Virtual schema properties: defaults, validation, unknown names and merging."""

import pytest

from vs.cfbd_vs.properties import (
    KNOWN_PROPERTIES,
    Properties,
    PropertyError,
    merge_properties,
    parse_properties,
)


def test_defaults_apply_when_properties_are_unset() -> None:
    props = parse_properties({})

    assert props == Properties()
    assert props.connection_name == "CFBD_API"
    assert props.fetch_mode == "live"
    assert props.replay_path == "/buckets/bfsdefault/default/cfbd/replay"
    assert props.max_calls_per_query == 10
    assert props.max_parallel_fetches == 6
    assert props.read_through is True
    assert props.api_features == frozenset()
    assert props.doh_url == "https://1.1.1.1/dns-query"
    assert (props.infra_schema, props.meta_schema, props.core_schema) == (
        "CFB_INFRA",
        "CFB_META",
        "CFB_CORE",
    )


def test_missing_mapping_means_defaults() -> None:
    assert parse_properties(None) == Properties()


def test_known_properties_are_exactly_the_spec_table() -> None:
    assert set(KNOWN_PROPERTIES) == {
        "CONNECTION_NAME",
        "FETCH_MODE",
        "REPLAY_PATH",
        "MAX_CALLS_PER_QUERY",
        "MAX_PARALLEL_FETCHES",
        "READ_THROUGH",
        "API_FEATURES",
        "DOH_URL",
        "INFRA_SCHEMA",
        "META_SCHEMA",
        "CORE_SCHEMA",
    }


def test_every_property_parses_to_its_typed_value() -> None:
    props = parse_properties(
        {
            "CONNECTION_NAME": "CFBD_API_MOCK",
            "FETCH_MODE": "replay",
            "REPLAY_PATH": "/buckets/bfsdefault/default/test/replay",
            "MAX_CALLS_PER_QUERY": "1000",
            "MAX_PARALLEL_FETCHES": "16",
            "READ_THROUGH": "FALSE",
            "API_FEATURES": "weather, scoreboard",
            "DOH_URL": "off",
            "INFRA_SCHEMA": "TEST_CFB_INFRA",
            "META_SCHEMA": "TEST_CFB_META",
            "CORE_SCHEMA": "TEST_CFB_CORE",
        }
    )

    assert props == Properties(
        connection_name="CFBD_API_MOCK",
        fetch_mode="replay",
        replay_path="/buckets/bfsdefault/default/test/replay",
        max_calls_per_query=1000,
        max_parallel_fetches=16,
        read_through=False,
        api_features=frozenset({"weather", "scoreboard"}),
        doh_url="off",
        infra_schema="TEST_CFB_INFRA",
        meta_schema="TEST_CFB_META",
        core_schema="TEST_CFB_CORE",
    )


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("FETCH_MODE", "LIVE", "live"),
        ("FETCH_MODE", "Replay", "replay"),
        ("READ_THROUGH", "true", True),
        ("READ_THROUGH", "False", False),
        ("DOH_URL", "OFF", "off"),
        ("DOH_URL", "https://9.9.9.9/dns-query", "https://9.9.9.9/dns-query"),
        ("DOH_URL", "https://[2606:4700:4700::1111]/dns-query", None),
        ("MAX_CALLS_PER_QUERY", "1", 1),
        ("MAX_PARALLEL_FETCHES", "1", 1),
        ("API_FEATURES", "", frozenset()),
        ("API_FEATURES", "graphQl,adjustedMetrics", frozenset({"graphQl", "adjustedMetrics"})),
    ],
)
def test_keyword_values_are_case_insensitive_and_normalized(
    name: str, value: str, expected: object
) -> None:
    props = parse_properties({name: value})
    field = {
        "FETCH_MODE": props.fetch_mode,
        "READ_THROUGH": props.read_through,
        "DOH_URL": props.doh_url,
        "MAX_CALLS_PER_QUERY": props.max_calls_per_query,
        "MAX_PARALLEL_FETCHES": props.max_parallel_fetches,
        "API_FEATURES": props.api_features,
    }[name]
    assert field == (value if expected is None else expected)


@pytest.mark.parametrize(
    ("name", "value", "allowed"),
    [
        ("MAX_CALLS_PER_QUERY", "0", "an integer from 1 to 1000"),
        ("MAX_CALLS_PER_QUERY", "ten", "an integer from 1 to 1000"),
        ("MAX_CALLS_PER_QUERY", "1001", "an integer from 1 to 1000"),
        ("MAX_CALLS_PER_QUERY", " 10", "an integer from 1 to 1000"),
        ("MAX_CALLS_PER_QUERY", "+5", "an integer from 1 to 1000"),
        ("MAX_CALLS_PER_QUERY", "5.0", "an integer from 1 to 1000"),
        ("MAX_PARALLEL_FETCHES", "0", "an integer from 1 to 16"),
        ("MAX_PARALLEL_FETCHES", "17", "an integer from 1 to 16"),
        ("FETCH_MODE", "record", "live, replay"),
        ("READ_THROUGH", "maybe", "TRUE, FALSE"),
        ("API_FEATURES", "weather,teleport", "adjustedMetrics, weather, scoreboard"),
        ("API_FEATURES", "Weather", "adjustedMetrics, weather, scoreboard"),
        ("DOH_URL", "http://1.1.1.1/dns-query", "an https URL whose host is an IP literal"),
        ("DOH_URL", "https://cloudflare-dns.com/dns-query", "an https URL whose host is an IP"),
        ("DOH_URL", "https://[::1", "an https URL whose host is an IP literal"),
        ("DOH_URL", "https://1.1.1.1/" + "x" * 1100, "at most 1024 characters"),
        ("CONNECTION_NAME", "cfbd_api", "upper-case Exasol identifier"),
        ("CONNECTION_NAME", "CFBD API", "upper-case Exasol identifier"),
        ("CONNECTION_NAME", "1CFBD", "upper-case Exasol identifier"),
        ("CONNECTION_NAME", "C" * 129, "upper-case Exasol identifier"),
        ("INFRA_SCHEMA", 'CFB_INFRA"; DROP', "upper-case Exasol identifier"),
        ("META_SCHEMA", "cfb_meta", "upper-case Exasol identifier"),
        ("CORE_SCHEMA", "CFB-CORE", "upper-case Exasol identifier"),
        ("REPLAY_PATH", "cfbd/replay", "/buckets/<service>/<bucket>"),
        ("REPLAY_PATH", "/tmp/replay", "/buckets/<service>/<bucket>"),
        ("REPLAY_PATH", "/buckets/bfsdefault", "/buckets/<service>/<bucket>"),
        ("REPLAY_PATH", "/buckets/bfsdefault/default/../x", "/buckets/<service>/<bucket>"),
        ("REPLAY_PATH", "/buckets/bfsdefault/default/a b", "/buckets/<service>/<bucket>"),
        ("REPLAY_PATH", "/buckets/bfsdefault/default//x", "/buckets/<service>/<bucket>"),
        ("REPLAY_PATH", "/buckets/bfsdefault/default/it's", "/buckets/<service>/<bucket>"),
        ("REPLAY_PATH", "/buckets/b/d/" + "x" * 1020, "at most 1024 characters"),
    ],
)
def test_invalid_values_name_the_property_and_its_allowed_values(
    name: str, value: str, allowed: str
) -> None:
    with pytest.raises(PropertyError) as caught:
        parse_properties({name: value})

    message = str(caught.value)
    assert message.startswith(f"CFBD_VS: property {name} must be")
    assert allowed in message


def test_unknown_property_is_named_and_the_known_ones_are_listed() -> None:
    with pytest.raises(PropertyError) as caught:
        parse_properties({"MAX_CALL": "5"})

    message = str(caught.value)
    assert "unknown property MAX_CALL" in message
    for known in KNOWN_PROPERTIES:
        assert known in message


def test_every_problem_is_reported_on_its_own_line() -> None:
    with pytest.raises(PropertyError) as caught:
        parse_properties({"FETCH_MODE": "record", "MAX_CALLS_PER_QUERY": "0", "FOO": "1"})

    lines = str(caught.value).splitlines()
    assert len(lines) == 3
    assert all(line.startswith("CFBD_VS: ") for line in lines)
    assert len(caught.value.problems) == 3


def test_long_values_are_truncated_in_messages() -> None:
    with pytest.raises(PropertyError) as caught:
        parse_properties({"FETCH_MODE": "x" * 5000})

    assert len(str(caught.value)) < 300


@pytest.mark.parametrize("value", ["a\x00b", "\ud800"])
def test_values_that_cannot_travel_as_sql_text_are_refused(value: str) -> None:
    with pytest.raises(PropertyError, match="CONNECTION_NAME"):
        parse_properties({"CONNECTION_NAME": value})


def test_non_text_values_are_refused() -> None:
    with pytest.raises(PropertyError, match="MAX_CALLS_PER_QUERY"):
        parse_properties({"MAX_CALLS_PER_QUERY": 10})


@pytest.mark.parametrize("empty", [None, ""])
def test_null_or_empty_values_mean_the_default(empty: str | None) -> None:
    assert parse_properties({"MAX_CALLS_PER_QUERY": empty, "FETCH_MODE": empty}) == Properties()


def test_merge_applies_changes_and_deletes_null_or_empty_values() -> None:
    current = {"FETCH_MODE": "replay", "MAX_CALLS_PER_QUERY": "12", "READ_THROUGH": "FALSE"}
    changes = {
        "MAX_CALLS_PER_QUERY": "20",
        "READ_THROUGH": None,
        "FETCH_MODE": "",
        "DOH_URL": "off",
    }

    assert merge_properties(current, changes) == {"MAX_CALLS_PER_QUERY": "20", "DOH_URL": "off"}


def test_merge_does_not_mutate_its_inputs() -> None:
    current = {"FETCH_MODE": "replay"}
    changes = {"FETCH_MODE": None}

    merge_properties(current, changes)

    assert current == {"FETCH_MODE": "replay"}
    assert changes == {"FETCH_MODE": None}


def test_merge_tolerates_missing_mappings() -> None:
    assert merge_properties(None, None) == {}
