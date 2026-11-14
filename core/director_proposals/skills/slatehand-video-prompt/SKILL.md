---
name: slatehand-video-prompt
description: 根据当前视频 recipe 与实际输入优化单个 Segment 的视频提示词，并按用户要求保存 recipe 参考素材与机制参数。
---

先读取 `project.get_workset` 的 `video_prompt` 工作集。当前 recipe 声明可用槽位、机制参数、实例设置摘要与提示；插件随附的专业 Skill 和参考资料在本契约的 `plugin/` 资源中（工作集 `plugin_skills` 只列目录）。按这些资料组织正文，使用 Core 提供的实际引用标签，不自行编号、不推导未提交的媒体。

保持 Scene、覆盖 Shot、Segment 已保存的导演意图。用户指定正文语言时服从，未指定时不增加语言约束。

Shot 的 `composition` 与已选首尾帧的景别或机位不一致时，已选帧是起止画面的事实，不按文字把镜头写回去；把差异告诉用户，
由用户决定是否用 `edit_script` 写回（写回会清除覆盖该 Shot 的 Segment 已选视频）。

写正文前先看已选首尾帧：动作从首帧的状态开始，到尾帧的状态结束，首帧里已经发生的动作不重演。首帧换过（含抽帧）后，必须按新帧重写开头。

## 必检：优先 Ref2VA 与角色视觉锚点

写正文或提交视频生成前必须逐项完成，不能只拿首尾帧直接提交：

1. **优先多参考 recipe（Ref2VA）**：插件提供支持附加图片的多参考 recipe（如 `ref2va`）时一律优先推荐和使用；纯首尾帧 recipe（`fl2va`）只在**确实没必要用 Ref2VA**——无对白、无音频/视频参考，且已选首尾帧已完整呈现本段所有可见角色所需的全部外观（正脸、全身服装）或纯空镜——时才考虑使用。当前停在无附加图片槽位的 recipe 而本段需要参考图时，直接用 `set_video_recipe`（`video.set_recipe`）切到多参考 recipe，不必停下等用户点名（用户本轮明确指定保持当前 recipe 时除外）。
2. **补齐可见角色参考图**：生关键帧时用过的角色造型设定图**不会**自动进入视频输入。先用 `read_media_input` 看过已选首尾帧，再核对工作集 `character_visuals` 中每个可见角色，满足任一条就**主动绑入该角色匹配的已选造型图**（`looks[].selected_asset_id`）：
   - **首尾帧没有该角色**（`in_enabled_keyframes=false`、`unanchored_characters` 非空，例如首帧只有角色 A、后续切镜到角色 B，或角色 B 中途入画）；
   - **首尾帧只有该角色的局部、背影、侧影、远小轮廓或遮挡**（例如首帧只有手部或局部道具，镜头后续拉远到全身或转正露脸；或首帧只见上半身、后续起身露全身），仅靠首尾帧会让视频模型随机编造五官或服装。
3. **绑定与标签同步**：先用 `read_media_input` 看过该造型的 `selected_asset_id`，再用 `select_reference_image(source="asset", source_id=..., description=...)` 选入空槽位（或随 `save_video_prompt` 的 `recipe_inputs` 写入附加图片槽位；造型尚未选图时改选同场含该角色清晰全貌的已选关键帧 `source="keyframe"`，都没有则告知用户缺口），`description` 写清角色名及提取其面部五官、发型与全身服装（忽略设定图的浅灰背景与多视角排版）。绑定会重排 `<Picture N>` 标签，必须按回执 `data.asset_inputs`（或重读工作集）的实际标签写正文，并确认 `unanchored_characters` 清零。

## 多 Shot Segment

`context.shots` 是该 Segment 覆盖的 Shot（按顺序），可能只有一项，也可能有多项。

- 只有一项：Segment 是连续镜头，不虚构段内切镜；需要切镜时与用户讨论拆 Segment。
- 多项：正文按当前插件 Skill 的段内切镜写法顺序写出各 Shot（例如 `[Shot 1]`、`[Shot 2] At MM:SS.mmm …`）。切点按各 Shot 的构图、动作与 `duration_seconds` 的比例估算，只是软约束：Core 不按 Shot 切视频、不记录段内切点，生成后切点漂移仍是合格候选。
- 首尾 Picture 只锚定第一个 Shot 的开头与最后一个 Shot 的结尾；后续 Shot 或中途入画的人物与空间靠参考图维持，按上方“角色视觉锚点”主动用 Ref2VA 绑入对应角色造型图。
- 同一句台词跨越段内剪辑时，按插件 Skill 的写法连接前后两段并保持音频连续。
- 段内 Shot 数当前不超过 3 个；更多切镜先建议拆 Segment。

## 保存

- 只改正文：`video.set_prompt`（MCP `save_video_prompt` 不带 `recipe_inputs`）。
- 绑定参考素材或填写机制参数：`video.update_recipe_inputs`（MCP `save_video_prompt` 带 `recipe_inputs`），与正文原子保存。
  `bindings` 与 `params` 按槽位、按键合并：给出的槽位整体替换（空列表表示清空），未给出的槽位与参数保持原样。
- 含对白的 Segment：每句对白须已选 TTS 才能生成；未加入参考音频不阻止生成，但视频不会用到这些对白音频，
  工作集 `dialogue_audio.unbound_lines` 列出未加入的台词。通常保存时带 `attach_dialogue_audio: true`，Core 按与 Web“一键添加本段对白 TTS”
  相同的规则把已选 WAV 按剧本顺序放在参考音频最前，覆盖本句旧 WAV 并保留其他手动参考；缺任一句 TTS 或超出名额时整次拒绝。
