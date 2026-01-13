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

## 配置

- `matrix_sticker_max_per_reply`：单次回复最多发送的 sticker 数量，<= 0 表示不限制（默认 5）。
