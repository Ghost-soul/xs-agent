from uuid import uuid4

from novel_writer.db.models import GenerationArtifactRecord as Artifact
from novel_writer.db.models import GenerationBatchRecord as Batch
from novel_writer.generation.content import digest
from novel_writer.generation.result_integrity import refusal_blocker
from tests.unit.test_output_reliability import call

REFUSAL = "**I must decline this request.**"


def fixture(action="write:1"):
    batch = Batch(id=uuid4(), state={})
    saved = call(REFUSAL, action)
    saved.status = "completed"
    candidate = Artifact(id=uuid4(), batch_id=batch.id, kind="candidate", payload={
        "body": REFUSAL, "source_call_id": str(saved.id),
    })
    units = Artifact(id=uuid4(), batch_id=batch.id, kind="units", payload={"items": [{
        "start": 0, "end": len(REFUSAL), "body_sha256": digest(REFUSAL),
        "source_call_id": str(saved.id),
    }]})
    batch.state = {"candidate_id": str(candidate.id), "units_id": str(units.id)}
    return batch, saved, candidate, units


def test_current_refusal_and_refusal_prefix_both_block_following_work():
    batch, saved, candidate, units = fixture()
    assert "write:1" in refusal_blocker(batch, [saved], [candidate, units])
    candidate.payload = {"body": REFUSAL + "\n\n后来的正文。", "source_call_id": str(uuid4())}
    assert refusal_blocker(batch, [saved], [candidate, units])
    assert saved.status == "completed"


def test_author_replacement_and_old_unselected_candidates_do_not_block():
    batch, saved, candidate, units = fixture()
    old_candidate = Artifact(id=uuid4(), batch_id=batch.id, kind="candidate",
                             payload=dict(candidate.payload))
    candidate.payload = {"body": "作者写好的有效正文。", "source": "author"}
    assert refusal_blocker(batch, [saved], [candidate, old_candidate, units]) is None


def test_trial_plan_and_current_note_are_guarded_without_schema_validation():
    batch, saved, candidate, units = fixture("plan")
    plan = Artifact(id=uuid4(), batch_id=batch.id, kind="plan", payload={"raw_response": REFUSAL})
    batch.state = {"plan_id": str(plan.id)}
    assert "plan" in refusal_blocker(batch, [saved], [plan])
    saved.action = "memory:1"
    candidate.payload = {"body": "正文。"}
    note = Artifact(id=uuid4(), batch_id=batch.id, kind="trial_note", payload={
        "text": REFUSAL, "source_call_id": str(saved.id),
    })
    units.payload = {"items": [{"start": 0, "end": 3, "body_sha256": digest("正文。"),
                               "note_id": str(note.id)}]}
    batch.state = {"candidate_id": str(candidate.id), "units_id": str(units.id)}
    assert "memory:1" in refusal_blocker(batch, [saved], [candidate, units, note])


def test_other_batch_artifacts_never_become_current_dependencies():
    batch, saved, candidate, units = fixture()
    candidate.batch_id = units.batch_id = uuid4()
    assert refusal_blocker(batch, [saved], [candidate, units]) is None
