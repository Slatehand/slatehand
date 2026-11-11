"""专业契约输入的回归；输入契约回归不冒充模型服从性评估。"""

from pathlib import Path

from core.director_proposals.professional_contracts import read_contract


def test_keyframe_contract_reviews_prompt_constraints():
    keyframe = read_contract("keyframe")["resources"][0]["text"]
    assert "正文约束复核" in keyframe and "无字幕、无水印" in keyframe


def test_keyframe_contract_carries_location_decoupling_and_user_boundary():
    """地点图片不形成隐藏输入，且不得新增用户未要求的排除项。"""
    text = "\n".join(r["text"] for r in read_contract("keyframe")["resources"])
    for required in (
        "不会自动进入生图调用",
        "不得借机新增用户没有要求的排除项",
    ):
        assert required in text
    assert "environment_view_id" not in text


def test_keyframe_contract_treats_location_images_as_explicit_references():
    """地点图片只在本帧显式参考（或用户加入辅助参考）后才进入实际输入。"""
    skill = read_contract("keyframe")["resources"][0]["text"]
    for required in (
        "地点语义与图片",
        "start_reference_location_image_ids",
        "referenceable=true",
        "只有显式参考的地点图片才会作为生图输入",
        "全景图不能直接参考",
    ):
        assert required in skill
    assert "地点视图的使用" not in skill


def test_script_skill_designs_shots_before_packing_them_into_segments():
    """先设计 Shot 列表，再按打包规则装进 Segment；长镜头与段内切镜都通过覆盖关系表达。"""
    resources = read_contract("script")["resources"]
    text = {item["name"]: item["text"] for item in resources}
    skill = text["SKILL.md"]
    for required in ("设计 Shot 列表", "打包成 Segment", "打包理由"):
        assert required in skill, required
    contract = text["references/script-contract.md"]
    for required in (
        "先设计 Shot 列表，再打包成 Segment",
        "一个 Segment 可以覆盖多个相邻 Shot",
        "一个 Shot 也可以跨多个相邻 Segment",
        "实验前每个 Segment 不超过 3 个",
        "需要精确构图控制的镜头独占 Segment",
        "`boundary_from_previous`",
        "初稿与回改都在 `reply_markdown` 简述打包理由",
    ):
        assert required in contract, required
    quality = " ".join(text["references/quality-gate.md"].split())
    for required in (
        "打包覆盖完整连续",
        "每个 Segment 预计不超过 15 秒",
        "段内切镜只用于同空间、同一节拍的紧密镜头组",
        "实验前每个 Segment 不超过 3 个",
        "精确构图控制",
        "打包理由已写入 `reply_markdown`",
    ):
        assert required in quality, required
    # 旧规则不能以任何措辞残留在契约资源里。
    combined = "\n".join(text.values())
    for stale in (
        "初次规划一个 Shot 只对应一个 Segment",
        "新规划的 Shot 恰好一个 Segment",
        "超过 15 秒软约束也不压缩或拆分",
        "一个 Shot 只有一个 Segment",
    ):
        assert stale not in combined, stale
    launcher = (
        Path(__file__).resolve().parents[1] / "integrations/agent_launcher/PROMPT.md"
    ).read_text()
    assert "exceeds_soft_duration_limit" in launcher
    assert "拆分 Segment 不改变 Shot 设计" in launcher
    assert "一个 Shot 只有一个 Segment" not in launcher


def test_video_contract_defers_long_segment_split_to_pre_generation_reminder():
    video = read_contract("video_prompt")["resources"][0]["text"]
    assert "exceeds_soft_duration_limit" in video
    assert "split_segment" in video
    assert "不擅自拆分，也不拒绝提交" in video
    launcher = (
        Path(__file__).resolve().parents[1] / "integrations/agent_launcher/PROMPT.md"
    ).read_text()
    assert "exceeds_soft_duration_limit" in launcher


