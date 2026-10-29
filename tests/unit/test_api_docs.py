"""The committed serving dictionary matches the linted serving SQL files."""

from analyses import docs


def test_committed_api_dictionary_is_current() -> None:
    assert docs.DICTIONARY_PATH.read_text(encoding="utf-8") == docs.render_dictionary()
