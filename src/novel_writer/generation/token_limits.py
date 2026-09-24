"""Separate input and output ceilings for new and explicitly re-budgeted work."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel_writer.generation.schemas import GenerationSpec

INPUT_TOKEN_LIMIT = 200000
# Historical name retained for the unchanged output ceiling.
TOKEN_LIMIT = 100000


def with_limits(
    spec: "GenerationSpec", *, input_limit: int = INPUT_TOKEN_LIMIT, output_limit: int = TOKEN_LIMIT
) -> "GenerationSpec":
    return spec.model_copy(
        update={
            "input_limit": input_limit,
            "chief_output_limit": output_limit,
            "writer_output_limit": output_limit,
            "auxiliary_output_limit": output_limit,
            "roles": {
                role: config.model_copy(update={"output_limit": output_limit})
                for role, config in spec.roles.items()
            },
        }
    )
