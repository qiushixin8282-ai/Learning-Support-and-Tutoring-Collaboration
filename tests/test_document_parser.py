"""上传文件校验与文本解析回归测试。"""

import io
import tempfile
import unittest
from pathlib import Path

from starlette.datastructures import Headers, UploadFile

from app.services.document_parser import DocumentParseError, parse_and_store


def upload(name: str, data: bytes, content_type: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name, headers=Headers({"content-type": content_type}))


class DocumentParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_file_is_stored_and_decoded(self):
        with tempfile.TemporaryDirectory() as directory:
            parsed = await parse_and_store(
                upload("学习说明.txt", "这是一份学习支持说明。".encode("utf-8"), "text/plain"), Path(directory)
            )
            self.assertEqual(parsed.source_type, "纯文本")
            self.assertIn("学习支持", parsed.content)
            self.assertTrue((Path(directory) / parsed.stored_name).is_file())

    async def test_rejects_fake_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DocumentParseError, "有效的 PDF"):
                await parse_and_store(upload("fake.pdf", b"not a pdf", "application/pdf"), Path(directory))

    async def test_rejects_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DocumentParseError, "仅支持"):
                await parse_and_store(upload("payload.exe", b"hello", "application/octet-stream"), Path(directory))


if __name__ == "__main__":
    unittest.main()
