"""Specification tests for ploston_core.native_tools.ml.

These tests assert the *intended* contract of the ML native-tool client
(per each function's docstring), not whatever the code currently returns.
They mock only the external boundary (the Ollama HTTP API via httpx); the
math, lexicon, dispatch and error-envelope logic under test runs for real.

If a test fails, it is reporting a defect against the documented contract --
do NOT weaken the assertion to match the implementation.
"""

import math
from typing import Any

import pytest

from ploston_core.native_tools import ml

# ---------------------------------------------------------------------------
# httpx mocking helpers (external boundary only)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self) -> Any:
        return self._json


def _install_fake_async_client(monkeypatch, handler):
    """Patch httpx.AsyncClient so that `.post(...)` is routed to `handler`.

    `handler(url, json=...)` must return a _FakeResponse (or raise).
    """
    import httpx

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.init_kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            return handler(url, json)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    # Keep the real exception types so `except httpx.ConnectError` still works.
    return httpx


# ---------------------------------------------------------------------------
# generate_text_embedding
# ---------------------------------------------------------------------------


async def test_embedding_payload_and_endpoint(monkeypatch):
    captured = {}

    def handler(url, json):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, {"embedding": [0.1, 0.2, 0.3]})

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.generate_text_embedding(
        "hello world", model="my-model", ollama_host="http://ollama:11434"
    )

    # Correct endpoint shaping.
    assert captured["url"] == "http://ollama:11434/api/embeddings"
    # Correct payload shaping for the Ollama embeddings API.
    assert captured["json"] == {"model": "my-model", "prompt": "hello world"}

    assert result["success"] is True
    assert result["embedding"] == [0.1, 0.2, 0.3]
    assert result["dimensions"] == 3
    assert result["model"] == "my-model"
    assert result["text_length"] == len("hello world")


async def test_embedding_empty_text_is_rejected(monkeypatch):
    # Should short-circuit before any HTTP call.
    def handler(url, json):  # pragma: no cover - must not be called
        raise AssertionError("HTTP must not be called for empty text")

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.generate_text_embedding("")
    assert result["success"] is False
    assert "error" in result


async def test_embedding_backend_non_200_returns_error_envelope(monkeypatch):
    def handler(url, json):
        return _FakeResponse(500, text="boom")

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.generate_text_embedding("hi")
    assert result["success"] is False
    assert "500" in result["error"]


async def test_embedding_connect_error_returns_error_envelope(monkeypatch):
    import httpx

    def handler(url, json):
        raise httpx.ConnectError("nope")

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.generate_text_embedding("hi", ollama_host="http://down:11434")
    assert result["success"] is False
    assert "down:11434" in result["error"]


# ---------------------------------------------------------------------------
# calculate_text_similarity -- cosine
# ---------------------------------------------------------------------------


