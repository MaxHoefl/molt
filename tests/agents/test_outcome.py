"""A refusal must not be reported as a missing structured response.

The distinction matters because the two failures have nothing in common: one is a
schema or parsing problem in this codebase, the other is the provider declining the
request, and only one of them is worth reading the agent code over.
"""

from langchain_core.messages import AIMessage

from src.agents.outcome import no_structure_reason


def message(**metadata) -> AIMessage:
    return AIMessage(content="", response_metadata=metadata)


def test_names_the_refusal_and_the_classifier_that_fired():
    result = {
        "messages": [
            message(
                stop_reason="refusal",
                stop_details={"type": "refusal", "category": "reasoning_extraction"},
            )
        ]
    }

    assert no_structure_reason(result, "structured plan") == (
        "the model refused the request (reasoning_extraction)"
    )


def test_names_the_refusal_even_without_a_category():
    result = {"messages": [message(stop_reason="refusal")]}

    assert no_structure_reason(result, "structured plan") == "the model refused the request"


def test_falls_back_to_the_generic_reason_when_the_model_simply_stopped():
    result = {"messages": [message(stop_reason="end_turn")]}

    assert no_structure_reason(result, "structured verdict") == "agent returned no structured verdict"


def test_says_something_useful_when_the_provider_reported_nothing():
    # Providers other than Anthropic carry no stop_reason here, and a run can come
    # back with no messages at all. Neither may raise on the error path.
    assert no_structure_reason({"messages": [message()]}, "structured verdict") == (
        "agent returned no structured verdict"
    )
    assert no_structure_reason({}, "structured plan") == "agent returned no structured plan"
