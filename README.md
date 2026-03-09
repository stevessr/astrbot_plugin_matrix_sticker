# AstrBot Matrix Sticker Plugin

Matrix Sticker 管理插件，提供 sticker 保存、列表、发送与别名管理等命令。

## 依赖

- `astrbot_plugin_matrix_adapter`
- `qdrant-client>=1.14.2`（仅在使用 `qdrant` 向量后端时需要；已写入插件 `requirements.txt`）

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
- `reindex` 重建向量索引
- `mode <on|off>` 开关 LLM 提示词注入（工具启停请在 WebUI 管理）
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
/sticker reindex
/sticker mode off
/sticker addroom party
/sticker removeroom party
/sticker roomlist

/sticker_alias add 12 hi
/sticker_alias list 12
```

## 说明

- 保存/添加房间表情通常需要引用一条图片消息。
- 插件可在 LLM 处理阶段注入/替换 sticker 短码（可通过 `mode` 与配置开关控制提示词注入）。
- Sticker 自动同步与 Emoji 短码转换能力均由本插件统一负责。

## 配置

- `matrix_sticker_max_per_reply`：单次回复最多发送的 sticker 数量，<= 0 表示不限制（默认 5）。
- `matrix_sticker_full_intercept`：完全拦截回复并按 :shortcode: 分段发送，短码会转为 sticker；需要 Matrix 适配器开启流式发送禁用编辑（默认 false）。
- `matrix_sticker_enable_other_platforms`：在非 Matrix 平台启用 sticker 扩展。开启后会注入短码到提示词，并将命中的 `:shortcode:` 转为图片组件发送（默认 false）。
- `matrix_sticker_prompt_injection`：是否向 LLM 提示词注入可用 sticker 短码（默认 true）。
- `matrix_sticker_index_reload_interval_seconds`：索引自动刷新最小间隔（秒，默认 3）。设置为 0 可在每次请求都强制刷新（性能开销更高）。
- `matrix_sticker_auto_sync`：自动同步房间 Sticker 包（默认 false）。
- `matrix_sticker_sync_user_emotes`：同步用户级别 Sticker 包（默认 false）。
- `matrix_sticker_vector`：Sticker 向量检索配置对象。启用后会优先使用插件内置的 Vertex 多模态 embedding 做文本/图片检索，模型必须支持 text/image 共享向量空间。
- `emoji_shortcodes`：启用 Emoji 短码转换（对所有适配器生效，默认 false）。
- `emoji_shortcodes_strict_mode`：短码严格模式。开启后仅识别 `:shortcode:`；关闭后也识别 `:shortcode`（默认 false）。

### 向量配置示例

#### 使用本地默认 `faiss` 后端

```json
{
  "matrix_sticker_vector": {
    "enabled": true,
    "backend": "faiss",
    "model": "multimodalembedding@001",
    "dimensions": 512,
    "vertex_project": "your-gcp-project",
    "vertex_location": "asia-east1",
    "api_base": "",
    "timeout": 20,
    "proxy": "",
    "top_k": 10,
    "fetch_k": 50,
    "similarity_threshold": 0.35,
    "rebuild_on_startup": false,
    "auto_reconcile": true,
    "query_image_enabled": true
  }
}
```

#### 使用远端 `qdrant` 后端

```json
{
  "matrix_sticker_vector": {
    "enabled": true,
    "backend": "qdrant",
    "model": "multimodalembedding@001",
    "dimensions": 512,
    "vertex_project": "your-gcp-project",
    "vertex_location": "asia-east1",
    "api_base": "",
    "timeout": 20,
    "proxy": "",
    "top_k": 10,
    "fetch_k": 50,
    "similarity_threshold": 0.35,
    "rebuild_on_startup": false,
    "auto_reconcile": true,
    "query_image_enabled": true,
    "qdrant": {
      "url": "http://127.0.0.1:6333",
      "api_key": "",
      "collection": "matrix_sticker_vectors",
      "prefer_grpc": false,
      "timeout": 10
    }
  }
}
```

- `enabled`：是否启用向量检索。
- `backend`：向量索引后端类型；当前可选 `faiss` 或 `qdrant`。
- `model` / `dimensions`：插件内置 Vertex 多模态 embedding 模型与维度，文本和图片查询都会走这一套向量模型。
- `vertex_project` / `vertex_location`：Vertex AI 项目与区域；`vertex_project` 留空时会尝试通过 ADC 自动检测。
- `api_base` / `timeout` / `proxy`：Vertex 请求地址覆盖、超时和代理配置。
- `top_k` / `fetch_k` / `similarity_threshold`：召回数量、最终返回数量和最小相似度阈值。
- `rebuild_on_startup` / `auto_reconcile` / `query_image_enabled`：控制启动重建、增量同步和图片查询能力。
- `qdrant.url`：Qdrant 服务地址；使用 `qdrant` backend 时必填。
- `qdrant.api_key`：Qdrant 鉴权密钥；服务启用鉴权时填写。
- `qdrant.collection`：Qdrant Collection 名称，默认 `matrix_sticker_vectors`。
- `qdrant.prefer_grpc`：是否优先使用 gRPC 连接。
- `qdrant.timeout`：Qdrant 请求超时秒数。

### 向量后端选择建议

- `faiss`：默认后端，索引文件保存在插件本地目录，部署最简单，适合单机或小规模使用。
- `qdrant`：索引存放在独立 Qdrant 服务中，适合想把向量库独立出去、或需要更方便迁移/持久化的场景。

### 使用 Qdrant 前的准备

1. 确保插件依赖已安装，至少包含 `qdrant-client>=1.14.2`。
2. 准备好可访问的 Qdrant 服务地址，并填写 `matrix_sticker_vector.qdrant.url`。
3. 将 `matrix_sticker_vector.backend` 切换为 `qdrant`。
4. 执行一次 `/sticker reindex`，将现有 sticker 全量写入 Qdrant。

### 常见排错

- 如果看到 `backend_initialize_failed:qdrant`，通常表示：
  - 没安装 `qdrant-client`；或
  - `qdrant.url` 未配置；或
  - Qdrant 服务不可达。
- 如果切换了 `backend`、`collection`、`dimensions` 或 Vertex 模型，建议重新执行 `/sticker reindex`。
- `qdrant` 后端只负责向量存储；查询向量仍然由插件内置 Vertex provider 生成。

### FC 工具

- `sticker_search`：高级搜索 Sticker（支持关键字、标签、包名、匹配模式、排序、分页、作用域）。结果包含本地物理路径与是否存在。
- `sticker_send`：通过 `sticker_id` 或 `shortcode` 发送 Sticker（非 Matrix 平台会自动转图片组件发送）。
- 工具默认启用；如需停用，请在 WebUI 的工具管理页面手动操作。

# Warning
已知问题：此插件会和分段回复冲突！导致回复逃逸出嘟文串
