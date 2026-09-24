from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark


def _reference_source() -> str:
    chapters: list[str] = []
    for index, number in enumerate("一二三四五六七八九十", 1):
        daily = [f"“今天还去吗？”他把第{index}只杯子推过去。“去，顺便买菜，记得带伞。”"] * 20
        conflict = ["“闭嘴！”她冷笑一声，“威胁一句就想让我退吗？”"] * 20
        action = ["他猛地冲出门，拔出短刀挡住来人，翻身避开一拳，又挥刀斩下。"] * 20
        psychology = [
            "他心中犹豫，忽然意识到那句话另有意思，也明白自己漏掉了什么。",
            "不过事情显然没有这么简单，桌下那只手已经握紧，呼吸也慢了下来。",
        ] * 10
        paragraphs = [item + item for item in daily + conflict + action + psychology]
        chapters.append(f"第{number}节 本地样本{index}\r\n\r\n" + "\r\n\r\n".join(paragraphs))
    return "\r\n\r\n".join(chapters)


def test_reference_style_requires_author_samples_and_blind_test_before_activation(
    clean_test_database: None, tmp_path: Path
) -> None:
    assert DATABASE_URL is not None
    source = tmp_path / "reference-gb18030.txt"
    source.write_bytes(_reference_source().encode("gb18030"))
    settings = Settings(local_task_worker_enabled=False,
        database_url=DATABASE_URL,
        local_token="integration-token",
        _env_file=None,
    )
    nonce = uuid4().hex

    with TestClient(create_app(settings)) as client:
        project = client.post(
            "/api/projects",
            json={"title": f"参考文风验证-{nonce}"},
            headers=headers(f"reference-project-{nonce}"),
        ).json()
        project_id = project["project_id"]
        registered = client.post(
            f"/api/projects/{project_id}/reference-style/corpora",
            json={
                "source_path": str(source),
                "local_analysis_allowed": True,
                "provider_excerpt_allowed": False,
                "confirmed": True,
            },
            headers=headers(f"reference-register-{nonce}"),
        )
        assert registered.status_code == 200, registered.text
        workspace = registered.json()
        assert workspace["manifest"]["source_status"] == "verified"
        assert workspace["manifest"]["parse_report"]["provider_calls"] == 0
        assert workspace["manifest"]["provider_excerpt_allowed"] is False
        assert len(workspace["samples"]) == 67

        dimensions = {
            "daily_dialogue": ["dialogue", "humor"],
            "conflict_dialogue": ["dialogue", "rhythm"],
            "action": ["rhythm"],
            "psychology_information": ["information_release", "narrator_stance"],
            "opening": ["information_release"],
            "ending": ["ending"],
            "full_chapter": ["rhythm", "narrator_stance"],
        }
        selected: dict[str, dict[str, object]] = {}
        for sample in workspace["samples"]:
            selected.setdefault(sample["category"], sample)
        for category, sample in selected.items():
            reviewed = client.put(
                f"/api/projects/{project_id}/reference-style/samples/{sample['sample_id']}",
                json={
                    "decision": "approved",
                    "approved_dimensions": dimensions[category],
                    "prohibited_transfer": ["内容与专有名词"],
                    "author_note": "集成测试明确选择",
                    "confirmed": True,
                },
                headers=headers(f"reference-review-{category}-{nonce}"),
            )
            assert reviewed.status_code == 200, reviewed.text

        compact = client.get(
            f"/api/projects/{project_id}/reference-style?category=action",
            headers=headers(),
        )
        assert compact.status_code == 200
        compact_body = compact.json()
        assert compact_body["samples"]
        assert {item["category"] for item in compact_body["samples"]} == {"action"}
        assert compact_body["approved_counts"] == {
            category: 1 for category in dimensions
        }
        assert len(compact.content) < len(registered.content)

        draft = client.post(
            f"/api/projects/{project_id}/reference-style/profiles",
            json={"manifest_id": workspace["manifest"]["manifest_id"], "confirmed": True},
            headers=headers(f"reference-draft-{nonce}"),
        )
        assert draft.status_code == 200, draft.text
        draft_body = draft.json()
        assert draft_body["status"] == "draft"
        assert all(item["evidence_sample_ids"] for item in draft_body["positive_contract"])

        rejected = client.post(
            f"/api/projects/{project_id}/reference-style/profiles/{draft_body['profile_id']}/activate",
            json={"blind_test": {}, "confirmed": True},
            headers=headers(f"reference-reject-activation-{nonce}"),
        )
        assert rejected.status_code == 400

        activated = client.post(
            f"/api/projects/{project_id}/reference-style/profiles/{draft_body['profile_id']}/activate",
            json={
                "blind_test": {
                    "more_natural": True,
                    "closer_rhythm": True,
                    "dialogue_interaction": True,
                    "no_content_transfer": True,
                    "keeps_project_voice": True,
                },
                "confirmed": True,
            },
            headers=headers(f"reference-activate-{nonce}"),
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "active"

        shared = client.put(
            f"/api/projects/{project_id}/reference-style/profiles/"
            f"{draft_body['profile_id']}/global-default",
            json={
                "reason": "作者确认所有作品共享这份抽象文风合同",
                "confirmed": True,
            },
            headers=headers(f"reference-global-default-{nonce}"),
        )
        assert shared.status_code == 200, shared.text
        assert shared.json()["contains_reference_text"] is False

        assert shared.json()["profile_id"] == draft_body["profile_id"]
        assert shared.json()["source_project_id"] == project_id
        assert "今天还去吗" not in str(shared.json())
