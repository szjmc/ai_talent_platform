"""
gxd-人才档案模块配置；沿用环境变量，不修改公共Settings契约。

标签：[RAG组] [P1]（人才字段与标签体系配置）
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class TalentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TALENT_", env_file=".env", extra="ignore")
    batch_limit: int = Field(20, ge=1, le=200)
    parse_timeout: int = Field(240, ge=30, le=600)
    refresh_timeout: int = Field(120, ge=10, le=600)
    max_text_chars: int = Field(60000, ge=1000, le=200000)
    max_pages: int = Field(50, ge=1, le=200)
    embedding_dim: int = Field(1024, ge=1)
    ocr_enabled: bool = False
    ocr_language: str = "chi_sim+eng"
    collection: str = "talent_vectors"
    # 团队约定：talent_vectors 一名人才一条复合画像向量，主键与 MySQL talents.id 同名。
    # 写入/读取都必须用这个字段，不要再按切片字符串 ID 写多行。
    primary_field: str = "talent_id"
    vector_field: str = "vector"
    duplicate_threshold: float = Field(0.92, ge=0, le=1)
    chunk_size: int = Field(1000, ge=200, le=4000)
    chunk_overlap: int = Field(100, ge=0, le=199)
    profile_text_chars: int = Field(8000, ge=500, le=60000)

talent_settings = TalentSettings()