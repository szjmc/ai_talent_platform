# 题库共用的分类及索引约定集中定义，便于各层保持一致。
"""题库标签约定，各层共享，避免题型与等级混用。
标签：[RAG组] [P1]。
"""
from typing import Literal

Difficulty = Literal['beginner', 'intermediate', 'advanced']
QuestionType = Literal['single', 'multiple', 'true_false', 'fill_blank', 'text', 'coding']
DIFFICULTY_LABELS = {'beginner': '初级', 'intermediate': '中级', 'advanced': '高级'}
TYPE_LABELS = {
    'single': '单选题', 'multiple': '多选题', 'true_false': '判断题',
    'fill_blank': '填空题', 'text': '简答题', 'coding': '编程题',
}
