import pytest

from src.config.rag_config import RagBackend, RagConfig
from src.rag.corpus import CLAUSES, clause, clauses_for
from src.rag.retrieval import LexicalRetriever, NullRetriever, build_retriever
from src.rag.tools import NOTHING_FOUND, build_license_search_tool


@pytest.fixture
def retriever():
    return LexicalRetriever()


# --- the corpus --------------------------------------------------------------


def test_every_passage_can_be_cited():
    assert all(passage.license and passage.section and passage.text for passage in CLAUSES)


def test_passage_ids_are_unique():
    ids = [passage.id for passage in CLAUSES]

    assert len(set(ids)) == len(ids)


def test_a_passage_renders_with_its_attribution():
    rendered = clause("MIT", "grant", "Permission is hereby granted.").render()

    assert rendered.startswith("[MIT — grant]")


def test_collapses_the_whitespace_of_an_indented_clause():
    assert clause("X", "s", "one\n        two").text == "one two"


def test_finds_the_clauses_of_a_license_by_its_spdx_spelling():
    assert {p.id for p in clauses_for("GPL-3.0-only")} == {p.id for p in clauses_for("GPL-3.0")}


def test_a_license_with_no_indexed_text_has_no_clauses():
    assert clauses_for("Beerware") == ()


# --- lexical retrieval -------------------------------------------------------


def test_finds_the_clause_that_answers_the_question(retriever):
    found = retriever.search("must users be able to relink a modified library", k=1)

    assert found[0].license == "LGPL-2.1"


def test_finds_the_network_clause_for_a_hosted_service(retriever):
    found = retriever.search("interacting with users remotely through a computer network", k=1)

    assert found[0].license == "AGPL-3.0"


def test_returns_at_most_the_number_of_passages_asked_for(retriever):
    assert len(retriever.search("license", k=2)) == 2


def test_returns_nothing_for_a_query_that_matches_nothing(retriever):
    assert retriever.search("kubernetes ingress controller") == []


def test_ranks_the_best_match_first(retriever):
    found = retriever.search("patent litigation terminates the license")

    assert found[0].section.startswith("section 3")


def test_scores_a_rare_term_above_a_common_one():
    """Every clause says "license"; only one says "relink"."""
    corpus = (
        clause("A", "one", "This license permits use of the software."),
        clause("B", "two", "This license requires that the user relink the library."),
        clause("C", "three", "This license imposes a notice condition."),
    )

    assert LexicalRetriever(corpus).search("license relink", k=1)[0].license == "B"


# --- the seam ----------------------------------------------------------------


def test_retrieval_can_be_turned_off_entirely():
    assert isinstance(build_retriever(RagConfig(backend=RagBackend.OFF)), NullRetriever)


def test_a_disabled_retriever_finds_nothing():
    assert NullRetriever().search("anything") == []


def test_the_lexical_backend_is_selectable():
    assert isinstance(build_retriever(RagConfig(backend=RagBackend.LEXICAL)), LexicalRetriever)


def test_falls_back_rather_than_failing_when_the_vector_store_will_not_start(monkeypatch):
    """A broken index should cost accuracy, not availability."""
    import src.rag.retrieval as retrieval

    monkeypatch.setattr(
        retrieval, "ChromaRetriever", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no disk"))
    )

    assert isinstance(build_retriever(RagConfig(backend=RagBackend.CHROMA)), LexicalRetriever)


# --- the agent tool ----------------------------------------------------------


def test_the_tool_returns_quotable_passages(retriever):
    tool = build_license_search_tool(retriever, top_k=1)

    answer = tool.invoke({"query": "relink a modified version of the library"})

    assert answer.startswith("[LGPL-2.1 —")


def test_the_tool_says_when_nothing_is_indexed(retriever):
    tool = build_license_search_tool(retriever, top_k=3)

    assert tool.invoke({"query": "kubernetes ingress controller"}) == NOTHING_FOUND


def test_the_tool_returns_several_passages_when_asked_for_several(retriever):
    tool = build_license_search_tool(retriever, top_k=3)

    assert tool.invoke({"query": "copyleft conveying source"}).count("[") >= 2


def test_the_tool_names_itself_the_way_the_prompt_does(retriever):
    assert build_license_search_tool(retriever).name == "search_license_text"


class StubRetriever(LexicalRetriever):
    def __init__(self):
        super().__init__((clause("MIT", "grant", "Permission is hereby granted."),))


def test_the_tool_asks_the_retriever_for_the_configured_number_of_passages():
    seen = {}

    class Counting(StubRetriever):
        def search(self, query, k=3):
            seen["k"] = k
            return []

    build_license_search_tool(Counting(), top_k=7).invoke({"query": "x"})

    assert seen["k"] == 7
