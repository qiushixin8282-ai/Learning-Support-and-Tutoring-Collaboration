"""持久化知识切片、混合检索与 OpenAI 兼容 RAG 回答。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

from app.services.database import connection


CHUNK_SIZE = 700
CHUNK_OVERLAP = 100
LOCAL_DIMENSION = 384
TOP_K = 5
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    item_id: int
    title: str
    source_name: str
    chunk_no: int
    content: str
    score: float


def _file_config() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def save_deepseek_config(api_key: str, model: str = "deepseek-v4-flash") -> None:
    """将密钥仅保存到已被 git 忽略的本地 .env 文件。"""
    current = _file_config()
    current.update({"LLM_PROVIDER": "deepseek", "LLM_API_KEY": api_key.strip(),
                    "LLM_BASE_URL": "https://api.deepseek.com",
                    "LLM_MODEL": model.strip() or "deepseek-v4-flash"})
    ENV_PATH.write_text("\n".join(f"{key}={value}" for key, value in current.items()) + "\n", encoding="utf-8")


def model_config() -> dict[str, str | bool]:
    file_values = _file_config()
    get = lambda key, default="": os.getenv(key) or file_values.get(key, default)
    api_key = get("LLM_API_KEY") or get("OPENAI_API_KEY")
    embedding_key = get("EMBEDDING_API_KEY")
    return {
        "configured": bool(api_key),
        "api_key": api_key or "",
        "provider": get("LLM_PROVIDER", "openai"),
        "base_url": get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "chat_model": get("LLM_MODEL", "gpt-5.5"),
        "embedding_api_key": embedding_key,
        "embedding_base_url": get("EMBEDDING_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "embedding_model": get("EMBEDDING_MODEL", "text-embedding-3-small") if embedding_key else "local-hash-384",
    }


def split_text(text: str) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text).strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= CHUNK_SIZE:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
        while len(paragraph) > CHUNK_SIZE:
            chunks.append(paragraph[:CHUNK_SIZE])
            paragraph = paragraph[CHUNK_SIZE - CHUNK_OVERLAP:]
        buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return chunks


def tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
    return ["".join(chinese[i:i + 2]) for i in range(len(chinese) - 1)] + re.findall(r"[a-z0-9_-]{2,}", text.lower())


def local_embedding(text: str) -> list[float]:
    vector = np.zeros(LOCAL_DIMENSION, dtype=np.float32)
    for token in tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        vector[int.from_bytes(digest, "big") % LOCAL_DIMENSION] += 1.0 if digest[0] % 2 == 0 else -1.0
    norm = np.linalg.norm(vector)
    return (vector / norm if norm else vector).tolist()


def _post_json(url: str, body: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"模型服务返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接模型服务：{exc.reason}") from exc


def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    config = model_config()
    if not config["embedding_api_key"]:
        return [local_embedding(text) for text in texts], "local-hash-384"
    payload = _post_json(
        f"{config['embedding_base_url']}/embeddings",
        {"model": config["embedding_model"], "input": texts, "encoding_format": "float"},
        str(config["embedding_api_key"]),
    )
    ordered = sorted(payload["data"], key=lambda item: item["index"])
    return [item["embedding"] for item in ordered], str(config["embedding_model"])


def index_knowledge_item(item_id: int) -> int:
    with connection() as conn:
        item = conn.execute("SELECT id, content FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise ValueError("资料不存在")
    chunks = split_text(item["content"])
    if not chunks:
        raise ValueError("资料没有可索引内容")
    vectors, model = embed_texts(chunks)
    with connection() as conn:
        conn.execute("DELETE FROM knowledge_chunks WHERE knowledge_item_id = ?", (item_id,))
        conn.executemany(
            "INSERT INTO knowledge_chunks(knowledge_item_id, chunk_no, content, vector_json, embedding_model) VALUES (?, ?, ?, ?, ?)",
            [(item_id, number, content, json.dumps(vector), model) for number, (content, vector) in enumerate(zip(chunks, vectors), 1)],
        )
    return len(chunks)


def _visible_clause(role: str) -> tuple[str, list[str]]:
    if role == "系统管理员":
        return "", []
    levels = {
        "学生": ["全员可见"],
        "助教": ["全员可见", "助教及以上"],
        "导师": ["全员可见", "助教及以上", "导师及以上"],
        "课题组负责人": ["全员可见", "助教及以上", "导师及以上"],
    }
    values = levels.get(role, ["全员可见"])
    return f" AND k.visibility IN ({','.join('?' for _ in values)})", values


def retrieve(question: str, role: str, top_k: int = TOP_K) -> list[SearchHit]:
    clause, values = _visible_clause(role)
    with connection() as conn:
        rows = conn.execute(
            """SELECT c.id, c.knowledge_item_id, c.chunk_no, c.content, c.vector_json,
                      k.title, COALESCE(k.original_name, k.title) AS source_name
               FROM knowledge_chunks c JOIN knowledge_items k ON k.id = c.knowledge_item_id
               WHERE k.status = '已发布'""" + clause,
            values,
        ).fetchall()
    if not rows:
        return []
    query_vectors, _ = embed_texts([question])
    query = np.asarray(query_vectors[0], dtype=np.float32)
    vectors = np.asarray([json.loads(row["vector_json"]) for row in rows], dtype=np.float32)
    if vectors.shape[1] != len(query):
        return []
    faiss.normalize_L2(vectors)
    query_matrix = query.reshape(1, -1)
    faiss.normalize_L2(query_matrix)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    semantic_scores, semantic_indexes = index.search(query_matrix, len(rows))
    semantic_by_index = {int(i): float(score) for score, i in zip(semantic_scores[0], semantic_indexes[0])}
    query_tokens = set(tokens(question))
    lexical = [len(query_tokens.intersection(tokens(row["content"]))) / max(len(query_tokens), 1) for row in rows]
    ranked = sorted(range(len(rows)), key=lambda i: 0.72 * semantic_by_index.get(i, 0) + 0.28 * lexical[i], reverse=True)[:top_k]
    return [SearchHit(
        chunk_id=rows[i]["id"], item_id=rows[i]["knowledge_item_id"], title=rows[i]["title"],
        source_name=rows[i]["source_name"], chunk_no=rows[i]["chunk_no"], content=rows[i]["content"],
        score=round(0.72 * semantic_by_index.get(i, 0) + 0.28 * lexical[i], 4),
    ) for i in ranked if semantic_by_index.get(i, 0) > 0 or lexical[i] > 0]


def generate_answer(question: str, hits: list[SearchHit], system_prompt: str) -> tuple[str, str, str]:
    config = model_config()
    if not hits:
        return "资料中未找到与问题相关的内容。", "retrieval-only", "none"
    if not config["configured"]:
        excerpts = "\n\n".join(f"【{i}】{hit.content[:260]}" for i, hit in enumerate(hits, 1))
        return f"模型服务尚未配置，以下是检索到的相关资料片段：\n\n{excerpts}", "retrieval-only", "local"
    context = "\n\n".join(
        f"[来源 {i}：{hit.source_name}，切片 {hit.chunk_no}]\n{hit.content}" for i, hit in enumerate(hits, 1)
    )
    user_input = f"资料上下文：\n{context}\n\n用户问题：{question}\n\n请只根据资料回答，并用【来源编号】标注依据。"
    if config["provider"] == "deepseek" or "deepseek.com" in str(config["base_url"]):
        payload = _post_json(
            f"{config['base_url']}/chat/completions",
            {"model": config["chat_model"], "messages": [
                {"role": "system", "content": system_prompt}, {"role": "user", "content": user_input}], "stream": False},
            str(config["api_key"]),
        )
        answer = payload.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    else:
        payload = _post_json(
            f"{config['base_url']}/responses",
            {"model": config["chat_model"], "instructions": system_prompt, "input": user_input},
            str(config["api_key"]),
        )
        answer = payload.get("output_text")
        if not answer:
            texts = [part.get("text", "") for item in payload.get("output", []) for part in item.get("content", []) if part.get("type") == "output_text"]
            answer = "".join(texts).strip()
    return answer or "模型没有返回文本。", "rag", str(config["chat_model"])
