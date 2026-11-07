"""
模块：app/services/file_service.py
职责：文件管理业务服务层。

标签：[架构组] [P1]  复用本组 BaseStorage（上传校验 / 元数据 / 鉴权下载）

============================ 实现思路与代码要点 ============================
Service 的定位：把「校验 + 仓库调用 + 外部能力 + 事务」组织成一次完整业务。
统一写法：
    class XxxService:
        def __init__(self, db: AsyncSession, repo: XxxRepository | None = None):
            self.db = db
            self.repo = repo or XxxRepository()

        async def create_xxx(self, data: XxxCreate, current_user: User) -> Xxx:
            # 1) 权限 / 业务规则校验（不满足抛 BusinessException 子类）
            # 2) Schema -> dict -> ORM
            # 3) 调用仓库写入
            # 4) 副作用（审计、缓存、向量化、AI 任务）
            # 5) 返回 ORM 对象（由 Router 用 Response Schema 序列化）

硬性约束：
- Service 不处理 HTTP（不接收 Request、不返回 JSONResponse）；
- Service 不写复杂 SQL（查询交给 Repository）；
- 跨模块调用只通过对方 Service 的公开方法，禁止直接引用别人的内部实现；
- 事务：默认随请求提交；需要局部事务用 infrastructure.database.transaction。

- 文件名必须重命名（uuid）存储，防止路径穿越与覆盖攻击；
- 下载接口不要直接把磁盘绝对路径暴露给前端。
方法清单（按顺序实现）：
    - [ ] 上传：类型/大小校验 -> sha256 -> 落盘 -> 建元数据
    - [ ] 下载：权限校验（biz_type + 归属）-> 返回 FileResponse
    - [ ] 删除：软删元数据 + 物理文件延迟清理
    - [ ] 批量上传：逐个校验，返回成功/失败明细
    - [ ] 孤儿清理：由脚本/定时任务调用
=========================================================================
"""

"""gxd-简历文件保存与读取，复用项目存储Adapter；不改变公共存储接口。"""
from pathlib import Path
from hashlib import sha256
from uuid import uuid4
from app.config.settings import settings
from app.core.exceptions import ExternalServiceError, FileError, NotFoundError
from app.adapters.storage.base_storage import get_storage, storage_registry
from app.models.file import File
# 注意：项目里存在两个同名 ResumeRepository（resume_repository.py 为简历精简版，
# 无 get_file/save；talent_resume_repository.py 才是含文件元数据访问的完整版）。
# 文件中心读写 files 表必须用完整版，否则 read()/save_resume() 会 AttributeError。
from app.repositories.talent_resume_repository import ResumeRepository