def test_multi_shot_video_segments_are_in_video_contract_and_launcher():
    """视频提示词契约与启动器都描述多 Shot Segment 的切镜写法与软约束。"""
    video = read_contract("video_prompt")["resources"][0]["text"]
    launcher = (
        Path(__file__).resolve().parents[1] / "integrations/agent_launcher/PROMPT.md"
    ).read_text()
    for text in (video, launcher):
        for required in (
            "context.shots",
            "[Shot 2] At MM:SS.mmm",
            "插件 Skill",
            "软约束",
            "切点漂移",
            "不超过 3 个",
            "首尾 Picture",
            "boundary_from_previous",
        ):
            assert required in text, required
        assert "一个 Shot 只有一个 Segment" not in text


def test_script_contract_carries_single_visible_speaker_rule_per_shot():
    """段内切镜后开口约束按 Shot 计：每个 Shot 至多一个开口说话的可见角色。"""
    resources = read_contract("script")["resources"]
    names = [item["name"] for item in resources]
    assert "references/drama-scene-craft.md" in names
    craft = next(i["text"] for i in resources if i["name"] == "references/drama-scene-craft.md")
    assert "每个 Shot 里只有一个开口说话的可见人物" in craft
    assert "一个 Segment 里只有一个开口说话的可见人物" not in craft
    quality = " ".join(
        next(i["text"] for i in resources if i["name"] == "references/quality-gate.md").split()
    )
    assert "每个 Shot 至多一个开口说话的可见角色" in quality
    assert "每个 Segment 至多一个开口说话的可见角色" not in quality


def test_script_contract_splits_each_long_dialogue_line_independently_of_segments():
    """15 秒约束必须直接作用于单条对白，不能被误写成只拆 Segment。"""
    resources = read_contract("script")["resources"]
    text = {item["name"]: item["text"] for item in resources}
    for name in (
        "SKILL.md",
        "references/script-contract.md",
        "references/drama-scene-craft.md",
        "references/quality-gate.md",
    ):
        assert "拆 Segment 不能代替拆长对白" in text[name], name
    contract = " ".join(text["references/script-contract.md"].split())
    for required in (
        "`DialogueLine` 自身有独立的 15 秒软上限",
        "任何一条 `dialogue_lines[].text` 本身预计超过 15 秒",
        "不删减、概括或改写原文",
        "使每条预计不超过 15 秒",
    ):
        assert required in contract, required


def test_script_contract_keeps_external_speakers_and_planned_music():
    """龙套台词不能因没有引用卡而丢失；导演方案写明的配乐不能被当成未经要求的 BGM。"""
    resources = read_contract("script")["resources"]
    contract = next(i["text"] for i in resources if i["name"] == "references/script-contract.md")
    assert "写 `character_id: null` 与 `speaker_label`" in contract
    assert "不要删掉，也不要挪进 `audio_direction`" in contract
    assert "导演方案或用户明确写出的配乐、音乐入点就是明确要求" in contract
    assert "也不要写“无背景音乐”去否定方案" in contract
    script = next(i["text"] for i in resources if i["name"] == "SKILL.md")
    assert "对白写 `character_id: null` 加 `speaker_label`" in script


def test_location_contract_keeps_images_optional_and_decoupled_from_shots():
    """Location Planner 只规划可选独立图片，不给 Shot 或 Keyframe 建绑定。"""
    resources = read_contract("location")["resources"]
    text = {item["name"]: item["text"] for item in resources}
    skill = text["SKILL.md"]
    for required in (
        "图片不是“主图 → 视图”的树",
        "不要默认创建全景图",
        "Shot 不保存地点或图片引用",
    ):
        assert required in skill
    coverage = text["references/view-coverage.md"]
    for required in (
        "不是必须覆盖全部 Shot 的机位表",
        "不为每个 Shot 预建一张图",
        "全景图适合需要环视",
    ):
        assert required in coverage
    # 图片说明强制、全景取景子图独立，且规划本身不能删除。
    bible = text["references/location-bible.md"]
    assert "每张图片必须有强制 `description`" in bible
    assert "子图的字节和说明从此独立" in bible


