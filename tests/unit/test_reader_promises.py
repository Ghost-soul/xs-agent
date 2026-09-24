from uuid import uuid4

from novel_writer.domain.models import (
    ReaderPromise,
    ReaderPromiseEvidence,
    ReaderPromiseUpdate,
)
from novel_writer.services.reader_promises import (
    assess_promise_lifecycle,
    compute_promise_memory_strength,
)


def test_reader_promise_lifecycle_uses_last_update_and_current_chapter() -> None:
    chapter_id = uuid4()
    evidence = ReaderPromiseEvidence(
        chapter_id=chapter_id,
        chapter_ordinal=2,
        start=0,
        end=2,
        quote="线索",
    )
    promise = ReaderPromise(
        kind="mystery",
        summary="失踪者留下的钥匙",
        established_chapter=2,
        last_updated_chapter=4,
        history=(
            ReaderPromiseUpdate(
                action="established",
                chapter_ordinal=2,
                note="钥匙出现",
                evidence=(evidence,),
            ),
            ReaderPromiseUpdate(
                action="advanced",
                chapter_ordinal=4,
                note="钥匙与旧门对应",
                evidence=(
                    evidence.model_copy(update={"chapter_ordinal": 4}),
                ),
            ),
        ),
    )

    assert compute_promise_memory_strength(promise, current_chapter=8) == 0.397
    assessment = assess_promise_lifecycle([promise], current_chapter=8)[0]
    assert assessment["silent_chapters"] == 4
    assert assessment["chapters_held"] == 6
    assert assessment["action_needed"] == "fulfill_or_advance"
