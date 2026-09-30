# -*- coding: utf-8 -*-
"""v2.14.0 RAG 优化核心行为回归测试（标准库 unittest）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from models.models import Base, Note
from services import ai_service, embedding_service, rag_service, rerank_service, vector_service


class ChunkTests(unittest.TestCase):
    def test_build_note_chunks_respects_limit_and_overlap(self):
        content = "第一段" * 180 + "\n\n" + "第二段" * 180
        chunks = vector_service.build_note_chunks(7, "测试标题", "技术", content)
        body_chunks = [item for item in chunks if item["metadata"]["kind"] == "body"]

        self.assertGreater(len(body_chunks), 1)
        self.assertTrue(all(len(item["text"]) <= vector_service.CHUNK_SIZE for item in chunks))
        self.assertTrue(body_chunks[0]["text"].startswith("标题：测试标题"))
        self.assertIn("分类：技术", body_chunks[0]["text"])
        overlap = body_chunks[0]["text"][-vector_service.CHUNK_OVERLAP:]
        self.assertTrue(body_chunks[1]["text"].endswith(overlap) is False)
        self.assertIn(overlap[:20], body_chunks[1]["text"])


class EmbeddingTests(unittest.TestCase):
    def test_embed_texts_splits_remote_requests_at_16(self):
        calls: list[list[str]] = []

        def fake_remote(texts):
            calls.append(list(texts))
            return [[0.1] * embedding_service.REMOTE_DIM for _ in texts]

        with patch.object(embedding_service, "_embed_remote_batch", side_effect=fake_remote):
            vectors = embedding_service.embed_texts(["文本"] * 33, mode="remote")

        self.assertEqual([len(call) for call in calls], [16, 16, 1])
        self.assertEqual(len(vectors), 33)
        self.assertEqual(len(vectors[0]), embedding_service.REMOTE_DIM)


class RerankTests(unittest.TestCase):
    def test_rerank_falls_back_to_original_order_on_error(self):
        documents = [{"id": 1, "text": "甲"}, {"id": 2, "text": "乙"}]
        with patch("services.rerank_service.httpx.post", side_effect=TimeoutError("超时")):
            result = rerank_service.rerank_documents("问题", documents, top_n=2)
        self.assertEqual([item["id"] for item in result], [1, 2])


class ConfigTests(unittest.TestCase):
    def test_ai_config_defaults_and_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai_config.json"
            with patch.object(config, "AI_CONFIG_PATH", path):
                self.assertFalse(ai_service.get_config()["use_local_embedding"])
                ai_service.save_config(
                    base_url="https://example.test/v1",
                    model="chat-model",
                    api_key="",
                    embedding_base_url="https://example.test/v1",
                    embedding_model="embedding-model",
                    use_local_embedding=False,
                    rerank_enabled=True,
                    rerank_model="rerank-model",
                )
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["embedding_model"], "embedding-model")
                self.assertTrue(saved["rerank_enabled"])


class RagTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.note = Note(
            title="Python 入门",
            content="这是一篇关于 Python 的笔记，包含基础语法。",
            tags='["Python", "编程"]',
            category="技术",
            source="手动输入",
            note_type="普通",
        )
        self.session.add(self.note)
        self.session.commit()
        self.session.refresh(self.note)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_retrieval_merges_vector_and_keyword_and_keeps_category(self):
        with patch.object(
            vector_service,
            "search_notes",
            return_value=[{"id": self.note.id, "score": 0.91}],
        ):
            items = rag_service.retrieve_candidates(self.session, "Python", limit=5)

        self.assertEqual(items[0]["id"], self.note.id)
        self.assertEqual(items[0]["category"], "技术")
        self.assertEqual(items[0]["score"], 0.91)

    def test_keyword_search_treats_percent_as_literal(self):
        other = Note(
            title="普通文章",
            content="没有特殊符号",
            tags="[]",
            category="默认",
            source="手动输入",
            note_type="普通",
        )
        percent = Note(
            title="进度 100%",
            content="完成度很高",
            tags="[]",
            category="默认",
            source="手动输入",
            note_type="普通",
        )
        self.session.add_all([other, percent])
        self.session.commit()

        hits = rag_service._keyword_search(self.session, "%", limit=10)
        ids = {item["id"] for item in hits}
        self.assertIn(percent.id, ids)
        self.assertNotIn(other.id, ids)


if __name__ == "__main__":
    unittest.main()