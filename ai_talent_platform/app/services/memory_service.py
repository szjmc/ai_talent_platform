"""复用ConversationMemory短期缓存与既有MySQL会话表。
标签：[Agent组] [P1]。"""
from app.ai.memory.conversation_memory import ConversationMemory
from app.repositories.conversation_repository import ConversationRepository
from app.models.conversation import Conversation
from app.core.exceptions import NotFoundError
class MemoryService:
    def __init__(self,db,user):
        self.db,self.user=db,user
        self.repo=ConversationRepository()
        self.pending=[]
        self.deleted_sessions=[]
    async def session(self,session_id,title='',lock=False):
        if session_id is None:
            row=Conversation(user_id=self.user.id,title=title[:128],agent_type='knowledge',message_count=0)
            self.db.add(row);await self.db.flush()
            return row
        row=await self.repo.owned(self.db,int(session_id),self.user.id,lock)
        if row is None:raise NotFoundError('会话不存在或不可访问')
        return row
    async def recent(self,row):
        # 长期数据库为事实来源；绕过旧ConversationMemory中未绑定会话ID的通用落库钩子。
        messages=await self.repo.messages(self.db,row.id,limit=6)
        return [{'role':m.role,'content':m.content or ''} for m in messages]
    async def save(self,row,question,answer,citations):
        await self.repo.append_turn(self.db,row,question,answer,citations)
        self.pending.append((row.id,[{'role':'user','content':question},{'role':'assistant','content':answer}]))
    async def after_commit(self):
        for sid in self.deleted_sessions:
            await ConversationMemory(sid).clear()
        self.deleted_sessions.clear()
        for sid,messages in self.pending:
            await ConversationMemory(sid).extend(messages)  # 不传db，避免重复写消息。
        self.pending.clear()
    async def conversations(self,page):
        return await self.repo.paginate(self.db,page,filters={'user_id':self.user.id,'agent_type':'knowledge'})
    async def history(self,sid,limit,before_id=None):
        row=await self.session(sid)
        return await self.repo.messages(self.db,row.id,limit,before_id)

    async def delete(self, sid):
        # 锁定本人会话，避免删除与同一会话的问答写入交错。
        row = await self.session(sid, lock=True)
        session_id = row.id
        # 会话模型的级联关系负责删除关联消息，缓存待事务提交后清理。
        await self.db.delete(row)
        await self.db.flush()
        self.deleted_sessions.append(session_id)
        return {'session_id': str(session_id)}
