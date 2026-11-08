"""Collect non-secret, reproducible facts from the checked-out project.

Run with the documentation virtualenv. No application lifespan, database connection,
external model call, or business test is executed by this module.
"""
from __future__ import annotations
import ast, hashlib, importlib, inspect, json, os, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ai_talent_platform"
sys.path.insert(0, str(APP))
os.environ.update(DB_AUTO_CREATE_TABLES="false", PGSQL_ENABLED="false", REDIS_ENABLED="false",
                  MILVUS_ENABLED="false", DIFY_API_KEY="", LLM_API_KEY="", APP_ENV="test", DEBUG="false")

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def const(node: Any) -> Any:
    try: return ast.literal_eval(node)
    except Exception: return ast.unparse(node)

def collect_routes() -> list[dict]:
    out=[]
    for p in sorted((APP/"app/routers").glob("*.py")):
        tree=ast.parse(p.read_text(encoding="utf-8"))
        prefixes={}
        for n in ast.walk(tree):
            if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call):
                f=n.value.func
                if (isinstance(f,ast.Name) and f.id=="APIRouter") or (isinstance(f,ast.Attribute) and f.attr=="APIRouter"):
                    prefix=""
                    for k in n.value.keywords:
                        if k.arg=="prefix": prefix=const(k.value)
                    for t in n.targets:
                        if isinstance(t,ast.Name): prefixes[t.id]=prefix
        for n in ast.walk(tree):
            if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
            for d in n.decorator_list:
                if not (isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and isinstance(d.func.value,ast.Name)): continue
                obj=d.func.value.id; method=d.func.attr.upper()
                if obj not in prefixes or method not in {"GET","POST","PUT","PATCH","DELETE","WEBSOCKET"} or not d.args: continue
                path=const(d.args[0])
                if not isinstance(path,str): continue
                summary=""; perms=[]
                for k in d.keywords:
                    if k.arg=="summary": summary=const(k.value)
                positional=list(n.args.args)+list(n.args.posonlyargs)
                defaults=list(n.args.defaults)
                default_map={id(a):defaults[i-(len(positional)-len(defaults))] for i,a in enumerate(positional) if i>=len(positional)-len(defaults)}
                default_map.update({id(a):d for a,d in zip(n.args.kwonlyargs,n.args.kw_defaults) if d is not None})
                for a in positional+list(n.args.kwonlyargs):
                    nodes=([a.annotation] if a.annotation else [])+([default_map[id(a)]] if id(a) in default_map else [])
                    for node in nodes:
                        if "require_permission" in ast.unparse(node):
                            for x in ast.walk(node):
                                if isinstance(x,ast.Attribute): perms.append(x.attr)
                full=("/api/v1"+prefixes[obj]+path).replace("//","/")
                out.append(dict(method=method,path=full,summary=summary or n.name,handler=n.name,
                                permissions=sorted(set(perms)),source=str(p.relative_to(APP)),line=n.lineno))
    # Health probes are registered on the application, not the API router.
    out += [dict(method="GET",path="/health",summary="存活探针",handler="health",permissions=[],source="app/core/application.py",line=144),
            dict(method="GET",path="/health/ready",summary="就绪探针",handler="readiness",permissions=[],source="app/core/application.py",line=157)]
    return sorted(out,key=lambda x:(x["path"],x["method"]))

def collect_models() -> dict:
    from app.models import Base, AnalyticsBase
    from sqlalchemy.schema import CreateTable, CreateIndex
    from sqlalchemy.dialects import mysql, postgresql
    groups={}
    for label,meta,dialect in [("MySQL",Base.metadata,mysql.dialect()),("PostgreSQL",AnalyticsBase.metadata,postgresql.dialect())]:
        tables=[]
        for name,t in sorted(meta.tables.items()):
            cols=[]
            for c in t.columns:
                cols.append(dict(name=c.name,type=str(c.type.compile(dialect=dialect)),nullable=c.nullable,
                                 primary_key=c.primary_key,unique=bool(c.unique),index=bool(c.index),
                                 foreign_keys=[str(f.target_fullname) for f in c.foreign_keys],
                                 default=str(c.default.arg) if c.default is not None and hasattr(c.default,"arg") else "",
                                 server_default=str(c.server_default.arg) if c.server_default is not None and hasattr(c.server_default,"arg") else "",
                                 comment=c.comment or ""))
            tables.append(dict(name=name,columns=cols,
                               indexes=[dict(name=i.name,columns=[c.name for c in i.columns],unique=i.unique) for i in t.indexes],
                               primary_key=[c.name for c in t.primary_key.columns],
                               foreign_keys=[dict(columns=[c.name for c in fk.columns],target=fk.referred_table.name,target_columns=[c.name for c in fk.elements]) for fk in t.foreign_key_constraints],
                               ddl=str(CreateTable(t).compile(dialect=dialect)).strip()))
        groups[label]=tables
    return groups