def test_script_contract_requires_plan_coverage_and_duration_reconciliation():
    """最少切镜不能变成删内容：方案清单逐项落地、合计时长对账、删减必须在 reply_markdown 说明。"""
    resources = read_contract("script")["resources"]
    text = {item["name"]: item["text"] for item in resources}
    assert "列出方案清单" in text["SKILL.md"]
    assert "合并镜号只合并摄影，不删内容" in text["SKILL.md"]
    assert "没有删减时明确写“无删减”" in text["SKILL.md"]
    assert "任何一条对白本身按自然语速、语气和停顿预估超过 15 秒" in text["SKILL.md"]
    assert "dialogue_timing.exceeds_duration" not in text["SKILL.md"]
    quality = text["references/quality-gate.md"]
    assert "方案覆盖：方案清单逐项落到了具体 Segment 或 Shot" in quality
    assert "偏差超过约 30% 已在" in quality
    assert "model_constraint" in text["references/script-contract.md"]
    intent = text["references/cinematic-director-intent.md"]
    assert "最少切镜原则只约束剪辑点，不减少内容" in intent


def test_split_shot_continuation_prefers_frame_extraction_and_cuts_turn_thirty_degrees():
    """同一 Shot 拆段无法共享边界时建议抽真实末帧续拍；30° 规则只约束真正的切镜。"""
    keyframe = "\n".join(r["text"] for r in read_contract("keyframe")["resources"])
    assert "从上一 seg 抽取" in keyframe  # 规划 Skill 给出人类入口
    # 规划与生产 Skill 都给出 Agent 入口
    assert keyframe.count('generate_media(kind="frame_extraction"') >= 2
    assert "默认抽倒数第 5 帧" in keyframe
    assert "只有人类能抽帧" not in keyframe
    assert "用户要求直接生成时照常生成" in keyframe
    assert "start_extraction_suggested=true" in keyframe
    assert "批量生成会自动跳过它们" in keyframe
    video = read_contract("video_prompt")["resources"][0]["text"]
    assert "从前段真实末帧\n抽取" in video
    script = "\n".join(r["text"] for r in read_contract("script")["resources"])
    assert "至少转 30°" in script
    assert "同一 Shot 拆成的相邻 Segment 是同一个连续镜头，不是剪辑点" in script


def test_same_shot_split_reuses_instead_of_redesigning_start_frames():
    """同 Shot 因模型约束拆段时，后段首帧按共享→抽帧→复用→参考重生成选来源，不另编表演瞬间。"""
    keyframe = "\n".join(r["text"] for r in read_contract("keyframe")["resources"])
    assert "同一 Shot 拆段：延续段不重新设计" in keyframe
    assert "boundary_reason" in keyframe
    assert "start_reuse_keyframe_id" in keyframe
    assert "共享 → 抽帧 → 复用 → 参考重生成" in keyframe
    assert "对白之间的细微表演由视频完成" in keyframe
    assert "reuse_ready" in keyframe


def test_keyframe_planner_derives_framing_and_frame_choice_from_the_beat():
    """景别服从镜头功能且可写回脚本，一帧一个重心，结果不提前进首帧，双帧有成本。"""
    resources = {r["name"]: r["text"] for r in read_contract("keyframe")["resources"]}
    planner = resources["SKILL.md"]
    design = resources["references/cinematic-keyframe-design.md"]
    assert "不得重新选择景别" not in planner  # 景别是手段，不是不可改的上游决定
    assert "update_shot_fields" in planner and "冻结的工作集过期" in planner
    assert "自行建立完整空间" not in planner
    assert "不要为了“有首帧”把结果提前画进首帧" in planner
    assert "双帧不是更保险的选择" in planner
    assert "一帧一个重心" in design
    assert "信息覆盖不等于把所有信息塞进一张图" in design
    assert "不设全片默认景别" in design  # 收紧景别不能变成新的机械默认值


