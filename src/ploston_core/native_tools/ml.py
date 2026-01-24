"""Core machine learning and AI functions."""

from typing import Any


async def generate_text_embedding(
    text: str, model: str = "all-minilm:latest", ollama_host: str = "http://localhost:11434"
) -> dict[str, Any]:
    """Generate text embeddings using Ollama language models.

    Args:
        text: The text to generate embeddings for
        model: Ollama model to use (default: all-minilm:latest)
        ollama_host: Ollama service URL

    Returns:
        Dictionary containing:
        - success: Whether embedding generation was successful
        - embedding: The embedding vector (list of floats)
        - dimensions: Number of dimensions in the embedding
        - model: Model used for embedding
        - text_length: Length of input text
        - error: Error message if generation failed
    """
    try:
        if not text:
            return {"success": False, "error": "Text is required"}

        # Try to use Ollama for embeddings
        try:
            import httpx
        except ImportError:
            return {
                "success": False,
                "error": "httpx library not available. Install with: pip install httpx",
            }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{ollama_host}/api/embeddings", json={"model": model, "prompt": text}
                )

                if response.status_code == 200:
                    embedding_data = response.json()
                    embedding = embedding_data.get("embedding", [])

                    return {
                        "success": True,
                        "embedding": embedding,
                        "dimensions": len(embedding),
                        "model": model,
                        "text_length": len(text),
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Ollama API error: {response.status_code} - {response.text}",
                    }

        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"Cannot connect to Ollama at {ollama_host}. Make sure Ollama is running.",
            }
        except Exception as e:
            return {"success": False, "error": f"Embedding generation failed: {str(e)}"}

    except Exception as e:
        return {"success": False, "error": f"Text embedding failed: {str(e)}"}


async def calculate_text_similarity(
    text1: str,
    text2: str,
    method: str = "cosine",
    model: str | None = None,
    ollama_host: str = "http://localhost:11434",
) -> dict[str, Any]:
    """Calculate similarity between two texts.

    Args:
        text1: First text to compare
        text2: Second text to compare
        method: Similarity method - "cosine", "jaccard", or "levenshtein"
        model: Ollama model for embeddings (only for cosine similarity)
        ollama_host: Ollama service URL (only for cosine similarity)

    Returns:
        Dictionary containing:
        - success: Whether calculation was successful
        - similarity: Similarity score (0.0 to 1.0)
        - method: Method used for calculation
        - error: Error message if calculation failed
    """
    try:
        if not text1 or not text2:
            return {"success": False, "error": "Both text1 and text2 are required"}

        method = method.lower()

        if method == "cosine":
            # Use embeddings for cosine similarity
            if not model:
                model = "all-minilm:latest"

            # Generate embeddings
            emb1_result = await generate_text_embedding(text1, model, ollama_host)
            if not emb1_result["success"]:
                return emb1_result

            emb2_result = await generate_text_embedding(text2, model, ollama_host)
            if not emb2_result["success"]:
                return emb2_result

            # Calculate cosine similarity
            emb1 = emb1_result["embedding"]
            emb2 = emb2_result["embedding"]

            dot_product = sum(a * b for a, b in zip(emb1, emb2))
            magnitude1 = sum(a * a for a in emb1) ** 0.5
            magnitude2 = sum(b * b for b in emb2) ** 0.5

            similarity = (
                dot_product / (magnitude1 * magnitude2) if magnitude1 and magnitude2 else 0.0
            )

            return {"success": True, "similarity": similarity, "method": "cosine", "model": model}

        elif method == "jaccard":
            # Jaccard similarity based on word sets
            words1 = set(text1.lower().split())
            words2 = set(text2.lower().split())

            intersection = len(words1 & words2)
            union = len(words1 | words2)

            similarity = intersection / union if union > 0 else 0.0

            return {"success": True, "similarity": similarity, "method": "jaccard"}

        elif method == "levenshtein":
            # Levenshtein distance normalized to 0-1 similarity
            distance = _levenshtein_distance(text1, text2)
            max_len = max(len(text1), len(text2))
            similarity = 1.0 - (distance / max_len) if max_len > 0 else 1.0

            return {
                "success": True,
                "similarity": similarity,
                "method": "levenshtein",
                "distance": distance,
            }

        else:
            return {
                "success": False,
                "error": f"Unknown similarity method: {method}. Use 'cosine', 'jaccard', or 'levenshtein'",
            }

    except Exception as e:
        return {"success": False, "error": f"Similarity calculation failed: {str(e)}"}


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


