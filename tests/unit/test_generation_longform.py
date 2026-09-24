import pytest

from novel_writer.generation.content import digest
from novel_writer.generation.novel import role_for, slots_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.segmentation import segment_body
from tests.unit.test_genre_generation import spec


def test_lossless_global_partition_and_valid_last_chapter():
    body = "\n\n".join(["甲" * 700, "乙" * 1100, "丙" * 1300, "丁" * 850])
    result = segment_body(body, 2000, 3, "test")
    assert result["tail"] is None
    assert len(result["segments"]) == 2
    assert "".join(body[s["start"] : s["end"]] for s in result["segments"]) == body
    assert all(digest(body[s["start"] : s["end"]]) == s["body_sha256"] for s in result["segments"])


def test_unsplittable_paragraph_retains_tail():
    result = segment_body("甲" * 10000, 2000, 3, "test")
    assert result["segments"] == [] and result["tail"] == {"start": 0, "end": 10000}


def test_finite_slots_and_legacy_validation():
    value = spec().model_dump(mode="json")
    long = GenerationSpec.model_validate(
        {
            **value,
            "workflow": "novel-run-v1",
            "stage_mode": "longform-v1",
            "unit_limit": 3,
            "chapter_count": 3,
        }
    )
    assert len(slots_for(long)) == 12
    assert role_for("chief:2") == "chief" and role_for("memory:6") == "memory"
    with pytest.raises(ValueError):
        GenerationSpec.model_validate({**value, "chapter_count": 2})


@pytest.mark.asyncio
async def test_macro_age_follows_current_parent_branch_and_reactivation():
    from types import SimpleNamespace
    from uuid import uuid4

    from novel_writer.generation.stage_history import phase_age

    project, phase = uuid4(), str(uuid4())
    a, b, c, abandoned = [uuid4() for _ in range(4)]
    chapters = [str(uuid4()) for _ in range(3)]
    revisions = [str(uuid4()) for _ in range(3)]

    def state(active):
        return {
            "narrative_phases": [
                {
                    "id": phase,
                    "name": "重逢",
                    "goal": "旧计划",
                    "expected_change": "彼此了解",
                    "status": "active" if active else "completed",
                }
            ]
        }

    records = {
        a: SimpleNamespace(
            id=a,
            project_id=project,
            parent_id=None,
            number=1,
            state=state(True),
            chapter_revisions=dict(zip(chapters[:1], revisions[:1], strict=False)),
        ),
        b: SimpleNamespace(
            id=b,
            project_id=project,
            parent_id=a,
            number=2,
            state=state(False),
            chapter_revisions=dict(zip(chapters[:2], revisions[:2], strict=False)),
        ),
        c: SimpleNamespace(
            id=c,
            project_id=project,
            parent_id=b,
            number=4,
            state=state(True),
            chapter_revisions=dict(zip(chapters, revisions, strict=False)),
        ),
        abandoned: SimpleNamespace(
            id=abandoned,
            project_id=project,
            parent_id=a,
            number=3,
            state=state(True),
            chapter_revisions={},
        ),
    }
    fetched = []

    class Session:
        async def get(self, model, identifier):
            fetched.append(identifier)
            return records[identifier]

        async def scalar(self, query):
            return 42

    class Service:
        session = Session()

        async def _version(self, identifier):
            return records[identifier]

    result = await phase_age(Service(), project, c, state(True))
    assert result["formal_chapters_since_activation"] == 1
    assert result["formal_versions_since_activation"] == 1
    assert result["activated_version_id"] == str(c)
    assert result["goal_reference"]["binding_instruction"] is False
    assert abandoned not in fetched and a not in fetched
