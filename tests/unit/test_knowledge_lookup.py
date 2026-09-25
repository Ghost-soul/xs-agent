from concurrent.futures import ThreadPoolExecutor

import pytest

from novel_writer.knowledge import lookup
from novel_writer.knowledge.text import chunks, rank, source


@pytest.mark.parametrize("queries", [
    ["玄铁剑", "腿伤"], ["剑"], ["champ"], ["AMPION"], ["role-a"], ["！"], ["不存在"],
])
def test_index_preserves_exact_terms_substrings_entity_ids_and_fusion(queries):
    sources = [
        source("chapter", "role-a", "她把玄铁剑交给师父。腿伤未愈。" * 35),
        source("chapter", "role-b", "The champion returns！"),
        source("chapter", "role-c", "另一处渡口的旧剑。"),
        source("chapter", "role-d", "所有人已经散去。"),
    ]
    pieces = chunks(sources)
    semantic = [[pieces[-1]["id"], "outside-this-book"]]
    assert lookup.prepare("test", sources).rank(queries, semantic) == rank(
        pieces, queries, semantic,
    )


def test_cache_validates_changed_text_and_does_not_retain_mutable_evidence():
    lookup.clear()
    sources = [source("chapter", "a", "玄铁剑归还。", entity_ids=["original"])]
    corpus = lookup.prepare("book:version", sources)
    sources[0]["entity_ids"][0] = "changed"
    hit = corpus.rank(["玄铁剑"])[0]
    assert hit["entity_ids"] == ["original"]
    hit["entity_ids"][0] = "tampered-result"
    assert corpus.rank(["玄铁剑"])[0]["entity_ids"] == ["original"]
    sources[0]["text"] = "被修改的冻结正文"
    with pytest.raises(ValueError, match="校验"):
        lookup.prepare("book:version", sources)


def test_cache_is_scoped_bounded_and_shared_during_concurrent_cold_builds(monkeypatch):
    lookup.clear()
    sources = [source("chapter", "a", "她还剑之后离开渡口。" * 120)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: lookup.prepare("book-a", sources), range(6)))
    assert all(item is results[0] for item in results)
    assert lookup.prepare("book-b", sources) is not results[0]
    expected = results[0].rank(["还剑"])
    monkeypatch.setattr(lookup, "MAX_CACHE_BYTES", results[0].retained_bytes + 1)
    lookup.prepare("book-c", sources)
    info = lookup.cache_info()
    assert info["entries"] == 1 and info["retained_bytes"] <= info["limit_bytes"]
    assert lookup.prepare("book-a", sources).rank(["还剑"]) == expected
