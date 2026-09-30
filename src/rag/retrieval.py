"""Two retrievers over one corpus, and the seam that makes them interchangeable.

`ChromaRetriever` is the real one: the clause corpus embedded into a persistent
local collection, matched semantically, so "does linking oblige me to let users
swap the library?" finds LGPL-2.1 section 6 without sharing a single content word
with it.

`LexicalRetriever` scores the same passages by term overlap. It exists for two
reasons that are both about honesty rather than convenience. Tests need a retriever
whose answers do not depend on a model download, and a server whose vector store
fails to start should degrade to a worse retriever rather than to a confidently
unsourced answer.

Both satisfy the same three-line interface, so nothing above this module knows or
cares which one it got.
"""

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from functools import cache
from pathlib import Path

from src.config.log_config import logger
from src.config.rag_config import RagBackend, RagConfig, load_rag_config
from src.rag.corpus import CLAUSES, Passage

WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and any are as at be by for from in is it its may must not of on or that the "
    "this to under with you your".split()
)


def terms(text: str) -> list[str]:
    return [word for word in WORD.findall(text.lower()) if word not in STOPWORDS]


class Retriever(ABC):
    @abstractmethod
    def search(self, query: str, k: int = 3) -> list[Passage]:
        raise NotImplementedError()


class NullRetriever(Retriever):
    """Retrieval turned off, without forcing callers to special-case it."""

    def search(self, query: str, k: int = 3) -> list[Passage]:
        return []


class LexicalRetriever(Retriever):
    """TF-IDF over the clause corpus: no model, no network, no variance."""

    def __init__(self, passages: tuple[Passage, ...] = CLAUSES) -> None:
        self._passages = passages
        self._tokens = [Counter(terms(f"{p.license} {p.section} {p.text}")) for p in passages]
        appearances = Counter(word for counts in self._tokens for word in counts)
        self._idf = {
            word: math.log(len(passages) / (1 + count)) + 1
            for word, count in appearances.items()
        }

    def search(self, query: str, k: int = 3) -> list[Passage]:
        wanted = terms(query)
        scored = [
            (self._score(wanted, counts), passage)
            for counts, passage in zip(self._tokens, self._passages, strict=True)
        ]
        hits = sorted(
            (pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0]
        )
        return [passage for _, passage in hits[:k]]

    def _score(self, wanted: list[str], counts: Counter) -> float:
        length = sum(counts.values()) or 1
        return sum(counts[word] / length * self._idf.get(word, 0.0) for word in wanted)


class ChromaRetriever(Retriever):
    """The clause corpus in a persistent local Chroma collection.

    Indexing is idempotent: passage ids are `<license>#<section>`, so re-running the
    server upserts the same rows rather than growing a duplicate corpus, and a clause
    whose text is edited is corrected on the next start.
    """

    def __init__(self, config: RagConfig, passages: tuple[Passage, ...] = CLAUSES) -> None:
        import chromadb

        directory = Path(config.persist_directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        self._collection = chromadb.PersistentClient(path=str(directory)).get_or_create_collection(
            config.collection
        )
        self._passages = {passage.id: passage for passage in passages}
        self._index(passages)

    def _index(self, passages: tuple[Passage, ...]) -> None:
        self._collection.upsert(
            ids=[passage.id for passage in passages],
            documents=[passage.text for passage in passages],
            metadatas=[
                {"license": passage.license, "section": passage.section} for passage in passages
            ],
        )

    def search(self, query: str, k: int = 3) -> list[Passage]:
        found = self._collection.query(query_texts=[query], n_results=k)
        return [self._passages[id] for id in (found.get("ids") or [[]])[0] if id in self._passages]


def build_retriever(config: RagConfig | None = None) -> Retriever:
    """Whatever the configuration asks for, falling back rather than failing."""
    config = config or load_rag_config()
    if config.backend == RagBackend.OFF:
        return NullRetriever()
    if config.backend == RagBackend.LEXICAL:
        return LexicalRetriever()
    try:
        return ChromaRetriever(config)
    except Exception as e:
        logger.warning(f"Chroma is unavailable ({e}); falling back to lexical retrieval")
        return LexicalRetriever()


@cache
def license_retriever() -> Retriever:
    """One retriever per process: building it embeds the corpus, which is not free."""
    return build_retriever()
