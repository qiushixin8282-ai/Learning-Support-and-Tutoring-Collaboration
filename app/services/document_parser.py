"""知识库上传文件的安全校验、落盘与文本解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from pypdf import PdfReader


MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_EXTRACTED_CHARS = 2_000_000
ALLOWED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}


class DocumentParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedDocument:
    original_name: str
    stored_name: str
    mime_type: str
    source_type: str
    content: str
    size_bytes: int
    page_count: int
    char_count: int


def _safe_display_name(name: str | None) -> str:
    raw = Path(name or "未命名文件").name
    return re.sub(r"[\x00-\x1f]", "", raw)[:180] or "未命名文件"


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParseError("文本编码无法识别，请使用 UTF-8 编码后重试")


async def parse_and_store(upload: UploadFile, upload_dir: Path) -> ParsedDocument:
    original_name = _safe_display_name(upload.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise DocumentParseError("仅支持 PDF、Markdown 和 TXT 文件")
    mime_type = (upload.content_type or "application/octet-stream").lower()
    if mime_type not in ALLOWED_MIME_TYPES[suffix]:
        raise DocumentParseError("文件类型与扩展名不匹配")

    data = await upload.read(MAX_FILE_SIZE + 1)
    if not data:
        raise DocumentParseError("文件内容为空")
    if len(data) > MAX_FILE_SIZE:
        raise DocumentParseError("文件不能超过 10 MB")
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise DocumentParseError("文件不是有效的 PDF 文档")

    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{suffix}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(data)
    try:
        if suffix == ".pdf":
            reader = PdfReader(stored_path)
            if reader.is_encrypted:
                raise DocumentParseError("暂不支持加密 PDF，请解除密码后重新上传")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise DocumentParseError(f"PDF 页数不能超过 {MAX_PDF_PAGES} 页")
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    pages.append(f"## 第 {index} 页\n\n{text}")
                if sum(len(value) for value in pages) > MAX_EXTRACTED_CHARS:
                    raise DocumentParseError("PDF 提取文本过长，请拆分文件后上传")
            content = "\n\n".join(pages).strip()
            if not content:
                raise DocumentParseError("PDF 未提取到文本，可能是扫描件；当前版本暂不含 OCR")
            page_count = len(reader.pages)
            source_type = "PDF"
        else:
            content = _decode_text(data).strip()
            if not content:
                raise DocumentParseError("文本文件没有可用内容")
            page_count = 0
            source_type = "Markdown" if suffix in {".md", ".markdown"} else "纯文本"
    except Exception as exc:
        stored_path.unlink(missing_ok=True)
        if isinstance(exc, DocumentParseError):
            raise
        raise DocumentParseError(f"文件解析失败：{exc}") from exc

    return ParsedDocument(
        original_name=original_name, stored_name=stored_name, mime_type=mime_type,
        source_type=source_type, content=content, size_bytes=len(data),
        page_count=page_count, char_count=len(content),
    )
