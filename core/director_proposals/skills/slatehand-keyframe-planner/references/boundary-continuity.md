## 相邻 Segment 的连续边界

`continues_from_previous=true` 表示两边复用**同一张像素级关键帧**，不仅是剧情连续。只有摄影机位置、
景别、构图、人物姿态、道具和光线状态在交接的同一时刻完全相同，而且需要锁定这一共同画面时才设置。普通正反打、切镜或明确换机位使用 `false`。
一镜到底中的推近、绕行、拉远或景别变化发生在 Segment 内。要锁住交接时，先确定交接时刻的共同画面，再推演后续运动。
遵循 `required_shared_boundaries` 和 Director 明确要求共用同一张图的交接。

设置为 `true` 时，当前 Segment 的 `start_*` 字段必须为空：`start_prompt_text=""`、
`start_visible_character_ids=[]`、`start_character_references=[]`、`start_reference_keyframe_ids=[]`；Core 沿用上一 Segment 的 `end_*`。
每个 Scene 的第一个 Segment 必须为 `false`。

明确切镜时，`continues_from_previous=false`。后段确需锁定开场画面时，才启用独立首帧并填写 `start_*` 描述。

当 Core 提供 `required_shared_boundaries` 时，表示 Director 已把本轮采用的连续镜头方案解析成必须共用的
时间边界，列出的是相邻 Segment 对。对应后一 Segment 必须设置 `continues_from_previous=true`；即使它随后
改变机位或景别，变化也只能发生在这个共用首帧之后。不得把该硬约束降级为仅保持人物、道具、光线或轴线状态相似。

共享同一图片只约束交接画面，不保证运动自动无缝；还要沿原脚本核对动作阶段、运动方向和摄影机运动的衔接。

### 同一 Shot 拆段：延续段不重新设计

工作集 `scenes[].segments[]` 的 `continues_from_previous=true` 表示该 Segment 与上一段覆盖同一个边界 Shot，
`boundary_reason` 是脚本记录的拆段原因。`model_constraint` 表示只因模型时长或对白条数上限把同一个镜头切成几段：
机位、景别、构图、空间与光线都不变，人物的表情、视线、手势和小幅走位是视频要演的过程，不是后段首帧需要
重新设计的理由。不要按后段的对白语气为它的首帧另编一个表演瞬间，
否则同一个镜头会在拼接处换成另一张独立生成的画面，背景、陈设和光线随之漂移。也不能借后段首帧改写
已批准的景别或机位（例如把固定双人中景改成单人反打近景）。

确需启用后段首帧时，按“共享 → 抽帧 → 复用 → 参考重生成”的顺序取来源。抽帧规则见下一小节：

- **建议复用**：不便抽帧、或用户想先用静帧排完整段时，把 `start_reuse_keyframe_id` 设为同一 Shot 中最近一张
  已规划的帧（通常是上一段首帧），来源可以尚未选图；正文写成与来源相同的画面。批量生成跳过本帧并等待来源选定，
  之后用 `select_keyframe_candidate(source="keyframe")` 选入同一张图。
- **参考来源重新生成**：只有脚本在这个边界明确写了大位移、换机位或景别变化，才独立设计首帧；来源已选图时写进
  `start_reference_keyframe_ids`，正文写清保留什么、改变什么。

尾帧同理：同一镜头内的尾帧与本段首帧或同 Shot 其它帧画面相同时，用 `end_reuse_keyframe_id` 指向来源。
同一帧的抽帧与复用只能二选一。已有计划要补设或取消复用建议时，用 `edit_keyframes` 的
`update_frame_fields.fields.reuse_keyframe_id`（`null` 取消），不必重新规划。

#### 建议抽帧

相邻 Segment 连续且后段确需锁定首帧时，如果上一段没有可共享的尾帧，用上一段视频的实际画面承接手势、视线、光线与道具。按交接时刻写 `start_prompt_text` 作为审核依据，并设置
`start_extraction_suggested=true`：关键帧卡显示“建议抽帧”，批量生成跳过这一帧。在回复里说明
这一帧等上一段视频选定后抽帧（人类用首帧卡“从上一 seg 抽取”，Agent 用 `generate_media(kind="frame_extraction")`）。这只是建议，用户要求直接生成时照常生成。已有计划要补标或取消标记时，用
`edit_keyframes` 的 `update_frame_fields.fields.extraction_suggested`，不必重新规划。
