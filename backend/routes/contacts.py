"""联系人管理路由

处理联系人的增删改查、搜索（写信自动补全用）、快速添加（邮件详情页用）、往来邮件统计。
按 user_uid 隔离数据，一个联系人可关联多个邮箱。
"""
from fastapi import APIRouter, Request, Query, Body

from errors import AppError

from db import (
    get_contacts,
    get_contact_by_id,
    get_contact_candidates,
    create_contact,
    update_contact,
    delete_contact,
    upsert_contact_by_email,
    get_contact_stats,
)
from deps import get_uid
from schemas import (
    ContactItem,
    ContactEmailItem,
    ContactCandidateListResponse,
    ContactImportRequest,
    ContactImportResponse,
    ContactListResponse,
    ContactSearchResponse,
    ContactCreateRequest,
    ContactUpdateRequest,
    QuickAddContactRequest,
    ContactStatsResponse,
    StatusResponse,
)
from utils.logger import get_logger

logger = get_logger("routes.contacts")

router = APIRouter(tags=["联系人"])


@router.get("/api/contacts", response_model=ContactListResponse, summary="获取联系人列表")
async def get_contacts_api(
    request: Request,
    search: str = Query(default="", description="按姓名或邮箱模糊搜索"),
):
    """获取当前用户的联系人列表，支持模糊搜索，每个联系人含 emails 数组"""
    uid = await get_uid(request)
    rows = await get_contacts(uid, search)
    return {"contacts": [_row_to_item(r) for r in rows]}


@router.get("/api/contacts/search", response_model=ContactSearchResponse, summary="搜索联系人（自动补全）")
async def search_contacts_api(
    request: Request,
    q: str = Query(default="", description="搜索关键词"),
):
    """轻量搜索接口，限制返回 10 条，用于写信自动补全"""
    uid = await get_uid(request)
    rows = await get_contacts(uid, q)
    return {"results": [_row_to_item(r) for r in rows[:10]]}


@router.get("/api/contacts/candidates", response_model=ContactCandidateListResponse, summary="从指定邮箱预览联系人候选")
async def get_contact_candidates_api(
    request: Request,
    account_id: str = Query(min_length=1, description="要扫描的邮箱账号ID"),
    search: str = Query(default="", max_length=255, description="按姓名或邮箱过滤候选"),
):
    """仅扫描当前用户指定邮箱已经同步到本地的邮件头，不连接远端邮箱。"""
    uid = await get_uid(request)
    candidates = await get_contact_candidates(uid, account_id, search)
    if candidates is None:
        raise AppError(404, "邮箱账号不存在")
    return {"candidates": candidates}


@router.post("/api/contacts/import", response_model=ContactImportResponse, summary="批量导入邮件联系人候选")
async def import_contact_candidates_api(
    request: Request,
    body: ContactImportRequest = Body(..., description="选中的候选联系人"),
):
    """批量导入用户勾选的候选项；已有联系人不会被覆盖。"""
    uid = await get_uid(request)
    candidates = await get_contact_candidates(uid, body.account_id, limit=1000)
    if candidates is None:
        raise AppError(404, "邮箱账号不存在")
    allowed = {item["email"].lower(): item for item in candidates}
    imported = 0
    skipped = 0
    seen: set[str] = set()
    for requested in body.contacts:
        email = requested.email.strip().lower()
        if not email or email in seen or email not in allowed:
            skipped += 1
            continue
        seen.add(email)
        candidate = allowed[email]
        name = requested.name.strip() or candidate.get("name", "")
        _row, is_new = await upsert_contact_by_email(uid, name, email)
        if is_new:
            imported += 1
        else:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


@router.get("/api/contacts/{contact_id}/stats", response_model=ContactStatsResponse, summary="获取联系人往来邮件统计")
async def get_contact_stats_api(
    request: Request,
    contact_id: int,
    email: str = Query(default="", description="要统计的邮箱地址"),
):
    """获取与某邮箱地址的往来邮件数量和最近联系时间"""
    uid = await get_uid(request)
    if not email:
        raise AppError(400, "邮箱地址不能为空")
    return await get_contact_stats(uid, email)


@router.post("/api/contacts", response_model=ContactItem, summary="新增联系人")
async def create_contact_api(
    request: Request,
    body: ContactCreateRequest = Body(default_factory=ContactCreateRequest, description="联系人信息"),
):
    """新增联系人（含多个邮箱），第一个邮箱为主邮箱"""
    uid = await get_uid(request)
    if not body.emails:
        raise AppError(400, "至少需要填写一个邮箱")
    row = await create_contact(uid, body.name, body.emails, body.phone, body.company, body.remark, body.group_name)
    return _row_to_item(row)


@router.put("/api/contacts/{contact_id}", response_model=StatusResponse, summary="更新联系人")
async def update_contact_api(
    request: Request,
    contact_id: int,
    body: ContactUpdateRequest = Body(default_factory=ContactUpdateRequest, description="联系人信息"),
):
    """更新联系人基本信息和邮箱列表"""
    uid = await get_uid(request)
    if not body.emails:
        raise AppError(400, "至少需要填写一个邮箱")
    ok = await update_contact(contact_id, uid, body.name, body.emails, body.phone, body.company, body.remark, body.group_name)
    if not ok:
        raise AppError(404, "联系人不存在或无权操作")
    return {"success": True}


@router.delete("/api/contacts/{contact_id}", response_model=StatusResponse, summary="删除联系人")
async def delete_contact_api(
    request: Request,
    contact_id: int,
):
    """删除联系人（含邮箱），校验归属"""
    uid = await get_uid(request)
    ok = await delete_contact(contact_id, uid)
    if not ok:
        raise AppError(404, "联系人不存在或无权操作")
    return {"success": True}


@router.post("/api/contacts/quick-add", response_model=ContactItem, summary="快速添加联系人")
async def quick_add_contact_api(
    request: Request,
    body: QuickAddContactRequest = Body(default_factory=QuickAddContactRequest, description="姓名和邮箱"),
):
    """快速添加联系人（邮件详情页用），邮箱已存在则返回 409 提示"""
    uid = await get_uid(request)
    row, is_new = await upsert_contact_by_email(uid, body.name, body.email)
    if not is_new:
        raise AppError(409, "该邮箱已在联系人中")
    return _row_to_item(row)


def _row_to_item(row: dict) -> dict:
    """数据库行转 API 返回的 ContactItem 格式"""
    emails = row.get("emails", [])
    return {
        "id": row.get("id", 0),
        "name": row.get("name", ""),
        "emails": [
            {"id": e.get("id", 0), "email": e.get("email", ""), "is_primary": e.get("is_primary", False)}
            for e in emails
        ],
        "phone": row.get("phone", ""),
        "company": row.get("company", ""),
        "remark": row.get("remark", ""),
        "group_name": row.get("group_name", ""),
    }
