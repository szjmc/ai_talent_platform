"""统一认证问答入口及仅本人历史查询。
标签：[RAG组] [P1]。"""
import logging
from fastapi import APIRouter,Depends,Query
from app.core.dependencies import require_permission
from app.core.permissions import PermissionEnum
from app.core.response import success
from app.core.pagination import PageParams
from app.services.response_answer_service import RequestQuestion,ResponseAnswerService
router=APIRouter(tags=['智能问答'])
async def get_service(user=Depends(require_permission(PermissionEnum.KNOWLEDGE_READ))):
    from app.infrastructure.database.session import get_session_factory
    async with get_session_factory()() as db:
        service=ResponseAnswerService(db,user)
        try:
            yield service
            await db.commit()
        except BaseException:
            await db.rollback();raise
        else:
            try:await service.memory.after_commit()
            except Exception:logging.getLogger(__name__).exception('短期记忆缓存失败，长期消息已提交')

@router.post('/text2answer', summary='智能知识问答', description='根据当前用户有权访问的知识片段生成带来源的回答，支持混合检索、可选模型重排及多轮追问。首次调用可不传session_id，续聊需传回返回的会话ID。传suggest=true可额外返回1-2个基于本轮材料的追问推荐（会多一次模型往返，默认关闭）；推荐生成失败时suggestions为空数组，不影响答案返回。需要知识库读取权限；无相关材料时说明资料不足，不替代实时数据库统计查询。')
async def answer(request:RequestQuestion,service=Depends(get_service,scope='function')):
    return success(data=await service.handle(request))
@router.get('/conversations', summary='分页查询本人问答会话记录列表', description='分页查看当前用户的知识问答会话，返回会话ID、标题、消息数量和最后消息时间。需要知识库读取权限；仅返回本人会话，不列出其他用户的历史。')
async def conversations(page:PageParams=Depends(),service=Depends(get_service)):
    result=await service.memory.conversations(page)
    result.items=[{'session_id':str(r.id),'title':r.title,'message_count':r.message_count,'last_message_at':r.last_message_at} for r in result.items]
    return success(data=result)
@router.get('/conversations/{session_id}/messages', summary='查看本人会话消息历史', description='查询指定会话的用户提问、助手回答和来源引用，默认返回最近100条，最多200条，结果按消息顺序展示。使用before_id可翻查更早消息。需要知识库读取权限及会话归属权限；不存在或无权访问的会话返回404。')
async def messages(session_id:int,limit:int=Query(100,ge=1,le=200),before_id:int|None=Query(None,ge=1),service=Depends(get_service)):
    rows=await service.memory.history(str(session_id),limit,before_id)
    return success(data=[{'id':m.id,'role':m.role,'content':m.content,'citations':m.citations,'created_at':m.created_at} for m in rows])
@router.delete('/conversations/{session_id}', summary='删除本人问答会话及其消息历史', description='从历史会话列表中删除指定会话，并一并删除该会话下的全部提问、回答与来源引用记录；为物理删除，删除后不可恢复，会话ID再次访问返回404。需要知识库读取权限及会话归属权限；不存在或不属于当前用户的会话返回404。')
async def delete_conversation(session_id:int,service=Depends(get_service,scope='function')):
    return success(data=await service.memory.delete(session_id))
