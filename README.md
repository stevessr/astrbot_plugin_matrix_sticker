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
- `matrix_sticker_auto_sync`：自动同步房间 Sticker 包（默认 false）。
- `matrix_sticker_sync_user_emotes`：同步用户级别 Sticker 包（默认 false）。
- `emoji_shortcodes`：启用 Emoji 短码转换（对所有适配器生效，默认 false）。
- `emoji_shortcodes_strict_mode`：短码严格模式。开启后仅识别 `:shortcode:`；关闭后也识别 `:shortcode`（默认 false）。

# Warning
已知问题：此插件会和分段回复冲突！导致回复逃逸出嘟文串
