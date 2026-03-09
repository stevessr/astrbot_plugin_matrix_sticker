# 问：如何管理表情？

答：没写，请使用 matrix 客户端内建的部分，如 fluffychat 最新版本，然后让机器人加入对应的群组，重启 matrix 适配器，其会自动拉取并更新已有表情的

# 可以在其他平台使用吗？
看运气，但是给了表情搜索和路径获取函数……LLM 应该可以吧

# 问：matrix 平台如何批量上传表情？
答：我用的 fluffychat，自己加了一点特性 https://chat.aaca.eu.org/ (从 tar.gz 上传到分组)

# 问：向量检索怎么配置？
答：现在统一使用 `matrix_sticker_vector` 这个 object 配置，不再拆成多个 `matrix_sticker_embedding_*` / `matrix_sticker_vector_*` 字段。

示例：

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
    "query_image_enabled": true,
    "qdrant": {
      "url": "",
      "api_key": "",
      "collection": "matrix_sticker_vectors",
      "prefer_grpc": false,
      "timeout": 10
    }
  }
}
```

说明：
- `enabled`：是否启用向量检索。
- `backend`：向量索引后端类型；当前可选 `faiss` 或 `qdrant`。
- `model` / `dimensions`：内置 Vertex 多模态 embedding 模型与维度。
- `vertex_project` / `vertex_location`：Vertex AI 项目与区域；`vertex_project` 留空时会尝试通过 ADC 自动检测。
- `api_base` / `timeout` / `proxy`：请求地址覆盖、超时和代理配置。
- `top_k` / `fetch_k` / `similarity_threshold`：召回数量、最终返回数量和最小相似度阈值。
- `rebuild_on_startup` / `auto_reconcile` / `query_image_enabled`：控制启动重建、增量同步和图片查询能力。
- `qdrant`：Qdrant 后端连接配置；使用 `qdrant` backend 时需要至少设置 `url`，`collection` 默认为 `matrix_sticker_vectors`。
