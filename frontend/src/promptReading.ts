/** Display-only labels. Unrecognized fields and all original values remain visible. */
export const promptLabels: Record<string, string> = {
  story_task: "作者任务", direction: "创作方向", author_note: "作者补充", author_answers: "作者答复",
  narrative_design: "叙事设计", selected_cards: "选定卡片", world_cards: "世界背景题材卡",
  formal_reference: "正式参考", opening_reference: "开场参考", formal_start: "正式开场状态",
  knowledge_context: "检索上下文", style_guidance: "风格指导", future_proposal_not_fact: "未来建议（不是事实）",
  plot_execution: "情节执行", current_task: "当前任务", selected_narratives: "选定叙事",
  stage_context: "阶段上下文", continuity: "连续性", stage_scale: "阶段规模", macro_reference: "长线参考",
  creative_autonomy: "创作自主范围", new_character_slots: "预留的新人物 ID",
  maximum_new_characters: "新增人物上限", maximum_total_cast: "全阶段承载人物上限",
  proposals_are_facts: "候选设计是否已是事实", new_characters: "新增人物设计",
  character_proposals: "人物候选设计", creative_notes: "创作待定事项（无需答复）",
  independent_goal: "独立目标", entry_reason: "出场原因", voice: "人物声音",
  name: "名称", id: "标识 ID", description: "描述", content: "内容", text: "原文", body: "正文",
  aliases: "别名", identity: "身份", personality: "性格", current_state: "当前状态", goals: "目标",
  characters: "人物", relationships: "关系", world_rules: "世界规则", world_lore: "世界资料",
  places: "地点", items: "条目", factions: "势力", events: "事件", beliefs: "人物认知",
  timeline: "时间线", reader_promises: "读者承诺", threads: "情节线", summaries: "摘要",
  character_id: "人物 ID", character_ids: "人物 ID 列表", object_id: "对象 ID", subject_id: "主体 ID",
  target_id: "目标 ID", source_id: "来源 ID", related_ids: "相关 ID", chapter_id: "章节 ID",
  project_id: "作品 ID", revision_id: "修订版本 ID", version_id: "版本 ID", version: "版本",
  source_revision: "来源修订", source: "来源", evidence: "证据", paragraph_ids: "段落 ID 列表",
  paragraph_id: "段落 ID", candidate: "待处理正文", candidate_chapter: "正文所属章节",
  chapter_goal: "阶段目标", bridge: "衔接", major_turn: "主要变化", genre_causal_role: "题材的因果作用",
  world_context: "世界背景依据", scenes: "场景或叙事单元", event: "事件", choice_and_response: "选择与回应",
  consequence: "预期后果", development: "过程设计", onstage_process: "现场展开过程",
  reveal_or_turn: "揭示或转折", payoff_or_aftermath: "回报或余波", local_freedom: "局部创作空间",
  size_weight: "展开份量权重", macro_progression: "长线推进", future_proposal: "后续建议",
  questions: "作者问题", story_questions: "故事悬念", question_scopes: "问题影响范围",
  author_question_reasons: "作者问题原因", kind: "类型", why_blocked: "提出阻塞的理由",
  scale_mode: "篇幅模式", min_characters: "参考字数下界", max_characters: "参考字数上界",
  preferred_units: "首选单元数", authorized_unit_limit: "授权单元上限", acceptable_units: "可接受单元数量区间",
  current_unit_reference: "当前单元篇幅参考", written_characters: "此前已写字数", remaining_units: "剩余单元数",
  allocation_policy: "篇幅分配方式", unit_kind: "单元组织方式", planning_unit_reference: "规划时的单元篇幅参考",
  counting: "计数口径", phase_status: "阶段状态", position: "当前位置", ordinal: "顺序",
  unit_limit: "单元上限", scene_count: "场景数量", current_plan: "当前计划", written_candidate: "已写候选正文",
  completed_units: "已完成单元", cast_scope: "人物范围", allowed_ids: "已有可选人物 ID",
  required_ids: "必选人物 ID", reference_boundary: "参考资料边界", read_only: "只读",
  output_schema: "输出结构", output_contract: "输出约定", writable_fields: "允许写入的字段",
  structured_delivery: "结构化交付指导", required_example: "必填结构示例", guidance: "交付指导",
  schema_sha256: "输出结构摘要值", reliability_contract: "可靠输出合同",
  progression_contract: "事件推进合同", event_progression: "事件连续推进指导",
  format_trial: "保留格式要求的试验", format_trial_contract: "格式试验合同",
  format_required: "仍要求输出格式", local_output_validation: "本地输出格式校验",
  writes_facts: "是否写入事实库", chief_response: "Chief 原始设计", schedule: "程序单元调度",
  trial_schedule: "试验单元调度", continuity_notes: "未校验连续性笔记", validated: "是否已验证",
  reference_characters_not_assigned_cast: "人物参考名册（不代表指定出场）",
  extracted_units: "读取到的单元数", scheduled_units: "安排的单元数", authorized_limit: "授权上限",
  fallback: "是否使用首选单元数", grouped: "是否合并到授权槽位", raw_response: "原始响应",
  unvalidated_chief_material: "未校验的 Chief 单元设计",
  response_requirement: "返回要求", type: "数据类型", properties: "字段定义", required: "必填字段",
  additionalProperties: "是否允许额外字段", items_schema: "列表项结构", anyOf: "满足任一结构",
  oneOf: "仅满足一个结构", allOf: "同时满足的结构", enum: "可选值", const: "固定值", default: "默认值",
  title: "标题", titles: "标题候选", minItems: "最少项数", maxItems: "最多项数",
  minLength: "最短字符数", maxLength: "最长字符数", minimum: "最小值", maximum: "最大值",
  exclusiveMinimum: "不含端点的下界", exclusiveMaximum: "不含端点的上界", format: "格式",
  $defs: "结构定义", $ref: "结构引用", $schema: "结构协议", pattern: "匹配规则",
  CharacterProposal: "新人物设计结构", CreativePlan: "创作方案结构", CraftUnit: "创作单元结构",
  Development: "过程设计结构", AuthorBlocker: "作者问题原因结构",
  revision_instruction: "本次修订要求", original_draft: "完整原稿", scope: "授权范围",
  authorized_paragraphs: "允许修改的段落", read_only_neighbors: "相邻只读段落",
  expression_reference: "表达参考", title_source: "待命名正文", protected_paragraph_ids: "受保护的段落 ID",
  changes: "事实变化", collection: "资料集合", values: "字段值", observation: "观察依据",
  outcome: "结果", status: "状态", position_evidence: "位置证据", promise_action: "承诺变更动作",
  patches: "局部修改", replacement: "替换内容", instruction: "要求", reason: "理由", mode: "模式",
  chapters: "章节", previous_ending: "上一章结尾", previous_unit_ending: "上一单元结尾",
  recent_chapters: "最近章节", formal_summaries: "正式摘要", relevant_history: "相关历史",
  working_state: "工作状态", handoff: "事实接力", verified_handoff: "已验证接力",
  retrieved_passages: "检索原文", passages: "原文片段", hits: "检索命中", query: "查询",
  queries: "查询列表", score: "检索分数", cutoff: "检索截止位置", cutoff_ordinal: "截止章节序号",
  content_hash: "内容摘要值", body_sha256: "正文 SHA256", sha256: "SHA256 摘要值",
  summary: "摘要", temporal_scope: "时间范围", valid_from: "有效起点", valid_to: "有效终点",
  relationship_type: "关系类型", from_character_id: "关系起点人物 ID", to_character_id: "关系终点人物 ID",
  importance: "重要程度", notes: "备注", details: "详情", rules: "规则", constraints: "限制条件",
  category: "分类", tags: "标签", core_definition: "核心定义", reader_contract: "读者预期",
  writing_guidance: "写作指导", detection: "表现特征", combinations: "组合建议", prerequisites: "适用前提",
  actual_recent_changes: "近期实际变化", author_boundaries: "作者明确边界", author_decisions: "作者决定",
  author_direction: "作者方向", author_origin: "作者提供的来源", available: "是否可用", base: "基准",
  binding_instruction: "绑定说明", boundary: "边界", candidates: "候选项", cast: "承载人物",
  chief: "Chief 剧情设计", writer: "Writer 写作", memory: "Memory 事实接力", checker: "Checker 逻辑核对",
  editor: "Editor 修订", complete: "是否完整", context: "上下文", context_policy: "上下文策略",
  coverage: "覆盖情况", current: "当前", current_phase: "当前阶段", earlier_direct_evidence: "较早的直接证据",
  earlier_excerpts: "较早的原文片段", end: "结束位置", family: "类别", future_is_fact: "未来计划是否已是事实",
  included: "是否包含", interpretation: "解读", knowledge_retrieval: "知识检索",
  later_plans_not_current_task: "后续计划（不属于当前任务）", lexical_queries: "词项检索查询",
  maximum_cast: "承载人物上限", method: "方法", model_key: "模型标识", modules: "资料模块",
  narrative_position: "叙事位置", nature: "性质", next_phase_reference: "下一阶段参考", offset: "偏移位置",
  omission_is_absence: "省略是否表示不存在", opening: "开场", opening_position: "开场位置", policy: "策略",
  primary: "主要项", secondary: "次要项", recent_prose: "近期正文", recent_summaries: "近期摘要",
  record: "记录", related_open_references: "相关未结事项参考", renderer: "渲染版本", revision: "修订版本",
  selected: "是否选入", source_length: "来源长度", source_version_id: "来源版本 ID",
  story_foundation: "故事基础", summary_availability: "摘要可用情况", system_prompt: "系统 Prompt",
  task_mode: "任务模式", templates: "模板", total: "总数", unit: "单元", unit_outcomes: "单元结果",
  unit_position: "单元位置", unresolved: "未解决事项", viewpoint: "视角",
  craft_payload_count: "创作输入计数", craft_protected_count: "受保护连续性计数",
};

