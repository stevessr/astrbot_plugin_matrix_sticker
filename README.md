# AstrBot Matrix Sticker Plugin

Matrix Sticker 管理插件，提供 sticker 保存、列表、发送与别名管理等命令。

## 依赖

- `astrbot_plugin_matrix_adapter`

## 命令概览

### /sticker

- `help` 显示帮助
- `list [pack]` 列出 sticker
- `packs` 列出 sticker 包
- `save <name> [pack]` 保存引用的图片为 sticker
- `send <id|name>` 发送 sticker
- `delete <id>` 删除 sticker
- `stats` 显示统计
- `sync` 同步房间 sticker
- `mode <inject|fc|hybrid>` 切换 LLM 模式（运行时注入 / FC 工具 / 混合）
- `addroom <shortcode> [state_key]` 将引用图片添加为房间表情
- `removeroom <shortcode> [state_key]` 删除房间表情
- `roomlist [state_key]` 列出房间表情

### /sticker_alias

- `add <sticker_id> <alias>` 添加别名
- `remove <sticker_id> <alias>` 删除别名
- `list <sticker_id>` 列出别名

## 使用示例

```text
/sticker list
/sticker packs
/sticker save hello
/sticker send hello
/sticker delete 12
/sticker stats
/sticker sync
/sticker mode fc
/sticker addroom party
/sticker removeroom party
/sticker roomlist

/sticker_alias add 12 hi
/sticker_alias list 12
```

## 说明

- 保存/添加房间表情通常需要引用一条图片消息。
- 插件会在 LLM 处理阶段注入/替换 sticker 短码。
- Sticker 自动同步与 Emoji 短码转换能力均由本插件统一负责。

## 配置

- `matrix_sticker_max_per_reply`：单次回复最多发送的 sticker 数量，<= 0 表示不限制（默认 5）。
- `matrix_sticker_full_intercept`：完全拦截回复并按 :shortcode: 分段发送，短码会转为 sticker；需要 Matrix 适配器开启流式发送禁用编辑（默认 false）。
- `matrix_sticker_enable_other_platforms`：在非 Matrix 平台启用 sticker 扩展。开启后会注入短码到提示词，并将命中的 `:shortcode:` 转为图片组件发送（默认 false）。
- `matrix_sticker_llm_mode`：Sticker 的 LLM 模式。`inject`（默认）为运行时注入短码并替换；`fc` 为函数工具模式；`hybrid` 为两者同时启用。
- `matrix_sticker_auto_sync`：自动同步房间 Sticker 包（默认 false）。
- `matrix_sticker_sync_user_emotes`：同步用户级别 Sticker 包（默认 false）。
- `emoji_shortcodes`：启用 Emoji 短码转换（对所有适配器生效，默认 false）。
- `emoji_shortcodes_strict_mode`：短码严格模式。开启后仅识别 `:shortcode:`；关闭后也识别 `:shortcode`（默认 false）。

### FC 工具

- `sticker_search`：高级搜索 Sticker（支持关键字、标签、包名、匹配模式、排序、分页、作用域）。
- `sticker_send`：通过 `sticker_id` 或 `shortcode` 发送 Sticker（非 Matrix 平台会自动转图片组件发送）。

# Warning
已知问题：此插件会和分段回复冲突！导致回复逃逸出嘟文串
