

from novel_writer.db.models import (
    AgentCallRecord,
    WritingChunkCallRecord,
)


def test_legacy_writer_model_name_is_compatible() -> None:
    assert WritingChunkCallRecord is AgentCallRecord
    assert AgentCallRecord.__tablename__ == "writing_chunk_calls"
