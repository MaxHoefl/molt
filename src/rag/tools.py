"""The retriever as an agent tool.

The corpus is exposed as a search rather than as more prompt text, and the tool
returns whole clauses with their license and section attached. That shape is chosen
so a citation is a quotation: the agent cannot cite "LGPL-2.1 section 6" without
having been handed the passage labelled that way, which is the difference between a
grounded answer and a fluent one.
"""

from langchain_core.tools import StructuredTool

from src.config.rag_config import load_rag_config
from src.rag.retrieval import Retriever

NOTHING_FOUND = (
    "No clause text is indexed for that query. Say so rather than paraphrasing a "
    "license you were not shown."
)


def render(passages) -> str:
    return "\n\n".join(passage.render() for passage in passages) or NOTHING_FOUND


def build_license_search_tool(retriever: Retriever, top_k: int | None = None) -> StructuredTool:
    k = top_k if top_k is not None else load_rag_config().top_k

    def search_license_text(query: str) -> str:
        """Search the indexed SPDX license clause texts and return the closest passages.

        Use it to quote the obligation a verdict rests on, in the license's own words.
        Ask for the obligation, not the license name — "must the user be able to relink
        a modified library?" finds the clause; "LGPL" finds several and tells you less.
        """
        return render(retriever.search(query, k))

    return StructuredTool.from_function(
        func=search_license_text,
        name="search_license_text",
        description=search_license_text.__doc__,
    )
