import pytest

from vs.host.namespace import REAL_CONNECTION, Namespace, NamespaceError


def test_real_namespace_has_the_plain_names() -> None:
    ns = Namespace("")
    assert (ns.infra_schema, ns.meta_schema, ns.core_schema, ns.vs_name) == (
        "CFB_INFRA",
        "CFB_META",
        "CFB_CORE",
        "CFBD_VS",
    )
    assert ns.owner_user == "SYS"
    assert ns.is_real
    assert ns.reader_role == "CFB_READER"
    assert ns.default_connection == REAL_CONNECTION == "CFBD_API"
    assert ns.fetch_script == "CFB_INFRA.CFBD_FETCH"


def test_test_namespace_prefixes_schemas_and_users() -> None:
    ns = Namespace("TEST_")
    assert (ns.infra_schema, ns.meta_schema, ns.core_schema, ns.vs_name) == (
        "TEST_CFB_INFRA",
        "TEST_CFB_META",
        "TEST_CFB_CORE",
        "TEST_CFBD_VS",
    )
    assert ns.owner_user == "CFB_TEST_OWNER"
    assert not ns.is_real
    assert ns.reader_role == "CFB_TEST_READER"
    assert ns.default_connection == "CFBD_API_MOCK"
    assert ns.fetch_script == "TEST_CFB_INFRA.CFBD_FETCH"


@pytest.mark.parametrize("prefix", ["test_", "TEST", "TEST-", "_", "1_", "A B_", "T;_"])
def test_invalid_prefixes_are_rejected(prefix: str) -> None:
    with pytest.raises(NamespaceError, match="prefix"):
        Namespace(prefix)


@pytest.mark.parametrize("requested", ["CFBD_API", "cfbd_api", " CFBD_API ", "  "])
def test_a_prefixed_namespace_refuses_the_real_connection_or_an_empty_name(
    requested: str,
) -> None:
    with pytest.raises(NamespaceError, match="real connection"):
        Namespace("TEST_").resolve_connection(requested)


def test_connection_resolution_defaults_follow_the_namespace() -> None:
    assert Namespace("").resolve_connection(None) == "CFBD_API"
    assert Namespace("").resolve_connection("CFBD_API") == "CFBD_API"
    assert Namespace("TEST_").resolve_connection(None) == "CFBD_API_MOCK"
    assert Namespace("TEST_").resolve_connection("MY_MOCK") == "MY_MOCK"


def test_check_connection_also_refuses_an_empty_name_for_a_prefixed_namespace() -> None:
    with pytest.raises(NamespaceError, match="real connection"):
        Namespace("TEST_").check_connection("")
    assert Namespace("").check_connection("") == ""


def test_stage_and_restore_schemas_follow_the_prefix() -> None:
    assert Namespace("").stage_schema == "CFB_STAGE"
    assert Namespace("").restore_schema == "CFB_RESTORE"
    assert Namespace("TEST_").stage_schema == "TEST_CFB_STAGE"
    assert Namespace("TEST_").restore_schema == "TEST_CFB_RESTORE"


def test_model_schema_follows_the_prefix() -> None:
    assert Namespace("").model_schema == "CFB_MODEL"
    assert Namespace("TEST_").model_schema == "TEST_CFB_MODEL"
    assert Namespace("PERF_").model_schema == "PERF_CFB_MODEL"