async def test_cosine_similarity_math(monkeypatch):
    # Hand-picked vectors with a known cosine.
    # v1 = [1, 2, 3], v2 = [4, 5, 6]
    # dot = 4 + 10 + 18 = 32
    # |v1| = sqrt(14), |v2| = sqrt(77)
    # cos = 32 / sqrt(14*77) = 32 / sqrt(1078)
    vectors = {"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]}

    def handler(url, json):
        prompt = json["prompt"]
        return _FakeResponse(200, {"embedding": vectors[prompt]})

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.calculate_text_similarity("a", "b", method="cosine")
    assert result["success"] is True
    assert result["method"] == "cosine"
    expected = 32 / math.sqrt(1078)
    assert result["similarity"] == pytest.approx(expected)


async def test_cosine_identical_vectors_is_one(monkeypatch):
    def handler(url, json):
        return _FakeResponse(200, {"embedding": [0.3, 0.4]})

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.calculate_text_similarity("x", "y", method="cosine")
    assert result["success"] is True
    assert result["similarity"] == pytest.approx(1.0)


async def test_cosine_zero_vector_yields_zero(monkeypatch):
    def handler(url, json):
        prompt = json["prompt"]
        return _FakeResponse(200, {"embedding": [0.0, 0.0] if prompt == "z" else [1.0, 1.0]})

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.calculate_text_similarity("z", "w", method="cosine")
    assert result["success"] is True
    assert result["similarity"] == 0.0


async def test_cosine_propagates_embedding_error(monkeypatch):
    def handler(url, json):
        return _FakeResponse(503, text="unavailable")

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.calculate_text_similarity("a", "b", method="cosine")
    assert result["success"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# calculate_text_similarity -- jaccard
# ---------------------------------------------------------------------------


async def test_jaccard_math():
    # words1 = {the, quick, brown, fox}; words2 = {the, lazy, fox}
    # intersection = {the, fox} = 2; union = {the,quick,brown,fox,lazy} = 5
    result = await ml.calculate_text_similarity(
        "the quick brown fox", "the lazy fox", method="jaccard"
    )
    assert result["success"] is True
    assert result["method"] == "jaccard"
    assert result["similarity"] == pytest.approx(2 / 5)


async def test_jaccard_identical_is_one():
    result = await ml.calculate_text_similarity("a b c", "c b a", method="jaccard")
    assert result["similarity"] == pytest.approx(1.0)


async def test_jaccard_disjoint_is_zero():
    result = await ml.calculate_text_similarity("a b", "c d", method="jaccard")
    assert result["similarity"] == 0.0


async def test_jaccard_case_insensitive():
    result = await ml.calculate_text_similarity("Hello World", "hello world", method="jaccard")
    assert result["similarity"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# calculate_text_similarity -- levenshtein
# ---------------------------------------------------------------------------


async def test_levenshtein_math():
    # "kitten" -> "sitting" distance is 3; max_len = 7; sim = 1 - 3/7
    result = await ml.calculate_text_similarity("kitten", "sitting", method="levenshtein")
    assert result["success"] is True
    assert result["method"] == "levenshtein"
    assert result["distance"] == 3
    assert result["similarity"] == pytest.approx(1 - 3 / 7)


async def test_levenshtein_identical_is_one():
    result = await ml.calculate_text_similarity("same", "same", method="levenshtein")
    assert result["distance"] == 0
    assert result["similarity"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# calculate_text_similarity -- dispatch / validation
# ---------------------------------------------------------------------------


async def test_unknown_method_returns_error():
    result = await ml.calculate_text_similarity("a", "b", method="bogus")
    assert result["success"] is False
    assert "bogus" in result["error"].lower() or "unknown" in result["error"].lower()


async def test_method_is_case_insensitive():
    # "COSINE" should dispatch to cosine, not be treated as unknown.
    # Use jaccard via uppercase to avoid needing an HTTP mock here.
    result = await ml.calculate_text_similarity("a b", "a b", method="JACCARD")
    assert result["success"] is True
    assert result["method"] == "jaccard"


async def test_similarity_empty_text_rejected():
    result = await ml.calculate_text_similarity("", "b", method="jaccard")
    assert result["success"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# classify_text
# ---------------------------------------------------------------------------


async def test_classify_picks_best_category(monkeypatch):
    # text embedding aligns perfectly with "sports", orthogonal-ish to "cooking".
    embeddings = {
        "I love playing football": [1.0, 0.0],
        "sports": [1.0, 0.0],
        "cooking": [0.0, 1.0],
    }

    def handler(url, json):
        return _FakeResponse(200, {"embedding": embeddings[json["prompt"]]})

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.classify_text("I love playing football", ["sports", "cooking"])
    assert result["success"] is True
    assert result["category"] == "sports"
    assert result["confidence"] == pytest.approx(1.0)
    assert result["scores"]["sports"] == pytest.approx(1.0)
    assert result["scores"]["cooking"] == pytest.approx(0.0)


async def test_classify_empty_categories_rejected(monkeypatch):
    def handler(url, json):  # pragma: no cover
        raise AssertionError("must not call backend with no categories")

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.classify_text("hi", [])
    assert result["success"] is False
    assert "error" in result


async def test_classify_empty_text_rejected():
    result = await ml.classify_text("", ["a"])
    assert result["success"] is False


async def test_classify_propagates_text_embedding_error(monkeypatch):
    def handler(url, json):
        return _FakeResponse(500, text="err")

    _install_fake_async_client(monkeypatch, handler)

    result = await ml.classify_text("hi", ["a", "b"])
    assert result["success"] is False


# ---------------------------------------------------------------------------
# analyze_sentiment
# ---------------------------------------------------------------------------


async def test_sentiment_positive():
    result = await ml.analyze_sentiment("this is good great excellent")
    assert result["success"] is True
    assert result["sentiment"] == "positive"
    assert result["score"] > 0
    # 3 positive of 5 words.
    assert result["score"] == pytest.approx(3 / 5)
    assert result["positive_words"] == 3
    assert result["negative_words"] == 0


async def test_sentiment_negative():
    result = await ml.analyze_sentiment("this is bad terrible awful")
    assert result["success"] is True
    assert result["sentiment"] == "negative"
    assert result["score"] == pytest.approx(-3 / 5)
    assert result["negative_words"] == 3


async def test_sentiment_neutral_no_lexicon_words():
    result = await ml.analyze_sentiment("the cat sat on the mat")
    assert result["success"] is True
    assert result["sentiment"] == "neutral"
    assert result["score"] == 0.0


async def test_sentiment_balanced_is_neutral():
    # equal positive and negative -> score 0 -> neutral
    result = await ml.analyze_sentiment("good bad")
    assert result["success"] is True
    assert result["score"] == 0.0
    assert result["sentiment"] == "neutral"


async def test_sentiment_score_in_range():
    result = await ml.analyze_sentiment("good great excellent amazing wonderful")
    assert -1.0 <= result["score"] <= 1.0
    assert 0.0 <= result["confidence"] <= 1.0


async def test_sentiment_empty_text_rejected():
    result = await ml.analyze_sentiment("")
    assert result["success"] is False
    assert "error" in result


async def test_sentiment_case_insensitive():
    result = await ml.analyze_sentiment("GOOD GREAT")
    assert result["sentiment"] == "positive"
    assert result["positive_words"] == 2
