## 实际视频输入边界

其他图片的规划、读取、生成与选图流程见项目提示（`save_reference_image_plan`、`video_inputs`、
`generate_media(kind="reference_image")`、`select_reference_image`）；只有用户明确要求时才生成，选入卡片后才进入视频输入。
从资产库直接绑定的图片可能没有生图计划，仍要按 `reference_inputs` 读取，不能因没有 `reference_image_plans` 就忽略它。

实际视频输入以 `video_inputs[].slots`、`require_any` 和 `reference_inputs` 为准：首尾槽位只提交已启用且已选的图，独立参考按当前 recipe 显式绑定。
FL2VA 至少需一个边界帧。Ref2VA 可关闭首尾帧，图片、音频与视频参考合计至少需要一项实际输入。切换 recipe 使用独立的视频命令；关键帧 proposal 只提交当前 recipe 支持的计划。
用于生图的角色造型、地点图片和辅助图只影响生图，不自动成为视频输入；独立参考不承诺精确的起止构图。
规划阶段不能声称已审看未生成的图；生成后实际画面若遗漏必要主体或空间，必须先审核修正该图或补绑参考图。

## 可见角色与参考约束

- 每张帧只列该静态时刻真正可见的角色 ID；未出现的角色不得为了“保持一致”而加入。
- Segment 的可见角色是覆盖 Shot 的并集；段内切换后仍可见的角色都可出现在首/尾帧。
- 共享边界时允许使用两侧 Segment 的角色并集，即使该角色在另一侧随后离开。
- 空镜返回空数组。
- 每张帧用 `start_character_references` / `end_character_references` 为可见角色选择本帧使用的造型：
  `[{character_id, look_id}]`，`look_id` 只能取自工作集 `visible_characters[].looks[].look_id`；`look_id: null`
  表示本帧不提交该角色的图。造型对应不同时期、服装或身体状态，按 `usage`（使用场合）与本帧所处剧情时刻选匹配的那一套。
- 角色只有一套造型时可以不列出该角色，Core 默认使用它；有多套造型时必须列出并选择，否则 Core 拒绝生成该帧。
  工作集同时给出造型的 `selected_asset_id` 与派生状态 `has_image`；需要核对角色外观时，用
  `read_media_input(asset_id=selected_asset_id)` 读取已选造型图。`selected_asset_id` 为 null（即 `has_image` 为 false）时
  该帧无法生成，提醒用户先为该造型设置造型图。
- Core 只提交所选造型的造型图，并在 `reference_mapping` 中标明角色与造型名；未列入可见角色的角色不得提交。
  角色造型图、前置帧与辅助参考合计最多 5 张输入图，多角色同框时尤其要精简选择。
- 用户在关键帧计划弹窗中选择的项目图片属于额外辅助参考，不替代角色卡身份参考。

## 参考其它已选关键帧

局部读取只列目标与已有引用。需要查找新的跨段帧或其它地点图片时，用 keyframe_context=scene 和 fact_keys 单独取 referenceable_keyframes 或 location_images。

- 工作集 `referenceable_keyframes` 列出当前查询关联的**已有选中图**的 Keyframe 索引（ID、所在 Scene/Segment/槽位、可见角色）。
  只有这些帧可被参考；只有候选、尚未选定的帧不能参考，因为最终选哪张未定。确实要参考某帧时，用
  `read_details(kind="keyframe", ids=[...])` 读取它的画面正文，不必为每个候选都读。
- 可选字段 `start_reference_keyframe_ids` / `end_reference_keyframe_ids`（各 0–4 个，不含本帧）让本帧生图时附带
  这些帧的**当前**选中图，作为“参考关键帧”输入，排在前置帧之后、辅助图之前。
- 用于跨 Segment 保持同一空间、人物状态、道具或光线：例如回到同一机位、同一房间的另一角度、换场后人物服装与伤痕延续。
  先用 `read_media_input` 审看被参考帧的实际图片，再在正文里写清该参考图要保留什么、改变什么；不要把参考当成复刻。
- 下游帧可在上游选定后用 `edit_keyframes` 的 `update_frame_fields.fields.reference_keyframe_ids` 写入，与本 proposal 是同一字段。
- 回切到画面与人物状态都没有变化的同一机位（例如多轮对话中重复出现的同一特写）时，后一帧不必重新设计：
  正文写成与被复用帧相同的画面，并设置 `start_reuse_keyframe_id` / `end_reuse_keyframe_id` 指向它（来源可尚未选图）。
  什么时候改为重新生成见 `slatehand-keyframe-sequence` 第 3 节。
- 被参考帧之后被改选时，下游已生成候选只带 `inputs_outdated` 软提示；被参考帧撤销选择时，下游帧在其重新选定前不能生成，批次会把它列为等待项。

## 地点语义与图片

Scene 可以绑定一个语义地点与状态；Shot 和 Keyframe 都不绑定地点。工作集 `location_library` 给出当前 Scene 使用的地点、
固定陈设、主光、状态和图片强制说明，`location_images` 列出当前查询关联的地点图片，`referenceable=true` 表示可被本帧参考。
要参考其它地点的图片时，先用 `read_details(kind="location_image", ids=[...])` 读取它的强制说明。
Planner 应把这些事实与 Scene setting、Shot 构图和动作一起用于写出自洽空间，不能假设地点图片的机位会被复刻。
地点是这一帧的背景事实，不是这一帧要展示的内容：不因地点里有门窗和家具就把它们放进画框，也不为保持地点一致而拉远。

- 地点图片默认只是资料。需要它的实际画面约束空间时，在 `start_reference_location_image_ids` /
  `end_reference_location_image_ids` 里列出其 `image_id`（各 0–4 个，只限 `referenceable=true` 的普通图片条目）；
  Core 以“地点参考”角色提交其当前选中图并附带图片说明。全景图不能直接参考，需要时请用户先取景为普通子图。
- 先用 `read_media_input` 审看图片并核对说明，在正文里写清这张图提供哪些空间、陈设或光线事实，以及本帧机位与它的差异；继承什么由本帧画框决定（见 `cinematic-frame-prompting.md` 第 1 节）。
- 只在本帧确实需要从它取得光线、材质或空间事实时才参考地点图片，和角色造型图、前置帧、参考关键帧共享 5 张名额。
- 没有地点图片、图片未选候选或没有合适视角时，仍应根据地点文字和镜头上下文完成画面设计，不把它报告为生成依赖缺口。
- 图片的强制 `description` 是理解实际画面的依据；`prompt_text` 只是历史生成意图，不能当成图片已实现的事实。
- Planner proposal 不新增或替换 Segment 辅助图；地点图片用上面的参考字段，`edit_keyframes` 的
  `update_frame_fields.fields.reference_location_image_ids` 写同一字段。
