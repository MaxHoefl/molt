"""Test-wide defaults that keep the suite offline and deterministic.

Retrieval defaults to Chroma in production, which downloads an embedding model on
first use. Unit tests must not depend on that, so the lexical retriever — same
corpus, same interface, no model — is the default here unless a test asks otherwise.
"""

import os

os.environ.setdefault("MOLT_SERVER_RAG_BACKEND", "lexical")