async def classify_text(
    text: str,
    categories: list[str],
    model: str = "all-minilm:latest",
    ollama_host: str = "http://localhost:11434",
) -> dict[str, Any]:
    """Classify text into predefined categories using embeddings.

    Args:
        text: The text to classify
        categories: List of category names
        model: Ollama model to use for embeddings
        ollama_host: Ollama service URL

    Returns:
        Dictionary containing:
        - success: Whether classification was successful
        - category: The predicted category
        - confidence: Confidence score (0.0 to 1.0)
        - scores: Dictionary of all category scores
        - error: Error message if classification failed
    """
    try:
        if not text:
            return {"success": False, "error": "Text is required"}

        if not categories or len(categories) == 0:
            return {"success": False, "error": "At least one category is required"}

        # Generate embedding for input text
        text_emb_result = await generate_text_embedding(text, model, ollama_host)
        if not text_emb_result["success"]:
            return text_emb_result

        text_emb = text_emb_result["embedding"]

        # Calculate similarity with each category
        scores = {}
        for category in categories:
            cat_emb_result = await generate_text_embedding(category, model, ollama_host)
            if not cat_emb_result["success"]:
                continue

            cat_emb = cat_emb_result["embedding"]

            # Cosine similarity
            dot_product = sum(a * b for a, b in zip(text_emb, cat_emb))
            magnitude1 = sum(a * a for a in text_emb) ** 0.5
            magnitude2 = sum(b * b for b in cat_emb) ** 0.5

            similarity = (
                dot_product / (magnitude1 * magnitude2) if magnitude1 and magnitude2 else 0.0
            )
            scores[category] = similarity

        if not scores:
            return {"success": False, "error": "Failed to calculate similarities for any category"}

        # Find best category
        best_category = max(scores, key=scores.get)
        confidence = scores[best_category]

        return {
            "success": True,
            "category": best_category,
            "confidence": confidence,
            "scores": scores,
            "model": model,
        }

    except Exception as e:
        return {"success": False, "error": f"Text classification failed: {str(e)}"}


async def analyze_sentiment(text: str, method: str = "lexicon") -> dict[str, Any]:
    """Analyze sentiment of text.

    Args:
        text: The text to analyze
        method: Analysis method - "lexicon" (simple word-based)

    Returns:
        Dictionary containing:
        - success: Whether analysis was successful
        - sentiment: Sentiment label ("positive", "negative", "neutral")
        - score: Sentiment score (-1.0 to 1.0)
        - confidence: Confidence in the prediction
        - error: Error message if analysis failed
    """
    try:
        if not text:
            return {"success": False, "error": "Text is required"}

        # Simple lexicon-based sentiment analysis
        positive_words = {
            "good",
            "great",
            "excellent",
            "amazing",
            "wonderful",
            "fantastic",
            "love",
            "best",
            "perfect",
            "happy",
            "joy",
            "beautiful",
            "awesome",
        }

        negative_words = {
            "bad",
            "terrible",
            "awful",
            "horrible",
            "worst",
            "hate",
            "poor",
            "sad",
            "angry",
            "disappointing",
            "useless",
            "fail",
            "wrong",
        }

        words = text.lower().split()

        positive_count = sum(1 for word in words if word in positive_words)
        negative_count = sum(1 for word in words if word in negative_words)

        total_sentiment_words = positive_count + negative_count

        if total_sentiment_words == 0:
            return {
                "success": True,
                "sentiment": "neutral",
                "score": 0.0,
                "confidence": 0.5,
                "method": method,
            }

        score = (positive_count - negative_count) / len(words)
        confidence = total_sentiment_words / len(words)

        if score > 0.05:
            sentiment = "positive"
        elif score < -0.05:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        return {
            "success": True,
            "sentiment": sentiment,
            "score": score,
            "confidence": min(confidence, 1.0),
            "positive_words": positive_count,
            "negative_words": negative_count,
            "method": method,
        }

    except Exception as e:
        return {"success": False, "error": f"Sentiment analysis failed: {str(e)}"}
