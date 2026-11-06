# 题库请求及响应字段在此校验，业务筛选和索引由服务处理。
"""题库导入、选题与响应契约。
标签：[RAG组] [P1]。
"""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator, model_validator

from app.core.question_bank_constants import Difficulty, QuestionType

PositiveId = Annotated[int, Field(gt=0, le=9223372036854775807, strict=True)]
Count = Annotated[int, Field(ge=0, le=100, strict=True)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1, max_length=4000)


class QuestionImport(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    bank_id: PositiveId
    external_id: ShortText
    technology: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9_+#.-]*$')
    difficulty: Difficulty
    question_type: QuestionType
    knowledge_points: list[ShortText] = Field(default_factory=list, max_length=30)
    stem: str = Field(min_length=1, max_length=16000)
    options: list[QuestionOption] | None = Field(default=None, max_length=26)
    answer: Any
    analysis: str | None = Field(default=None, max_length=16000)
    status: Literal['draft', 'published', 'disabled'] = 'published'
    permission_scope: Literal['public', 'dept', 'private'] = 'private'
    organization_id: PositiveId | None = None

    @field_validator('technology', mode='before')
    @classmethod
    def normalize_technology(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode='after')
    def validate_question(self):
        if self.permission_scope == 'dept' and self.organization_id is None:
            raise ValueError('部门题目必须指定 organization_id')
        if self.question_type in {'single', 'multiple'}:
            if not self.options or len(self.options) < 2:
                raise ValueError('选择题至少需要两个选项')
            keys = [option.key for option in self.options]
            if len(set(keys)) != len(keys):
                raise ValueError('选项key不能重复')
            if self.question_type == 'single':
                if not isinstance(self.answer, str) or self.answer not in keys:
                    raise ValueError('单选答案必须为有效选项key')
            else:
                if (not isinstance(self.answer, list) or not self.answer
                        or any(not isinstance(key, str) or key not in keys for key in self.answer)
                        or len(set(self.answer)) != len(self.answer)):
                    raise ValueError('多选答案必须为不重复的有效选项key列表')
        else:
            if self.options:
                raise ValueError('非选择题不接受options')
            if self.question_type == 'true_false' and type(self.answer) is not bool:
                raise ValueError('判断题答案必须为JSON布尔值')
            if self.question_type == 'fill_blank':
                if (not isinstance(self.answer, list) or not self.answer
                        or any(not isinstance(group, list) or not group
                               or any(not isinstance(x, str) or not x.strip() for x in group)
                               for group in self.answer)):
                    raise ValueError('填空答案为每个空的可接受答案列表，例如[["列表", "list"]]')
            if self.question_type in {'text', 'coding'}:
                if not isinstance(self.answer, str) or not self.answer.strip():
                    raise ValueError('简答和编程题答案必须为非空文本')
        return self


class QuestionSearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    technology: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9_+#.-]*$')
    difficulty: Difficulty
    query: str | None = Field(default=None, max_length=2000)
    counts: dict[QuestionType, Count]
    bank_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    exclude_question_ids: list[PositiveId] = Field(default_factory=list, max_length=1000)
    include_answers: bool = False

    @field_validator('technology', mode='before')
    @classmethod
    def normalize_technology(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator('query')
    @classmethod
    def empty_query_is_none(cls, value):
        return value or None

    @model_validator(mode='after')
    def validate_counts(self):
        if not 1 <= sum(self.counts.values()) <= 100:
            raise ValueError('选题总数必须在1到100之间')
        return self


class SelectedQuestion(BaseModel):
    question_id: int
    bank_id: int
    external_id: str
    content_version: int
    technology: str
    difficulty: Difficulty
    question_type: QuestionType
    knowledge_points: list[str]
    stem: str
    options: list[QuestionOption] | None = None
    score: float | None = None
    answer: Any = None
    analysis: str | None = None


class QuestionSearchResponse(BaseModel):
    mode: Literal['semantic', 'random']
    requested_count: int
    returned_count: int
    shortage: dict[QuestionType, int]
    items: list[SelectedQuestion]


class QuestionImportBatch(RootModel):
    root: Annotated[list[QuestionImport], Field(min_length=1, max_length=10000)]

    @model_validator(mode='after')
    def unique_source_ids(self):
        keys = [(item.bank_id, item.external_id) for item in self.root]
        if len(set(keys)) != len(keys):
            raise ValueError('同一文件内bank_id与external_id组合不能重复')
        return self


class QuestionImportResponse(BaseModel):
    imported_count: int
    question_ids: list[int]
    sync_requested: bool
    indexed_count: int
    failed_count: int
    pending_count: int
    sync_error: str | None = None
