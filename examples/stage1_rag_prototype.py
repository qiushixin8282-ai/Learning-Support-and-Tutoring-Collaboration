#!/usr/bin/env python3
"""阶段 1 最小原型：带角色权限过滤和来源标注的本地资料问答。

本脚本刻意不使用 Web、数据库或后台任务；每次运行均从本地 Markdown 重建索引，
用于验证“资料 → 检索 → AI 建议 → 审计留痕”这条最小链路。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import faiss
import numpy as np


# -------------------------- 原型运行参数 --------------------------
# 临时代码：一期仅用固定维度的哈希向量，不依赖额外 Embedding 服务。
# 后续阶段替换为 agents/embedding_provider.py，由可替换的模型供应商生成向量。
CHUNK_SIZE = 500
EMBEDDING_DIMENSION = 384
TOP_K = 3
ALLOWED_ROLES = {"学生", "助教", "导师"}

# 核心模块：切片与元数据。后续迁移到 services/knowledge_chunk_service.py，
# 并把可见角色改为从项目成员和资料 ACL 表中读取。
DEFAULT_CHUNK_VISIBILITY = (
    ("学生", "助教", "导师"),
    ("助教", "导师"),
    ("导师",),
)


@dataclass(frozen=True)
class Chunk:
    """一段可检索的资料及其访问控制元数据。"""

    source_file: str
    chunk_no: int
    text: str
    visible_roles: tuple[str, ...]


def chinese_tokens(text: str) -> list[str]:
    """以中文字符二元组和英文单词做轻量分词。

    临时代码：这是不引入 NLP 依赖的原型分词方案。生产环境应使用统一分词器，
    并让查询、BM25 和向量模型使用一致的预处理规则。
    """
    normalized = re.sub(r"\s+", "", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
    bigrams = ["".join(chinese[i : i + 2]) for i in range(len(chinese) - 1)]
    latin_words = re.findall(r"[a-z0-9_./-]{2,}", text.lower())
    return bigrams + latin_words


def split_markdown(source_path: Path) -> list[Chunk]:
    """读取 Markdown，并按最多 500 个字符切片。

    核心模块：切片与元数据。保留来源文件和切片编号，便于 AI 输出引用，
    也为后续成果版本追溯提供稳定的来源定位。
    """
    raw_text = source_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError(f"资料文件为空：{source_path}")

    # 先按段落拆开，尽量不在段落中间截断；超长段落再按字符切开。
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", raw_text) if part.strip()]
    pieces: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= CHUNK_SIZE:
            buffer = candidate
            continue
        if buffer:
            pieces.append(buffer)
            buffer = ""
        while len(paragraph) > CHUNK_SIZE:
            pieces.append(paragraph[:CHUNK_SIZE])
            paragraph = paragraph[CHUNK_SIZE:]
        buffer = paragraph
    if buffer:
        pieces.append(buffer)

    chunks: list[Chunk] = []
    for index, piece in enumerate(pieces, start=1):
        # 临时代码：用切片序号循环分配示例权限，专门用于验证权限过滤。
        # 生产环境必须从资料 ACL 或项目成员授权中读取，不能依赖编号规则。
        roles = DEFAULT_CHUNK_VISIBILITY[(index - 1) % len(DEFAULT_CHUNK_VISIBILITY)]
        chunks.append(Chunk(source_path.name, index, piece, roles))
    return chunks


def hash_embed(text: str) -> np.ndarray:
    """将文本转为归一化稠密向量，供 FAISS 内积检索使用。

    临时代码：哈希向量只用于验证索引、权限过滤和混合检索接口。
    生产环境将替换为 API Embedding，并对向量模型名称和版本写审计日志。
    """
    vector = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
    for token in chinese_tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest, "big") % EMBEDDING_DIMENSION
        sign = 1.0 if digest[0] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def build_faiss_index(chunks: list[Chunk]) -> tuple[faiss.IndexFlatIP, np.ndarray]:
    """构建 FAISS 内积索引。

    核心模块：向量索引构建。后续迁移到 services/knowledge_index_service.py，
    持久化到 pgvector 或专用向量数据库，不再在每次启动时重建。
    """
    vectors = np.vstack([hash_embed(chunk.text) for chunk in chunks]).astype(np.float32)
    index = faiss.IndexFlatIP(EMBEDDING_DIMENSION)
    index.add(vectors)
    return index, vectors


def bm25_scores(query: str, chunks: list[Chunk]) -> np.ndarray:
    """计算 BM25 分数，作为向量检索的关键词补充。

    核心模块：混合检索。为保持单脚本最小依赖，此处手写 BM25；
    后续可替换为 Elasticsearch、OpenSearch 或 PostgreSQL 全文检索。
    """
    query_tokens = chinese_tokens(query)
    corpus = [chinese_tokens(chunk.text) for chunk in chunks]
    document_count = len(corpus)
    average_length = sum(len(tokens) for tokens in corpus) / max(document_count, 1)
    document_frequency = Counter(token for tokens in corpus for token in set(tokens))
    scores = np.zeros(document_count, dtype=np.float32)
    k1, b = 1.5, 0.75
    for doc_index, tokens in enumerate(corpus):
        frequencies = Counter(tokens)
        for token in query_tokens:
            if token not in frequencies:
                continue
            idf = math.log(1 + (document_count - document_frequency[token] + 0.5) /
                           (document_frequency[token] + 0.5))
            term_frequency = frequencies[token]
            denominator = term_frequency + k1 * (1 - b + b * len(tokens) / max(average_length, 1))
            scores[doc_index] += idf * term_frequency * (k1 + 1) / denominator
    return scores


def normalize(scores: np.ndarray) -> np.ndarray:
    """将不同量纲的检索分数缩放到 0 至 1。"""
    if len(scores) == 0 or float(scores.max()) == float(scores.min()):
        return np.zeros_like(scores) if len(scores) else scores
    return (scores - scores.min()) / (scores.max() - scores.min())


def retrieve(question: str, role: str, chunks: list[Chunk], vectors: np.ndarray) -> list[Chunk]:
    """先按角色过滤，再执行 BM25 加向量混合检索。

    核心模块：权限过滤。权限必须发生在检索前，禁止先检索全部资料、
    再在回答阶段隐藏来源；否则敏感片段可能进入 LLM 上下文。
    """
    visible_indexes = [i for i, chunk in enumerate(chunks) if role in chunk.visible_roles]
    if not visible_indexes:
        return []

    # 关键安全控制：先依据角色取得可见切片，再只为这些向量建立临时 FAISS 索引。
    # 不得先检索全量敏感向量再做过滤，以免排序或上下文侧信道泄露受限资料。
    query_vector = hash_embed(question).reshape(1, -1).astype(np.float32)
    visible_vectors = vectors[visible_indexes]
    visible_index = faiss.IndexFlatIP(EMBEDDING_DIMENSION)
    visible_index.add(visible_vectors)
    vector_distances, local_indexes = visible_index.search(query_vector, len(visible_indexes))
    vector_scores = np.zeros(len(chunks), dtype=np.float32)
    for score, local_index in zip(vector_distances[0], local_indexes[0]):
        vector_scores[visible_indexes[int(local_index)]] = float(score)

    # BM25 也只在角色可见资料中计算，保证关键词检索遵守相同权限边界。
    visible_chunks = [chunks[i] for i in visible_indexes]
    visible_bm25 = bm25_scores(question, visible_chunks)
    keyword_scores = np.zeros(len(chunks), dtype=np.float32)
    for score, global_index in zip(visible_bm25, visible_indexes):
        keyword_scores[global_index] = float(score)
    hybrid_scores = 0.55 * normalize(vector_scores[visible_indexes]) + 0.45 * normalize(keyword_scores[visible_indexes])

    # 保守策略：没有任何词法交集即视为“资料中未找到”，避免原型产生无依据回答。
    question_tokens = set(chinese_tokens(question))
    eligible = [
        i for i in visible_indexes
        if question_tokens.intersection(chinese_tokens(chunks[i].text))
    ]
    if not eligible:
        return []
    score_by_global_index = dict(zip(visible_indexes, hybrid_scores))
    ranked = sorted(eligible, key=lambda item: float(score_by_global_index[item]), reverse=True)[:TOP_K]
    return [chunks[i] for i in ranked]


def call_llm(prompt: str) -> str:
    """调用 OpenAI 兼容的 LLM Chat Completions API。

    核心模块：LLM 调用封装。供应商、模型、密钥和 Base URL 仅从环境变量读取，
    便于后续迁移到 agents/provider.py；不得把密钥写入代码或审计日志。
    """
    provider = os.getenv("LLM_PROVIDER", "openai_compatible")
    model = os.getenv("LLM_MODEL")
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    if provider != "openai_compatible":
        raise ValueError("当前原型仅实现 openai_compatible 供应商适配器")
    if not model or not api_key:
        raise RuntimeError("请设置 LLM_MODEL 与 LLM_API_KEY 环境变量")

    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "你是学习支持协作实验室助手。只能依据给定资料回答；没有依据时回答资料中未找到。"},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM API 请求失败：{error.code} {details}") from error
    return payload["choices"][0]["message"]["content"].strip()


def build_prompt(question: str, chunks: Iterable[Chunk]) -> str:
    """将仅限当前角色可见的来源片段拼入提示词。"""
    context = "\n\n".join(
        f"[来源：{chunk.source_file}，切片 {chunk.chunk_no}]\n{chunk.text}"
        for chunk in chunks
    )
    return f"资料上下文：\n{context}\n\n问题：{question}\n\n请给出简洁回答，并且不得使用上下文之外的事实。"


def append_audit_log(log_path: Path, role: str, question: str, hits: list[Chunk], model_name: str) -> None:
    """追加一行 JSONL 审计日志。

    核心模块：审计日志。后续迁移到 audit_logs 表或异步审计服务；
    本原型只保留最小字段，且不记录 API Key 或完整模型输入。
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "question": question,
        "hit_chunks": [f"{chunk.source_file}#{chunk.chunk_no}" for chunk in hits],
        "model": model_name,
    }
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    """解析命令行参数，执行检索、建议生成、来源标注和审计。"""
    parser = argparse.ArgumentParser(description="阶段 1：权限过滤 RAG 最小原型")
    parser.add_argument("--document", type=Path, required=True, help="本地 Markdown 资料文件")
    parser.add_argument("--role", choices=sorted(ALLOWED_ROLES), required=True, help="模拟访问角色")
    parser.add_argument("--question", help="问题；不传时进入交互式输入")
    parser.add_argument("--audit-log", type=Path, default=Path("audit.jsonl"), help="JSONL 审计日志路径")
    args = parser.parse_args()

    question = args.question or input("请输入问题：").strip()
    if not question:
        raise ValueError("问题不能为空")

    chunks = split_markdown(args.document)
    _, vectors = build_faiss_index(chunks)
    hits = retrieve(question, args.role, chunks, vectors)
    model_name = os.getenv("LLM_MODEL", "not_called")

    if not hits:
        print("资料中未找到")
        append_audit_log(args.audit_log, args.role, question, [], model_name)
        return

    try:
        answer = call_llm(build_prompt(question, hits))
    except RuntimeError as error:
        # 原型安全策略：模型不可用时只报告错误，不改为无来源的本地生成答案。
        append_audit_log(args.audit_log, args.role, question, hits, model_name)
        print(f"模型调用失败：{error}", file=sys.stderr)
        raise SystemExit(1) from error

    references = "；".join(f"{chunk.source_file} + 切片 {chunk.chunk_no}" for chunk in hits)
    print(f"{answer}\n\n引用来源：{references}")
    append_audit_log(args.audit_log, args.role, question, hits, model_name)


if __name__ == "__main__":
    main()
