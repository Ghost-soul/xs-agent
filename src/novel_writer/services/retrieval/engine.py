"""Remove historical LanceDB data when its owning project is explicitly deleted."""

import importlib
from pathlib import Path
from uuid import UUID

INDEX_KEYS = ("chapters", "characters", "world_rules", "promises")


async def purge_project_indexes(project_id: UUID, base_dir: Path) -> None:
    if not base_dir.exists() or not any(base_dir.iterdir()):
        return
    if base_dir.is_symlink() or base_dir.is_junction():
        raise ValueError("retrieval root must not be a link")
    lancedb = importlib.import_module("lancedb")
    database = lancedb.connect(str(base_dir))
    predicate = f"project_id = '{UUID(str(project_id))}'"
    for index_key in INDEX_KEYS:
        table_name = f"novel_{index_key}"
        if table_name in database.table_names():
            table = database.open_table(table_name)
            table.delete(predicate)
            if table.count_rows(filter=predicate) != 0:
                raise RuntimeError("retrieval cleanup did not remove every project row")
