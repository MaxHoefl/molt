"""Why an agent run came back without the structured object a tool asked for.

Every tool here ends the same way: invoke an agent, pull `structured_response` out
of the result, and give up if it is missing. The give-up path used to report
"agent returned no structured verdict", which reads like a parsing bug and sends
whoever hits it into the schema. It usually is not one.

A model that declines a request does not raise. The call returns normally, with an
empty message carrying the provider's reason for stopping — `stop_reason:
"refusal"` on Anthropic models, with a category naming the classifier that fired.
Writing that category into the error is the difference between an afternoon spent
dumping raw message metadata and a one-line answer, which is a trade this module
exists to make once rather than five times.
"""

from typing import Any

GENERIC = "agent returned no {what}"


def no_structure_reason(result: dict[str, Any], what: str) -> str:
    """The clearest available explanation for a missing structured response.

    Args:
        result: The agent invocation result.
        what: What the tool wanted, e.g. "structured verdict".

    Returns:
        A one-line reason naming the refusal and its category where the provider
        reported one, and the generic message otherwise.
    """
    metadata = _last_response_metadata(result)
    if metadata.get("stop_reason") != "refusal":
        return GENERIC.format(what=what)
    category = (metadata.get("stop_details") or {}).get("category")
    refused = "the model refused the request"
    return f"{refused} ({category})" if category else refused


def _last_response_metadata(result: dict[str, Any]) -> dict[str, Any]:
    messages = result.get("messages") or []
    if not messages:
        return {}
    return getattr(messages[-1], "response_metadata", None) or {}
