"""持久化切片与混合检索回归测试。"""

import os
import tempfile
import unittest
from pathlib import Path

from app.services.database import connection, initialize_database
from app.services.rag_service import index_knowledge_item, retrieve, split_text


class RagServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_database = os.environ.get("DATABASE_PATH")
        self.old_key = os.environ.pop("LLM_API_KEY", None)
        os.environ["DATABASE_PATH"] = str(Path(self.temp.name) / "rag.sqlite3")
        initialize_database()

    def tearDown(self):
        if self.old_database is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.old_database
        if self.old_key is not None:
            os.environ["LLM_API_KEY"] = self.old_key
        self.temp.cleanup()

    def test_split_text_keeps_multiple_chunks(self):
        chunks = split_text("第一段。" * 180 + "\n\n" + "第二段介绍空间智能。" * 80)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunks))

    def test_index_and_retrieve_visible_content(self):
        with connection() as conn:
            cursor = conn.execute(
                "INSERT INTO knowledge_items(title, source_type, visibility, content, status) VALUES (?, ?, ?, ?, '已发布')",
                ("Atlas 知识", "知识模板", "全员可见", "Atlas 是用于空间智能的多模态世界模型，可以进行三维场景重建。"),
            )
            item_id = cursor.lastrowid
        self.assertEqual(index_knowledge_item(item_id), 1)
        hits = retrieve("Atlas 如何进行空间重建？", "学生")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "Atlas 知识")

    def test_role_filter_hides_admin_only_content(self):
        with connection() as conn:
            cursor = conn.execute(
                "INSERT INTO knowledge_items(title, source_type, visibility, content, status) VALUES (?, ?, ?, ?, '已发布')",
                ("内部资料", "纯文本", "仅管理员", "内部预算与治理规则。"),
            )
            item_id = cursor.lastrowid
        index_knowledge_item(item_id)
        self.assertEqual(retrieve("内部预算", "学生"), [])
        self.assertTrue(retrieve("内部预算", "系统管理员"))


if __name__ == "__main__":
    unittest.main()