class FileService:
    def __init__(self, db):
        """gxd-注入请求或后台任务自己的数据库会话。"""
        self.db, self.repo = db, ResumeRepository()

    @staticmethod
    def resume_extension(filename):
        """gxd-按原始文件名和团队配置校验简历格式，返回带点的小写扩展名。"""
        extension = Path(filename).suffix.lower()
        supported = {".pdf", ".docx", ".doc", ".txt", ".png", ".jpg", ".jpeg"}
        if extension not in supported or extension not in settings.allowed_upload_extensions:
            raise FileError("不支持的简历格式")
        return extension

    @staticmethod
    def validate_content(extension, content):
        """gxd-上传和对象读取复用同一套大小和文件签名校验。"""
        if not content or len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise FileError("文件为空或超过大小限制")
        signatures = {".pdf": b"%PDF-", ".docx": b"PK", ".doc": bytes.fromhex("d0cf11e0"), ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8"}
        if extension in signatures and not content.startswith(signatures[extension]):
            raise FileError("文件内容与扩展名不符")

    async def save_resume(self, filename, content, content_type, user_id):
        """gxd-校验内容签名和大小，随机命名后保存文件元数据。"""
        name = Path(filename or "resume").name.replace("\\", "_")[:255]
        extension = self.resume_extension(name)
        self.validate_content(extension, content)
        stored = uuid4().hex + extension
        storage = get_storage()
        path = await storage.save(content, stored, subdir="resume")
        obj = File(file_name=name, stored_name=stored, file_path=path, file_size=len(content), content_type=content_type, extension=extension, file_hash=sha256(content).hexdigest(), storage_type=storage.name, biz_type="resume", uploader_id=user_id, created_by=user_id)
        try:
            return await self.repo.save(self.db, obj)
        except Exception:
            await storage.delete(path)
            raise

    async def read(self, file_id):
        """gxd-读取已由调用Service鉴权的简历文件，存储路径不返回前端。

        兼容两种入参：文件 ID（int）或已查出的 File 对象——
        assessment_service 等调用方此前直接把 File ORM 对象传进来，
        `File.id == <File对象>` 会抛 ArgumentError（建测评必现 500）。
        """
        if hasattr(file_id, "id") and hasattr(file_id, "file_path"):
            file = file_id
        else:
            file = await self.repo.get_file(self.db, file_id)
        if file is None:
            raise NotFoundError("原始文件不存在")
        return file, await self.read_original(file)

    async def read_original(self, file):
        """gxd-RAG对接点2：用 files.file_path 从对应存储读取原件，返回 bytes。"""
        extension = self.resume_extension(file.file_name)
        if file.file_size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise FileError("简历超过大小限制")
        if file.storage_type == "local":
            storage = get_storage("local")
        else:
            # gxd-合并时由 RAG 注册与 storage_type 同名的 MinIO 读取器，file_path 按双方约定保存对象键。
            reader = storage_registry.try_get(file.storage_type)
            if reader is None:
                raise ExternalServiceError("简历对象存储读取器尚未接入，请检查 MinIO 适配器注册")
            storage = reader()
        content = await storage.read(file.file_path)
        self.validate_content(extension, content)
        if file.file_hash and sha256(content).hexdigest() != file.file_hash.lower():
            raise FileError("原始简历与文件记录的 SHA-256 不一致，请检查对象版本")
        return content

    async def upload(self, data, filename, content_type, uploader_id, category, before_write=None, biz_type="knowledge"):
        """供知识库调用：对象按 category 目录分类，数据库保留对象键。"""
        import re
        from pathlib import Path
        from app.config.settings import settings
        from app.core.exceptions import FileError
        from app.adapters.storage.base_storage import get_storage
        from app.utils.file_utils import safe_filename, get_file_hash
        from app.models.file import File
        if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}", category):
            raise FileError("分类只允许中文、字母、数字、下划线和连字符")
        if not data or len(data) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise FileError("文件为空或超过上传限制")
        extension = Path(filename).suffix.lower()
        if extension not in {".txt", ".md", ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
            raise FileError("支持文本、PDF、docx、xlsx 和图片；旧版 doc/xls 请先转换")
        storage = get_storage()
        stored_name = safe_filename(filename)
        options = {"before_write": before_write} if before_write is not None else {}
        key = await storage.save(data, stored_name, subdir=biz_type + "/" + category, **options)
        record = File(file_name=Path(filename).name[:255], stored_name=stored_name, file_path=key, file_size=len(data), content_type=content_type, extension=extension, file_hash=get_file_hash(data), storage_type=settings.STORAGE_BACKEND, biz_type=biz_type, uploader_id=uploader_id)
        try:
            self.db.add(record)
            await self.db.flush()
        except Exception:
            await storage.delete(key)
            raise
        return record

    async def read_object(self, record):
        """按已加载的文件记录读取原件（通用对象读取，不做简历扩展名校验）。

        与 read(file_id) 的分工：
        - read(file_id)：简历场景，查 files 表 + 扩展名/哈希校验后返回 (file, bytes)；
        - read_object(record)：知识库等已持有 File 记录的场景，直接按 storage_type 读取 bytes。
        历史上两者同名 read，后者覆盖前者，导致 read(file_id) 运行时
        'int' object has no attribute 'storage_type'。
        """
        from app.adapters.storage.base_storage import get_storage
        return await get_storage(record.storage_type).read(record.file_path)
