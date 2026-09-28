import type { GenerationSpec, ProviderProfile } from "./api";
import { TOKEN_LIMIT } from "./generationTokenLimits";

export function outputCapacities(spec: GenerationSpec, profile?: ProviderProfile) {
  const defaults = { chief: spec.chief_output_limit, writer: spec.writer_output_limit, memory: spec.auxiliary_output_limit, checker: spec.auxiliary_output_limit, editor: spec.auxiliary_output_limit };
  return (["chief", "writer", "memory", "checker", "editor"] as const).map((role) => {
    const configured = spec.roles?.[role];
    const model = configured?.model ?? (role === "writer" ? spec.writer_model : spec.chief_model);
    const capacity = profile?.models.find((m) => m.id === model)?.max_output_tokens;
    return { role, model, requested: configured?.output_limit ?? defaults[role] ?? TOKEN_LIMIT, capacity };
  });
}

export function fitOutputCapacities(spec: GenerationSpec, profile?: ProviderProfile): Partial<GenerationSpec> {
  const cap = (model: string, value: number) => Math.min(value, TOKEN_LIMIT, profile?.models.find((m) => m.id === model)?.max_output_tokens ?? TOKEN_LIMIT);
  return {
    chief_output_limit: cap(spec.chief_model, spec.chief_output_limit ?? TOKEN_LIMIT),
    writer_output_limit: cap(spec.writer_model, spec.writer_output_limit ?? TOKEN_LIMIT),
    auxiliary_output_limit: cap(spec.chief_model, spec.auxiliary_output_limit ?? TOKEN_LIMIT),
    roles: Object.fromEntries(Object.entries(spec.roles ?? {}).map(([role, value]) => [role, { ...value, output_limit: cap(value.model, value.output_limit) }])),
  };
}
