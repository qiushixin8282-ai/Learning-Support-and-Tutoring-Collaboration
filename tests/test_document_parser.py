"""上传文件校验与文本解析回归测试。"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from app.services.document_parser import DocumentParseError, parse_and_store


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class InMemoryUpload:
    """解析器所需的最小异步上传对象，避免测试依赖 Web 服务器线程池。"""

    def __init__(self, name: str, data: bytes, content_type: str):
        self.filename = name
        self.content_type = content_type
        self._data = data

    async def read(self, _: int = -1) -> bytes:
        return self._data


def upload(name: str, data: bytes, content_type: str) -> InMemoryUpload:
    return InMemoryUpload(name, data, content_type)


@unittest.skipIf(sys.platform == "win32", "当前受限 Windows 环境无法创建 asyncio socketpair；CI 在 Linux 执行")
class DocumentParserTests(unittest.TestCase):
    def test_text_file_is_stored_and_decoded(self):
        with tempfile.TemporaryDirectory() as directory:
            parsed = asyncio.run(parse_and_store(
                upload("学习说明.txt", "这是一份学习支持说明。".encode("utf-8"), "text/plain"), Path(directory)
            ))
            self.assertEqual(parsed.source_type, "纯文本")
            self.assertIn("学习支持", parsed.content)
            self.assertTrue((Path(directory) / parsed.stored_name).is_file())

    def test_rejects_fake_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DocumentParseError, "有效的 PDF"):
                asyncio.run(parse_and_store(upload("fake.pdf", b"not a pdf", "application/pdf"), Path(directory)))

    def test_rejects_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DocumentParseError, "仅支持"):
                asyncio.run(parse_and_store(upload("payload.exe", b"hello", "application/octet-stream"), Path(directory)))


if __name__ == "__main__":
    unittest.main()