def collect_schemas() -> dict:
    out={}
    from pydantic import BaseModel
    for p in sorted((APP/"app/schemas").glob("*.py")):
        if p.stem in {"__init__","common"}: continue
        mod=importlib.import_module("app.schemas."+p.stem)
        classes={}
        for name,obj in vars(mod).items():
            if inspect.isclass(obj) and issubclass(obj,BaseModel) and obj.__module__==mod.__name__ and hasattr(obj,"model_fields"):
                fields=[]
                for fn,fi in obj.model_fields.items():
                    default="" if fi.is_required() else repr(fi.default) if fi.default is not None else "None"
                    if not fi.is_required() and fi.default_factory is not None: default="<factory>"
                    fields.append(dict(name=fn,type=str(fi.annotation).replace("typing.",""),required=fi.is_required(),
                                       description=fi.description or "",default=default))
                classes[name]=dict(description=(obj.__doc__ or "").strip().split("\n")[0],fields=fields)
        out[p.stem]=classes
    return out

def collect_permissions() -> dict:
    from app.core.permissions import RoleEnum,PermissionEnum,ROLE_PERMISSIONS,ROLE_DATA_SCOPE
    return dict(roles=[str(x.value) for x in RoleEnum],permissions=[str(x.value) for x in PermissionEnum],
                role_permissions={k:sorted(str(x) for x in v) for k,v in ROLE_PERMISSIONS.items()},
                data_scope={str(k):str(v) for k,v in ROLE_DATA_SCOPE.items()})

def static_checks() -> dict:
    files=list((APP/"app").rglob("*.py"))+list((APP/"scripts").rglob("*.py"))+list((APP/"tests").rglob("*.py"))
    errors=[]
    for p in files:
        try: ast.parse(p.read_text(encoding="utf-8"))
        except Exception as e: errors.append(dict(file=str(p.relative_to(APP)),error=str(e)))
    app_import={"status":"failed","detail":""}
    try:
        from app.core.application import create_application
        create_application()
        app_import={"status":"passed","detail":"create_application() imported and assembled without running lifespan"}
    except Exception as e:
        app_import={"status":"failed","detail":f"{type(e).__name__}: {e}"}
    return dict(python=sys.version.split()[0],syntax_files=len(files),syntax_errors=errors,
                application_import=app_import,
                note="仅文档编制环境静态检查；未运行应用业务测试、压测、安全测试或真实外部服务联调。")

def collect() -> dict:
    sources=["2026顶配版AI数字化人才平台详细技术与功能需求.docx",
             "ai_talent_platform/docs/requirements_specification.md",
             "ai_talent_platform/docs/system_design_document.md",
             "ai_talent_platform/docs/database_design.md",
             "ai_talent_platform/docs/api_document.md",
             "ai_talent_platform/docs/team_sprint_plan.md",
             "ai_talent_platform/docs/TEST_STATUS.md",
             "ai_talent_platform/docs/KNOWN_ISSUES.md",
             "ai_talent_platform/app/routers/router.py",
             "ai_talent_platform/app/config/settings.py",
             "ai_talent_platform/frontend/src/App.tsx"]
    return dict(project="AI 数字化人才平台（智才云 TalentOS）",project_code="AITP-2026",
                generated_date="2026-11-08",baseline="2b6e2a4abbe452701396fa1615521f341ab428f4",
                routes=collect_routes(),models=collect_models(),schemas=collect_schemas(),
                permissions=collect_permissions(),checks=static_checks(),
                sources=[dict(path=s,sha256=sha(ROOT/s)) for s in sources],
                sql_scripts=sorted(str(p.relative_to(APP)) for p in (APP/"docs/sql").glob("*.sql")),
                frontend_pages=sorted(p.stem for p in (APP/"frontend/src/pages").glob("*.tsx")))

if __name__=="__main__":
    data=collect()
    out=ROOT/"deliverables/project_facts.json"
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:(len(v) if isinstance(v,(list,dict)) else v) for k,v in data.items()},ensure_ascii=False))
    print("checks",data["checks"])
