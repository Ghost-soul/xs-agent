from novel_writer.services.canonical_json import canonical_json, canonical_json_sha256


def test_canonical_json_is_stable_across_mapping_order_and_unicode() -> None:
    left = {"z": [3, {"beta": True, "alpha": "阵光"}], "a": 1}
    right = {"a": 1, "z": [3, {"alpha": "阵光", "beta": True}]}

    assert canonical_json(left) == canonical_json(right)
    assert canonical_json(left) == '{"a":1,"z":[3,{"alpha":"阵光","beta":true}]}'
    assert canonical_json_sha256(left) == canonical_json_sha256(right)
