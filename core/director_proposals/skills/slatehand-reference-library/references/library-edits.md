# 修改已建立的引用库

引用库建立后，Agent 与用户拥有同样的编辑权。所有修改都用 `edit_references` 一次原子提交（字段形状见工具 schema）：
操作按顺序执行，任一失败整批不写入；同一 patch 新建的角色卡与造型用局部 `key` 互相引用，回执 `data.created_ids`
给出正式 ID。用户要求的改动直接执行，完成后在回复里说明改了什么、清除了哪些下游选择。字段写法同
[card-contract.md](card-contract.md) 与 [voice-descriptions.md](voice-descriptions.md)。

- **角色卡**：`add_character` 的出镜角色至少带一套造型；非出镜声音 `visual_description` 为 null、无造型、`voice_description` 必填。
  改成非出镜声音前须在同一 patch 先删光造型，且它不能是任何 Shot 或 Keyframe 的可见角色。`delete_character` 在脚本仍有
  Shot 可见角色或对白说话者引用时被拒绝，错误列出位置，先用 `edit_script` 移除；Keyframe 中对它的选择自动剪除。
- **造型**：`update_look` 不作废已选造型图，外观变化大时应重新生成并选定或取消造型图；`delete_look` 会剪除 Keyframe
  对它的选择，相关帧重新选择造型前不能生成。
- **造型图**：`select_look_image` 的 `generation` 只接受该造型成功、未隐藏的设定图候选，`asset` 接受任一项目图片；
  选前用 `read_media_input` 看图，确认身份与造型一致。生成见 [look-sheets.md](look-sheets.md)。
- **参考音色**：先用 `generate_media` `kind: "voice"` 设计试听（角色卡 ID 不带 `language`；旁白身份 ID 带 `zh` / `en`），
  `wait` 完成后用 `select_voice` 采用：`generation` 的描述与文字稿随候选写入；`asset` 采用任一项目 WAV，必须给出与录音
  逐字一致的完整 `transcript`。`clear_voice` 回到按描述 Voice Design。
- **旁白身份**：`update_narrator` 修改名称或已启用语言的音色描述与文字稿。

角色实际进入对白 TTS 的音色输入（参考 WAV + 文字稿，或无参考时的描述）一变，其所在 Segment 的对白音频选择、发声时序与
视频选择都会被清除，回执 `invalidated` 列出；已有选定对白时先确认用户接受重新合成。
