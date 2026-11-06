# 解析返回受理状态，客户端使用简历 ID 查询结果。后台任务使用独立数据库会话。
"""简历HTTP接口：统一鉴权、响应和后台解析。
标签：[RAG组] [P1]。"""
from fastapi import APIRouter,Depends,File,UploadFile,BackgroundTasks
from app.core.dependencies import get_db,require_permission
from app.core.permissions import PermissionEnum
from app.core.response import success
from app.core.pagination import PageParams
from app.config.settings import settings
from app.services.resume_service import ResumeService
from app.schemas.resume import ResumeResponse,ResumeUploadResponse,ResumeParseRequest,ResumeBindRequest,ResumeQuery

router=APIRouter(prefix='/resumes',tags=['简历管理'])

async def get_service():
    from app.infrastructure.database.session import session_scope
    async with session_scope() as db:
        yield ResumeService(db)

async def run_parse(resume_id):
    from app.infrastructure.database.session import session_scope
    async with session_scope() as db:
        await ResumeService(db).parse_worker(resume_id)

@router.post('/upload', summary='上传简历文件', description='校验简历文件并保存原件及简历记录，按内容哈希检查重复上传。需要简历上传权限；返回简历ID、文件ID、状态和duplicate标记。上传后不会自动执行结构化解析，也不会自动加入知识库，请另行调用解析接口。')
async def upload(file:UploadFile=File(...),user=Depends(require_permission(PermissionEnum.RESUME_UPLOAD)),service=Depends(get_service,scope='function')):
    data=await file.read(settings.MAX_UPLOAD_SIZE_MB*1024*1024+1)
    row,duplicate=await service.upload(data,file.filename or 'resume.txt',file.content_type,user)
    return success(data=ResumeUploadResponse(**ResumeResponse.model_validate(row).model_dump(),duplicate=duplicate))

@router.get('', summary='分页查询简历列表', description='按文件名关键词、解析状态或人才ID筛选简历，支持公共分页参数。需要简历上传权限；管理员和超级管理员可查看全部，其他用户仅可查看本人上传的简历。返回文件信息与解析状态，不暴露存储对象键。')
async def list_resumes(page:PageParams=Depends(),query:ResumeQuery=Depends(),user=Depends(require_permission(PermissionEnum.RESUME_UPLOAD)),service=Depends(get_service)):
    from app.models.resume import Resume
    where=[] if user.is_superuser or 'admin' in user.role_codes else [Resume.created_by==user.id]
    result=await service.repo.paginate(service.db,page,filters={'status':query.status,'talent_id':query.talent_id},keyword=query.keyword,extra_where=where)
    result.items=[ResumeResponse.model_validate(row) for row in result.items]
    return success(data=result)

@router.get('/{resume_id}', summary='查看简历详情', description='根据简历ID查询文件信息、绑定的人才ID和解析状态。需要简历上传权限并通过归属检查；本接口返回元数据，结构化简历内容请使用解析结果接口查询。')
async def detail(resume_id:int,user=Depends(require_permission(PermissionEnum.RESUME_UPLOAD)),service=Depends(get_service)):
    return success(data=ResumeResponse.model_validate(await service.get(resume_id,user)))

@router.get('/{resume_id}/result', summary='查看简历解析结果', description='查询简历当前解析状态、结构化字段、警告、解析时间及失败原因，可用于后台解析后的轮询。需要简历上传权限并通过归属检查；失败时不将旧结果作为当前成功结果返回，accuracy可为空。')
async def result(resume_id:int,user=Depends(require_permission(PermissionEnum.RESUME_UPLOAD)),service=Depends(get_service)):
    return success(data=await service.result(resume_id,user))

@router.post('/{resume_id}/parse',status_code=202, summary='触发简历结构化解析', description='读取简历原件，后台执行文本提取或OCR、DeepSeek结构化提取、Schema校验和结果落库。需要简历解析权限及归属权限；force=true可强制重解析。HTTP202表示受理，请轮询解析结果；任务为进程内后台任务，不是持久化队列。')
async def parse(resume_id:int,data:ResumeParseRequest,tasks:BackgroundTasks,user=Depends(require_permission(PermissionEnum.RESUME_PARSE)),service=Depends(get_service,scope='function')):
    row,trigger=await service.request_parse(resume_id,user,data.force)
    if trigger:tasks.add_task(run_parse,row.id)
    return success(data={'resume_id':row.id,'status':row.status},code=202,message='解析任务已受理' if trigger else '已有解析结果')

@router.post('/{resume_id}/bind', summary='绑定简历到人才档案', description='将解析成功的简历绑定到已有的人才档案。需要简历解析权限，并通过简历及目标人才的管理范围校验；talent_id必须存在。本接口不自动创建人才，也不将解析字段自动覆盖到人才主档。')
async def bind(resume_id:int,data:ResumeBindRequest,user=Depends(require_permission(PermissionEnum.RESUME_PARSE)),service=Depends(get_service,scope='function')):
    return success(data=ResumeResponse.model_validate(await service.bind(resume_id,data.talent_id,user)))