def test_every_keyframe_decision_starts_with_action_state_and_continuity():
    """首帧启动前、尾帧已结束、进行中不进首尾槽位；连续镜头优先共享或抽帧，且规则排在最前。"""
    resources = {r["name"]: r["text"] for r in read_contract("keyframe")["resources"]}
    planner = resources["SKILL.md"]
    # Agent 可能只读开头几千字符，三问必须排在规则正文之前。
    head = planner[: planner.index("## 定位与上游")]
    for required in (
        "每张帧先答三问",
        "启动前",
        "进行中",
        "已结束",
        "动词序列",
        "start_extraction_suggested=true",
        "“跳切更有冲击力”不是理由",
        "Core 没有中间槽位",
        "在回复里每帧一行",
    ):
        assert required in head, required
    assert len(head) < 2000

    sequence = resources["production/slatehand-keyframe-sequence/SKILL.md"]
    for required in ("| 动作状态 |", "| 状态错位 |", "重写视频提示词开头", "上一段改选视频后重抽"):
        assert required in sequence, required

    # “将发未发”“蓄势”会让动作中途被当成首帧，这些说法不能回来。
    combined = "\n".join(resources.values())
    for stale in ("将发未发", "蓄势", "蓄力", "动作即将发生", "动势"):
        assert stale not in combined, stale
    # 关键帧计划没有 reply_markdown 字段，写了会被 schema 拒绝。
    assert "reply_markdown" not in planner

    video = read_contract("video_prompt")["resources"][0]["text"]
    assert "首帧里已经发生的动作不重演" in video
    assert "必须按新帧重写" in video

    launcher = (
        Path(__file__).resolve().parents[1] / "integrations/agent_launcher/PROMPT.md"
    ).read_text()
    assert "每帧三问" in launcher and "进行中的画面不进首尾槽位" in launcher


def test_image_prompt_skill_inherits_scene_reference_by_framing():
    """场景参考不把参考图的景别和构图带进来；示例也不教“以某图为场景基础”。"""
    skill = next(
        r["text"]
        for r in read_contract("keyframe")["resources"]
        if r["name"].startswith("global/slatehand-image-prompt-writing/")
    )
    assert "场景参考按本图画框决定继承什么" in skill
    assert "不要笼统写“以某张图为场景基础”" in skill
    assert "为场景基础，保留" not in skill
    assert "近景示例（场景参考只取光线与材质）" in skill


def test_keyframe_production_samples_first_and_reports_hidden_candidates():
    """先打样再铺开；只隐藏有明确依据的候选，并如实汇报。"""
    sequence = next(
        r["text"]
        for r in read_contract("keyframe")["resources"]
        if r["name"] == "production/slatehand-keyframe-sequence/SKILL.md"
    )
    assert "先打样，再铺开" in sequence
    assert "一次 `start` 会全部提交" in sequence
    assert "| 计划错误 |" in sequence
    assert "不为了当锚点把某帧拉远" in sequence
    assert "不隐藏（dismiss）被拒的候选" not in sequence
    assert "只隐藏有明确依据的" in sequence
    assert "留下最接近的一条" in sequence
    assert "隐藏了哪些候选及原因" in sequence


def test_script_and_video_contracts_follow_the_framing_rules():
    """脚本从源头不为交代地点拉远；视频提示词以已选帧为起止画面的事实。"""
    script = "\n".join(r["text"] for r in read_contract("script")["resources"])
    assert "空间三层" not in script
    assert "景别从镜头功能和心理距离来" in script
    assert "一个构图一个重心" in script
    assert "最少切镜也不等于用一个大景别把所有信息装进一个构图" in script
    assert "每个全景都能说出戏剧理由" in script
    video = read_contract("video_prompt")["resources"][0]["text"]
    assert "已选帧是起止画面的事实" in video


def test_reference_library_contract_carries_voice_description_craft():
    """音色描述从身份与用嗓经历写起，给声学特征与排除项，并以平静朗读收尾。"""
    resources = read_contract("reference_library")["resources"]
    text = {item["name"]: item["text"] for item in resources}
    assert "references/voice-descriptions.md" in text["SKILL.md"]
    assert "voice-descriptions.md" in text["references/card-contract.md"]
    craft = " ".join(text["references/voice-descriptions.md"].split())
    for required in (
        "先写这是谁、怎样用嗓",
        "听得出来的声学特征",
        "写明排除项",
        "不要换成某个现实语种的名称",
        "不带英语口音",
        "语速平稳均匀，语调平淡，无明显情绪起伏",
    ):
        assert required in craft, required