- 两种保存的回执 `data` 都是写入后的摘要：`prompt_chars`、合并后的 `recipe_inputs`、实际提交标签 `asset_inputs`、
  插件 linter 的 `prompt_problems`、`dialogue_audio.unbound_lines` 与 `unanchored_characters`。确认保存结果直接读它，不要为确认而重读工作集。

都不生成媒体、不选择候选。用户明确要求生成时，再单独调用 `video.generate`。

## 参考素材与参数

- 工作集 `video_inputs[]` 给出当前 Segment 的其他图片计划与实际媒体绑定；图片可以是任意画面内容，不限角色、地点或关键帧。按 `reference_inputs[].asset_id` 用 `read_media_input` 审看已绑定图片；未选中的规划与候选不能冒充实际视频输入。需要单独规划或生成其他图片时，使用 `save_reference_image_plan` 与 `generate_media(kind="reference_image")`；看图后用 `select_reference_image` 选入对应卡片。
- 对白 TTS（`attach_dialogue_audio`）与缺失全貌的本段可见角色造型图由你按上文规则主动补齐；其它非角色造型的手工参考（参考视频、手动环境声/音色、额外道具或地点图）、机制参数，以及替换或移除用户已有手动绑定，只在用户本轮明确要求时修改。改某个槽位时以工作集 `recipe_inputs` 中该槽位为起点，不擅自增删、重排无关绑定或改写其说明。
- 槽位以 `video_recipe.recipe.slots` 为准，只填首帧/尾帧以外的槽位；首尾帧由 Core 按已选关键帧填写，不写进 `bindings`。数量、媒体类型、时长与 `describe=required` 的必填说明都按槽位声明。
- `asset_id` 从 `character_visuals`（角色造型图）或 `bindable_assets` 选取，其 `slots` 列出它能进入的槽位；用户点名的其它本项目 Asset 也可以，由 Core 校验。不能引用 Playground Asset。
- `params` 只填 `video_recipe.editable_params` 声明的机制参数，只给要改的键，不改时传 `{}`；时长、seed、画幅等带语义角色的参数由 Core 填写。
- 多参考 recipe 优先；补齐角色参考图或对白需要槽位时直接用 `video.set_recipe` 切换到多参考 recipe，切到不支持某些槽位的 recipe 前必须先清空这些槽位。
- 绑定变化会让 Core 按槽位顺序重新编号 Picture/Audio/Video 标签。正文需要引用新标签时，先带当前正文（`historical_prompt`）保存输入，从回执 `data.asset_inputs` 取得新标签，再写正文并保存。
- Core 原样提交已保存正文，不前置任何对白约束。对白写法由当前插件 Skill 规定（见本契约 `plugin/` 资源），
  工作集 `prompt_dialogue` 给出每句台词与实际 `audio_label`；添加或重排对白 TTS 会改变 Audio 标签，之后要重读工作集并按新标签改写正文。
  保存后重读工作集，`prompt_problems` 列出插件检查出的格式问题；它只是提示，不阻止生成，但应先按提示修正正文再请求生成。

## 时长提醒

超长 Segment 留到这里处理。工作集 `exceeds_soft_duration_limit` 为
true 时（`context.duration_seconds` 超过插件声明的 `soft_duration_limit_seconds`），在优化
提示词或用户要求生成该 Segment 视频前主动提醒用户：说明时长和可能 OOM 的风险，建议拆成多个 Segment，并给出
建议的拆分点（自然动作的稳定状态、明确的控制方式变化或可复用的连续性锚点），同时说明拆分后
新 Segment 需要自己的关键帧和提示词；后段与前段不共享边界帧时，建议它的首帧在前段视频选定后从前段真实末帧
抽取，而不是独立生图。

这只是提醒，不是 Core 约束。用户同意后再用 `script.apply_operations` 的 `split_segment` 拆分，然后分别为各段
写提示词；拆分不改变 Shot 设计，拆出的相邻 Segment 共享边界 Shot 时是延续同一镜头，两段都要给出覆盖的
`shot_ids` 并在后一段填写 `boundary_from_previous`；因视频插件的时长软约束拆分时，`reason` 使用
`model_constraint`。用户选择不拆时照常保存或生成，不擅自拆分，也不拒绝提交。

对白句数超过当前 recipe 参考音频名额时，工作集 `dialogue_audio.error` 给出提示，
`attach_dialogue_audio` 会整次拒绝。生成前主动提醒用户并建议在台词之间或 Shot 边界用 `split_segment` 拆分，
`reason` 使用 `model_constraint`；用户不拆时，按用户指定只绑定其中几句，未绑定的台词不会进入视频。

有对白时只认工作集 `dialogue_timing` 的真实 WAV 时序；未就绪不猜秒数。真实结束时间超过 Segment 时长时建议
加长或拆分，不删减、改写或加速台词。

设定对白时序与 Segment 时长时，首句开口前、末句结束后各留至少 `breathing_seconds`（0.5 秒），否则相邻 Segment
硬切后两段对白会贴在一起。`dialogue_timing` 的 `lead_seconds` / `tail_seconds` 给出当前余量（段尾按插件实际输出时长计算），
不足时 `breathing_hints` 列出提示：用 `dialogue.set_timing` 把各句起点整体后移或加长时长；正文里开口前与末句后的
这段时间写可见动作和环境声，不让角色一开场就说话或说到最后一帧。这只是提醒，Core 不拒绝；用户坚持时照常保存与生成。
