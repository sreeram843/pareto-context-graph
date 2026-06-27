"""Tests for centralized taxonomy helpers."""

from pareto_context_graph.taxonomy import (
    classify_query_intent,
    file_class,
    is_concept_query,
    is_noise_path,
    looks_like_symbol,
    query_is_test_focused,
)


def test_file_class_source_and_test():
    assert file_class("app/models/user.py") == "source"
    assert file_class("spec/models/user_spec.rb") == "test"


def test_is_noise_path_deprioritizes_tests_and_docs():
    assert is_noise_path("tests/test_auth.py")
    assert is_noise_path("docs/guide.md")
    assert not is_noise_path("src/auth.py")


def test_classify_query_intent_endpoint():
    assert classify_query_intent("API route controller handler") == "endpoint"


def test_classify_query_intent_openapi_beats_route_tie():
    query = "OpenAPI schema generation and route documentation utilities"
    assert classify_query_intent(query) == "openapi"


def test_looks_like_symbol_camelcase():
    assert looks_like_symbol("OAuth2PasswordBearer")


def test_is_concept_query_long_question():
    assert is_concept_query("how does middleware handle authentication tokens")


def test_query_is_test_focused():
    assert query_is_test_focused("pytest fixture mock")
