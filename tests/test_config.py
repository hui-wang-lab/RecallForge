from __future__ import annotations

import warnings

from recallforge.config import Settings, get_config


class TestSettings:
    def test_defaults(self):
        s = Settings(
            openai_api_key="test-key",
            database_url="postgresql://localhost:5432/recallforge",
        )
        assert s.database_url == "postgresql://localhost:5432/recallforge"
        assert s.embedding_model == "text-embedding-v4@1024"
        assert s.embedding_dim == 1024
        assert s.embedding_provider == "dashscope"
        assert s.dashscope_api_key == ""
        assert s.dashscope_endpoint
        assert s.embedding_batch_size == 32
        assert s.embedding_max_retries == 3
        assert s.default_top_k == 50
        assert s.final_top_k == 8
        assert s.reranker_required is True
        assert s.min_rerank_score == 0.35
        assert s.min_vector_score == 0.6
        assert s.max_context_tokens == 24000
        assert s.child_max_tokens == 450
        assert s.child_min_tokens == 80
        assert s.parent_granularity == "chapter"
        assert s.log_level == "INFO"

    def test_empty_api_key_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(openai_api_key="")
            assert any("OPENAI_API_KEY" in str(warning.message) for warning in w)

    def test_api_key_present_no_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(openai_api_key="sk-test-key")
            assert not any("OPENAI_API_KEY" in str(warning.message) for warning in w)


class TestGetConfig:
    def test_returns_settings_instance(self):
        config = get_config()
        assert isinstance(config, Settings)

    def test_singleton(self):
        assert get_config() is get_config()

    def test_config_has_all_fields(self):
        config = get_config()
        for field in Settings.model_fields:
            assert hasattr(config, field)


class TestLogger:
    def test_get_logger(self):
        from recallforge.observability.logger import get_logger

        logger = get_logger("test_logger_basic")
        assert logger.name == "test_logger_basic"

    def test_logger_no_duplicate_handlers(self):
        from recallforge.observability.logger import get_logger

        logger1 = get_logger("test_no_dup_handlers")
        handler_count = len(logger1.handlers)
        logger2 = get_logger("test_no_dup_handlers")
        assert len(logger2.handlers) == handler_count

    def test_logger_no_duplicate_filters_on_handler(self):
        from recallforge.observability.logger import _extra_filter, get_logger

        logger = get_logger("test_no_dup_filters")
        handler = logger.handlers[0]
        filter_count = sum(1 for f in handler.filters if f is _extra_filter)
        # Call get_logger again — should not add another _extra_filter
        get_logger("test_no_dup_filters")
        filter_count_after = sum(1 for f in handler.filters if f is _extra_filter)
        assert filter_count == filter_count_after
