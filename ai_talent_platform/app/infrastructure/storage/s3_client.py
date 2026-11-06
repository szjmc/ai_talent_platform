"""
模块：app/infrastructure/storage/s3_client.py
职责：S3 兼容对象存储底层客户端（MinIO / AWS S3 / 其他 S3 网关），纯 httpx + SigV4 签名。

标签：[架构组] [P1]（被 adapters/storage/{minio,s3}_adapter.py 复用）

============================ 实现思路与代码要点 ============================
职责边界：
    本文件 -> 与「S3 协议」打交道（签名、PUT / GET / HEAD / DELETE、预签名 URL）
    adapters/storage/minio_adapter.py / s3_storage_adapter.py -> 包装成统一 BaseStorage
    services/file_service.py -> 业务规则（校验、元数据、权限）

关键实现：
- 不依赖 minio / boto3 SDK，只用 httpx，私有化部署依赖更少、行为更可控；
- AWS Signature V4：canonical request -> string to sign -> signing key -> Authorization；
  PUT 使用真实 payload 的 sha256，GET/HEAD/DELETE 使用空串 sha256；
- 支持 path-style（MinIO 默认）与 virtual-hosted-style（AWS S3 默认）；
- 预签名 URL 使用 query 签名（X-Amz-* + UNSIGNED-PAYLOAD），供前端直传 / 直链下载；
- 错误统一映射为平台异常：404 -> FileError，401/403/5xx -> ExternalServiceError；
- 时间来自 _utcnow()，测试可替换以获得确定性签名。
=========================================================================
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.core.exceptions import ExternalServiceError, FileError

logger = logging.getLogger(__name__)

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "s3"
_XML_CODE_RE = re.compile(r"<Code>([^<]+)</Code>")
_XML_MESSAGE_RE = re.compile(r"<Message>([^<]+)</Message>")

# 可信的相对路径字符（AWS SigV4 要求）
_URI_SAFE = "-_.~"


def _utcnow() -> datetime:
    """当前 UTC 时间（单独函数便于测试替换）。"""
    return datetime.now(timezone.utc)


def _encode(value: str) -> str:
    return quote(str(value), safe=_URI_SAFE)


def _canonical_query(params: dict[str, str] | None) -> str:
    if not params:
        return ""
    return "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(params.items()))


def _encode_key(key: str) -> str:
    """对象键按段编码（保留 / 分隔符）。"""
    return "/".join(_encode(segment) for segment in str(key or "").split("/") if segment)


def normalize_endpoint(endpoint: str, secure: bool = True) -> str:
    """补全协议并去掉尾部斜杠：`127.0.0.1:9000` -> `http://127.0.0.1:9000`。"""
    value = str(endpoint or "").strip().rstrip("/")
    if not value:
        raise ExternalServiceError("S3 / MinIO endpoint 未配置")
    if "://" not in value:
        value = f"{'https' if secure else 'http'}://{value}"
    return value


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str, service: str = _SERVICE) -> bytes:
    k_date = _sign(f"AWS4{secret}".encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


class S3Client:
    """S3 协议底层客户端（MinIO / AWS S3 通用）。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        secure: bool = True,
        path_style: bool = True,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not access_key or not secret_key:
            raise ExternalServiceError("S3 / MinIO 访问密钥未配置（access_key / secret_key）")
        if not bucket:
            raise ExternalServiceError("S3 / MinIO bucket 未配置")
        self.endpoint = normalize_endpoint(endpoint, secure)
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region or "us-east-1"
        self.path_style = bool(path_style)
        self.timeout = float(timeout)
        self._transport = transport
        parsed = urlparse(self.endpoint)
        self._scheme = parsed.scheme
        self._netloc = parsed.netloc
        self._base_path = parsed.path.rstrip("/")

    # ---------------------------------------------------------------- 地址
    def _host(self) -> str:
        return self._netloc if self.path_style else f"{self.bucket}.{self._netloc}"

    def _path(self, key: str) -> str:
        """返回已编码的 URL 路径（含 bucket 或虚拟主机前缀）。"""
        encoded_key = _encode_key(key)
        if self.path_style:
            path = f"/{self.bucket}"
            if encoded_key:
                path += f"/{encoded_key}"
        else:
            path = f"/{encoded_key}" if encoded_key else "/"
        return f"{self._base_path}{path}"

    def _url(self, key: str = "") -> str:
        return f"{self._scheme}://{self._host()}{self._path(key)}"

    def object_url(self, key: str) -> str:
        """对象直链（公共读 / CDN 场景使用，不含签名）。"""
        return self._url(key)

    # ---------------------------------------------------------------- 签名
    def _signed_headers(
        self,
        method: str,
        path: str,
        payload_hash: str,
        *,
        content_type: str | None = None,
        now: datetime | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """生成带 Authorization 的请求头（SigV4）。"""
        moment = now or _utcnow()
        amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = moment.strftime("%Y%m%d")

        headers: dict[str, str] = {
            "host": self._host(),
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if content_type:
            headers["content-type"] = content_type

        canonical_headers = "".join(
            f"{name}:{str(value).strip()}\n" for name, value in sorted(headers.items())
        )
        signed_header_names = ";".join(sorted(headers))
        canonical_request = "\n".join(
            [
                method.upper(),
                path,
                _canonical_query(query),
                canonical_headers,
                signed_header_names,
                payload_hash,
            ]
        )
        scope = f"{date_stamp}/{self.region}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join(
            [
                _ALGORITHM,
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _signing_key(self.secret_key, date_stamp, self.region)
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return {
            "Host": headers["host"],
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            **({"Content-Type": content_type} if content_type else {}),
            "Authorization": (
                f"{_ALGORITHM} Credential={self.access_key}/{scope}, "
                f"SignedHeaders={signed_header_names}, Signature={signature}"
            ),
        }

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
        amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = moment.strftime("%Y%m%d")
        scope = f"{date_stamp}/{self.region}/{_SERVICE}/aws4_request"
        path = self._path(key)
        host = self._host()

        params = {
            "X-Amz-Algorithm": _ALGORITHM,
            "X-Amz-Credential": f"{self.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(max(1, int(expires_seconds))),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_request = "\n".join(
            [
                method.upper(),
                path,
                _canonical_query(params),
                f"host:{host}\n",
                "host",
                UNSIGNED_PAYLOAD,
            ]
        )
        string_to_sign = "\n".join(
            [
                _ALGORITHM,
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _signing_key(self.secret_key, date_stamp, self.region)
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        params["X-Amz-Signature"] = signature
        return f"{self._scheme}://{host}{path}?{_canonical_query(params)}"

    # ---------------------------------------------------------------- HTTP
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def _request(
        self,
        method: str,
        key: str = "",
        *,
        data: bytes | None = None,
        content_type: str | None = None,
        query: dict[str, str] | None = None,
    ) -> httpx.Response:
        payload_hash = hashlib.sha256(data).hexdigest() if data is not None else EMPTY_SHA256
        path = self._path(key)
        headers = self._signed_headers(
            method, path, payload_hash, content_type=content_type, query=query
        )
        url = self._url(key)
        if query:
            url = f"{url}?{_canonical_query(query)}"
        import asyncio
        # GET/HEAD/PUT固定对象键/DELETE均可幂等重试；不重试认证和参数错误。
        for attempt in range(3):
            try:
                async with self._client() as client:
                    response = await client.request(method, url, headers=headers, content=data)
                if response.status_code not in {408,429,500,502,503,504} or attempt == 2:
                    return response
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise ExternalServiceError('对象存储连接失败，已重试3次') from exc
            await asyncio.sleep(0.5 * (2 ** attempt))

    # ---------------------------------------------------------------- 对象操作
    async def put_object(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request("PUT", key, data=data, content_type=content_type)
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
        """HEAD bucket：返回存在性 / 可访问性（不抛 404）。"""
        payload_hash = EMPTY_SHA256
        path = self._path("")
        headers = self._signed_headers("HEAD", path, payload_hash)
        async with self._client() as client:
            try:
                response = await client.request("HEAD", self._url(""), headers=headers)
            except httpx.HTTPError as exc:
                raise ExternalServiceError(f"对象存储连接失败（{self.endpoint}）：{exc}") from exc
        if response.status_code == 404:
            return {"ok": False, "reason": "bucket_not_found", "bucket": self.bucket}
        self._raise_for_status("HEAD", self.bucket, response)
        return {"ok": True, "bucket": self.bucket, "status": response.status_code}

    async def create_bucket(self) -> bool:
        """创建存储桶（MinIO 部署初始化用；已存在返回 True）。"""
        payload_hash = EMPTY_SHA256
        path = self._path("")
        headers = self._signed_headers("PUT", path, payload_hash)
        async with self._client() as client:
            try:
                response = await client.request("PUT", self._url(""), headers=headers)
            except httpx.HTTPError as exc:
                raise ExternalServiceError(f"对象存储连接失败（{self.endpoint}）：{exc}") from exc
        if response.status_code in (200, 409):
            return True
        self._raise_for_status("PUT", self.bucket, response)
        return False

    async def health(self) -> dict[str, Any]:
        """连通性与 bucket 自检（不抛异常）。"""
        try:
            info = await self.bucket_info()
        except Exception as exc:  # noqa: BLE001 - 自检接口返回结构化结果
            return {
                "ok": False,
                "backend": "s3",
                "endpoint": self.endpoint,
                "bucket": self.bucket,
                "reason": str(exc),
            }
        return {
            "ok": bool(info.get("ok")),
            "backend": "s3",
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "path_style": self.path_style,
            **{k: v for k, v in info.items() if k != "ok"},
        }

    # ---------------------------------------------------------------- 错误映射
    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        body = response.text[:500]
        code = _XML_CODE_RE.search(body)
        message = _XML_MESSAGE_RE.search(body)
        if code or message:
            return " ".join(filter(None, [code.group(1) if code else "", message.group(1) if message else ""]))
        return body or response.reason_phrase

    def _raise_for_status(self, action: str, key: str, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = self._error_detail(response)
        if response.status_code == 404:
            if "NoSuchBucket" in detail:
                raise ExternalServiceError(
                    f"存储桶不存在：{self.bucket}，请先创建（MinIO 可调用 ensure_bucket()）",
                    data={"bucket": self.bucket},
                )
            raise FileError(f"对象不存在：{key}")
        if response.status_code in (401, 403):
            raise ExternalServiceError(
                f"对象存储鉴权失败（{action} {key}，HTTP {response.status_code}）：{detail}",
                data={"status": response.status_code, "bucket": self.bucket},
            )
        raise ExternalServiceError(
            f"对象存储调用失败（{action} {key}，HTTP {response.status_code}）：{detail}",
            data={"status": response.status_code, "bucket": self.bucket},
        )


    async def list_keys(self, prefix):
        """分页列举指定前缀，供补偿任务扫描；不读取其他业务对象。"""
        from xml.etree import ElementTree
        keys, token = [], None
        while True:
            query = {'list-type':'2', 'prefix':prefix}
            if token:
                query['continuation-token'] = token
            response = await self._request('GET', query=query)
            self._raise_for_status('GET', prefix, response)
            root = ElementTree.fromstring(response.content)
            keys.extend(node.text for node in root.findall('.//{*}Contents/{*}Key') if node.text)
            if root.findtext('{*}IsTruncated') != 'true':
                return keys
            token = root.findtext('{*}NextContinuationToken')
            if not token:
                raise ExternalServiceError('对象列表分页缺少 continuation token')


__all__ = [
    "EMPTY_SHA256",
    "S3Client",
    "normalize_endpoint",
]
