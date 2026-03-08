import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

import google.auth
import httpx
from google.auth.transport.requests import Request

from astrbot.core.provider.provider import EmbeddingProvider

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
SUPPORTED_DIMENSIONS = {128, 256, 512, 1408}


class VertexMultimodalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.project = str(provider_config.get("vertex_project", "") or "").strip()
        self.location = str(
            provider_config.get("vertex_location", "us-central1") or "us-central1"
        ).strip()
        self.model = str(
            provider_config.get("embedding_model", "multimodalembedding@001")
            or "multimodalembedding@001"
        ).strip()
        self._dimension = int(provider_config.get("embedding_dimensions", 1408))
        if self._dimension not in SUPPORTED_DIMENSIONS:
            raise ValueError(
                f"Vertex 多模态 embedding 仅支持以下维度：{sorted(SUPPORTED_DIMENSIONS)}"
            )
        api_base = str(provider_config.get("embedding_api_base", "") or "").strip()
        if not api_base:
            api_base = f"https://{self.location}-aiplatform.googleapis.com"
        self.api_base = api_base.rstrip("/")
        timeout = int(provider_config.get("timeout", 20))
        proxy = str(provider_config.get("proxy", "") or "").strip()
        client_kwargs: dict[str, Any] = {"timeout": timeout}
        if proxy:
            client_kwargs["proxy"] = proxy
        self.client = httpx.AsyncClient(**client_kwargs)
        self._credentials = None
        self._token_request = Request()

    def supports_image_embedding(self) -> bool:
        return True

    def get_dim(self) -> int:
        return self._dimension

    async def get_embedding(self, text: str) -> list[float]:
        predictions = await self._predict([{"text": text}])
        return self._extract_embedding(predictions[0], "textEmbedding")

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        if not text:
            return []
        predictions = await self._predict([{"text": item} for item in text])
        return [self._extract_embedding(item, "textEmbedding") for item in predictions]

    async def get_image_embedding(self, image_path: str) -> list[float]:
        predictions = await self._predict([self._build_image_instance(image_path)])
        return self._extract_embedding(predictions[0], "imageEmbedding")

    async def get_image_embeddings(self, image_paths: list[str]) -> list[list[float]]:
        if not image_paths:
            return []
        predictions = await self._predict(
            [self._build_image_instance(path) for path in image_paths]
        )
        return [self._extract_embedding(item, "imageEmbedding") for item in predictions]

    async def terminate(self):
        await self.client.aclose()

    def _build_endpoint_path(self) -> str:
        project = self.project.strip()
        if not project:
            raise ValueError(
                "Sticker 向量检索缺少 vertex_project，请在插件配置中填写，或确保 ADC 可自动检测项目。"
            )
        return (
            f"/v1/projects/{project}/locations/{self.location}"
            f"/publishers/google/models/{self.model}:predict"
        )

    def _build_image_instance(self, image_path: str) -> dict[str, Any]:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在：{image_path}")
        mime_type, _ = mimetypes.guess_type(str(path))
        if not mime_type:
            mime_type = "application/octet-stream"
        return {
            "image": {
                "bytesBase64Encoded": base64.b64encode(path.read_bytes()).decode(
                    "utf-8"
                ),
                "mimeType": mime_type,
            }
        }

    def _extract_embedding(self, prediction: dict[str, Any], key: str) -> list[float]:
        embedding = prediction.get(key)
        if isinstance(embedding, dict):
            values = embedding.get("values")
        else:
            values = embedding
        if not isinstance(values, list):
            raise ValueError(f"Vertex 返回中缺少 {key}")
        return [float(value) for value in values]

    async def _predict(self, instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
        token = await self._get_access_token()
        payload = {
            "instances": instances,
            "parameters": {"dimension": self.get_dim()},
        }
        response = await self.client.post(
            f"{self.api_base}{self._build_endpoint_path()}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        predictions = data.get("predictions")
        if not isinstance(predictions, list):
            raise ValueError("Vertex 返回中缺少 predictions")
        return predictions

    async def _get_access_token(self) -> str:
        return await asyncio.to_thread(self._refresh_access_token_sync)

    def _refresh_access_token_sync(self) -> str:
        if self._credentials is None:
            credentials, detected_project = google.auth.default(
                scopes=[CLOUD_PLATFORM_SCOPE]
            )
            self._credentials = credentials
            if not self.project and detected_project:
                self.project = str(detected_project)
        self._credentials.refresh(self._token_request)
        token = getattr(self._credentials, "token", None)
        if not token:
            raise ValueError("无法获取 Vertex 访问令牌")
        return str(token)