const enumLabels: Record<string, string> = {
  "stage-range": "阶段篇幅", natural: "自然篇幅", current_unit: "当前单元", later: "后续",
  missing_canonical_fact: "正式资料缺口", author_boundary_conflict: "作者边界冲突",
  object: "对象", array: "列表", string: "文本", integer: "整数", number: "数字", boolean: "布尔值",
};

export type PromptReading = { text: string; sections: number; translatedFields: number };

/** Locate complete JSON only; surrounding prose and malformed fragments remain untouched. */
function jsonEnd(text: string, start: number): number {
  const stack: string[] = [];
  let quoted = false, escaped = false;
  for (let i = start; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === "{" || char === "[") stack.push(char);
    else if (char === "}" || char === "]") {
      if (stack.pop() !== (char === "}" ? "{" : "[")) return i + 1;
      if (!stack.length) return i + 1;
    }
  }
  return text.length;
}

function readableJson(raw: string): { text: string; fields: number } {
  const parsed = JSON.parse(raw); // Validate, but do not reserialize numbers or duplicate keys.
  const tokens = raw.match(/"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\]:,]/gu)!;
  let index = 0, fields = 0;
  const label = (key: string, schema = false) => {
    const translated = schema && key === "items" ? "列表项结构" : Object.hasOwn(promptLabels, key) ? promptLabels[key] : undefined;
    if (translated) fields += 1;
    return translated ? `${translated}（${key}）` : key;
  };
  function value(depth: number, field = "", schema = false): string {
    if (depth > 128) throw new Error("Keep deeply nested source verbatim");
    const token = tokens[index++];
    if (token === "{" || token === "[") {
      const end = token === "{" ? "}" : "]";
      const rows: string[] = [];
      while (tokens[index] !== end) {
        if (token === "{") {
          const key = JSON.parse(tokens[index++]) as string;
          index += 1; // colon
          rows.push(`${"  ".repeat(depth + 1)}${JSON.stringify(label(key, schema))}: ${value(depth + 1, key, schema || key === "output_schema" || key === "json_schema")}`);
        } else rows.push(`${"  ".repeat(depth + 1)}${value(depth + 1, field, schema)}`);
        if (tokens[index] === ",") index += 1;
      }
      index += 1;
      return rows.length ? `${token}\n${rows.join(",\n")}\n${"  ".repeat(depth)}${end}` : token + end;
    }
    if (token.startsWith('"')) {
      const decoded = JSON.parse(token) as string;
      if (field === "required" && Object.hasOwn(promptLabels, decoded)) return JSON.stringify(label(decoded));
      if (["type", "kind", "scale_mode", "enum", "const"].includes(field) && Object.hasOwn(enumLabels, decoded)) {
        return JSON.stringify(`${decoded}（${enumLabels[decoded]}）`);
      }
    }
    return token;
  }
  return { text: value(0, "", !!(parsed && parsed.type && parsed.properties)), fields };
}

export function readPrompt(source: string): PromptReading {
  const starts = /(?:^|\n)[\t ]*(?=[{\[])/gu;
  let match: RegExpExecArray | null, cursor = 0, text = "", sections = 0, translatedFields = 0;
  while ((match = starts.exec(source)) !== null) {
    const start = match.index + match[0].length;
    const end = jsonEnd(source, start);
    text += source.slice(cursor, start);
    const raw = source.slice(start, end);
    try {
      const result = readableJson(raw);
      text += result.text; translatedFields += result.fields; sections += 1;
    } catch { text += raw; }
    cursor = end;
    starts.lastIndex = end;
  }
  return { text: text + source.slice(cursor), sections, translatedFields };
}
