"""
模块：app/infrastructure/storage/oss_client.py
职责：阿里云 OSS 底层客户端（纯 httpx + OSS Signature V1，不依赖 oss2 SDK）。

标签：[架构组] [P1]（被 adapters/storage/oss_adapter.py 复用）

============================ 实现思路与代码要点 ============================
职责边界：
    本文件 -> 与「阿里云 OSS」打交道（签名、PUT / GET / HEAD / DELETE、预签名 URL）
    adapters/storage/oss_adapter.py -> 包装成统一 BaseStorage
    services/file_service.py -> 业务规则（校验、元数据、权限）

签名规则（OSS Signature V1）：
    StringToSign = VERB + "\\n" + Content-MD5 + "\\n" + Content-Type + "\\n"
                   + Date + "\\n" + CanonicalizedOSSHeaders + CanonicalizedResource
    Authorization: OSS {AccessKeyId}:{base64(hmac_sha1(AccessKeySecret, StringToSign))}
    - Date 使用 RFC 1123 GMT；CanonicalizedResource 为 /{bucket}/{key}；
    - 预签名 URL：Expires 为 Unix 时间戳，Date 位置填 Expires；
    - 采用 virtual-hosted 风格：https://{bucket}.{endpoint}/{key}。
错误统一映射为平台异常：404 -> FileError，401/403/5xx -> ExternalServiceError。
=========================================================================
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.core.exceptions import ExternalServiceError, FileError

logger = logging.getLogger(__name__)

_XML_CODE_RE = re.compile(r"<Code>([^<]+)</Code>")
_XML_MESSAGE_RE = re.compile(r"<Message>([^<]+)</Message>")
_URI_SAFE = "-_.~"


def _utcnow() -> datetime:
    """当前 UTC 时间（单独函数便于测试替换）。"""
    return datetime.now(timezone.utc)


def _encode(value: str) -> str:
    return quote(str(value), safe=_URI_SAFE)


def _encode_key(key: str) -> str:
    return "/".join(_encode(segment) for segment in str(key or "").split("/") if segment)


def normalize_endpoint(endpoint: str, secure: bool = True) -> str:
    """补全协议并去掉尾部斜杠；仅保留 host（OSS 用虚拟主机方式访问）。"""
    value = str(endpoint or "").strip().rstrip("/")
    if not value:
        raise ExternalServiceError("OSS endpoint 未配置")
    if "://" not in value:
        value = f"{'https' if secure else 'http'}://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ExternalServiceError(f"OSS endpoint 不合法：{endpoint}")
    return f"{parsed.scheme}://{parsed.netloc}"


class OSSClient:
    """阿里云 OSS 底层客户端（Signature V1）。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key_id: str,
        access_key_secret: str,
        bucket: str,
        secure: bool = True,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not access_key_id or not access_key_secret:
            raise ExternalServiceError("OSS 访问密钥未配置（OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET）")
        if not bucket:
            raise ExternalServiceError("OSS bucket 未配置")
        self.endpoint = normalize_endpoint(endpoint, secure)
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.bucket = bucket
        self.timeout = float(timeout)
        self._transport = transport
        parsed = urlparse(self.endpoint)
        self._scheme = parsed.scheme
        self._netloc = parsed.netloc
        self._host = f"{self.bucket}.{self._netloc}"

    @property
    def scheme(self) -> str:
        """访问协议（http / https），供自检展示。"""
        return self._scheme

    # ---------------------------------------------------------------- 地址
    def _url(self, key: str = "") -> str:
        encoded_key = _encode_key(key)
        path = f"/{encoded_key}" if encoded_key else "/"
        return f"{self._scheme}://{self._host}{path}"

    def object_url(self, key: str) -> str:
        """对象直链（公共读 / CDN 场景使用，不含签名）。"""
        return self._url(key)

    # ---------------------------------------------------------------- 签名
    def _signature(
        self,
        method: str,
        *,
        content_type: str = "",
        date: str,
        key: str,
        oss_headers: dict[str, str] | None = None,
    ) -> str:
        canonical_headers = ""
        if oss_headers:
            canonical_headers = "".join(
                f"{name.lower()}:{str(value).strip()}\n" for name, value in sorted(oss_headers.items())
            )
        resource = f"/{self.bucket}/{key}" if key else f"/{self.bucket}/"
        string_to_sign = "\n".join(
            [method.upper(), "", content_type or "", date, f"{canonical_headers}{resource}"]
        )
        digest = hmac.new(
            self.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _headers(
        self,
        method: str,
        key: str,
        *,
        content_type: str = "",
        now: datetime | None = None,
    ) -> dict[str, str]:
        moment = (now or _utcnow()).astimezone(timezone.utc)
        # OSS 要求 RFC 1123 GMT 格式（.timestamp() 的 +0000 写法可能被拒绝）
        date = format_datetime(moment, usegmt=True)
        signature = self._signature(method, content_type=content_type, date=date, key=key)
        headers = {
            "Date": date,
            "Authorization": f"OSS {self.access_key_id}:{signature}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def presigned_url(
        self,
        key: str,
        expires_seconds: int = 3600,
        *,
        method: str = "GET",
        now: datetime | None = None,
    ) -> str:
        """生成临时签名地址（默认 1 小时有效）。"""
        moment = now or _utcnow()
        expires = int((moment + timedelta(seconds=max(1, int(expires_seconds)))).timestamp())
        signature = self._signature(method, date=str(expires), key=key)
        query = "&".join(
            [
                f"OSSAccessKeyId={_encode(self.access_key_id)}",
                f"Expires={expires}",
                f"Signature={_encode(signature)}",
            ]
        )
        return f"{self._url(key)}?{query}"

    # ---------------------------------------------------------------- HTTP
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def _request(
        self,
        method: str,
        key: str = "",
        *,
        data: bytes | None = None,
        content_type: str = "",
    ) -> httpx.Response:
        headers = self._headers(method, key, content_type=content_type)
        async with self._client() as client:
            try:
                return await client.request(
                    method, self._url(key), headers=headers, content=data
                )
            except httpx.HTTPError as exc:
                raise ExternalServiceError(f"OSS 连接失败（{self.endpoint}）：{exc}") from exc

    # ---------------------------------------------------------------- 对象操作
    async def put_object(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "PUT", key, data=data, content_type=content_type or ""
        )
        self._raise_for_status("PUT", key, response)
        return {
            "key": key,
            "size": len(data),
            "etag": response.headers.get("etag", "").strip('"'),
            "status": response.status_code,
        }

    async def get_object(self, key: str) -> bytes:
        response = await self._request("GET", key)
        self._raise_for_status("GET", key, response)
        return response.content

    async def head_object(self, key: str) -> dict[str, Any] | None:
        response = await self._request("HEAD", key)
        if response.status_code == 404:
            return None
        self._raise_for_status("HEAD", key, response)
        length = response.headers.get("content-length")
        return {
            "key": key,
            "size": int(length) if length and length.isdigit() else 0,
            "etag": response.headers.get("etag", "").strip('"'),
            "content_type": response.headers.get("content-type", ""),
        }

    async def delete_object(self, key: str) -> bool:
        response = await self._request("DELETE", key)
        if response.status_code == 404:
            return False
        self._raise_for_status("DELETE", key, response)
        return response.status_code in (200, 204)

    # ---------------------------------------------------------------- 存储桶
    async def bucket_info(self) -> dict[str, Any]:
        response = await self._request("HEAD", "")
        if response.status_code == 404:
            return {"ok": False, "reason": "bucket_not_found", "bucket": self.bucket}
        self._raise_for_status("HEAD", self.bucket, response)
        return {"ok": True, "bucket": self.bucket, "status": response.status_code}

    async def health(self) -> dict[str, Any]:
        """连通性与 bucket 自检（不抛异常）。"""
        try:
            info = await self.bucket_info()
        except Exception as exc:  # noqa: BLE001 - 自检接口返回结构化结果
            return {
                "ok": False,
                "backend": "oss",
                "endpoint": self.endpoint,
                "bucket": self.bucket,
                "reason": str(exc),
            }
        return {
            "ok": bool(info.get("ok")),
            "backend": "oss",
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            **{k: v for k, v in info.items() if k != "ok"},
        }

    # ---------------------------------------------------------------- 错误映射
    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        body = response.text[:500]
        code = _XML_CODE_RE.search(body)
        message = _XML_MESSAGE_RE.search(body)
        if code or message:
            return " ".join(
                filter(None, [code.group(1) if code else "", message.group(1) if message else ""])
            )
        return body or response.reason_phrase

    def _raise_for_status(self, action: str, key: str, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = self._error_detail(response)
        if response.status_code == 404:
            if "NoSuchBucket" in detail:
                raise ExternalServiceError(
                    f"OSS 存储桶不存在：{self.bucket}",
                    data={"bucket": self.bucket},
                )
            raise FileError(f"对象不存在：{key}")
        if response.status_code in (401, 403):
            raise ExternalServiceError(
                f"OSS 鉴权失败（{action} {key}，HTTP {response.status_code}）：{detail}",
                data={"status": response.status_code, "bucket": self.bucket},
            )
        raise ExternalServiceError(
            f"OSS 调用失败（{action} {key}，HTTP {response.status_code}）：{detail}",
            data={"status": response.status_code, "bucket": self.bucket},
        )


__all__ = ["OSSClient", "normalize_endpoint"]
