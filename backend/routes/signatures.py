"""签名模板管理路由

处理邮件签名的查询、保存，以及多签名模板的增删改查。
"""
import asyncio

from fastapi import APIRouter, Request, Body, File, UploadFile
from fastapi.responses import FileResponse

from errors import AppError

from db import (
    create_signature,
    delete_signature,
    get_account_by_id,
    get_signature_by_id,
    get_signatures,
    get_user_settings,
    set_user_settings,
    update_signature,
)
from deps import get_uid
from models import Signature
from schemas import (
    SignatureImageUploadResponse,
    SignatureListResponse,
    SignatureSettingsRequest,
    SignatureSettingsResponse,
    SignatureTemplateItem,
    SignatureTemplateRequest,
    SignatureTemplateUpdateRequest,
    StatusResponse,
)
from services.signature_images import (
    MAX_SIGNATURE_IMAGE_BYTES,
    resolve_signature_image,
    save_signature_image,
)

router = APIRouter(tags=["签名"])


# ==================== 签名设置接口 ====================


@router.get("/api/signature", response_model=SignatureSettingsResponse, summary="获取邮件签名")
async def get_signature(request: Request):
    """获取当前用户的邮件签名配置

    signature_html/signature_enabled 按 user_uid 存储在 user_settings 表
    """
    uid = await get_uid(request)
    # 从用户级配置表读取（按 user_uid 隔离）
    user_settings = await get_user_settings(uid, ["signature_html", "signature_enabled"])
    return {
        "signature_html": user_settings.get("signature_html", ""),
        "signature_enabled": user_settings.get("signature_enabled", 0),
    }


@router.put("/api/signature", response_model=StatusResponse, summary="保存邮件签名")
async def save_signature(request: Request, body: SignatureSettingsRequest = Body(default_factory=SignatureSettingsRequest, description="邮件签名设置")):
    """保存邮件签名配置

    signature_html/signature_enabled 按 user_uid 存储在 user_settings 表
    """
    uid = await get_uid(request)
    # 写入用户级配置表（按 user_uid 隔离）
    await set_user_settings(uid, {
        "signature_html": body.signature_html,
        "signature_enabled": 1 if body.signature_enabled else 0,
    })
    return {"success": True}


# ==================== 签名图片 ====================


@router.post(
    "/api/signatures/images",
    response_model=SignatureImageUploadResponse,
    summary="上传签名图片",
)
async def upload_signature_image(request: Request, image: UploadFile = File(...)):
    uid = await get_uid(request)
    content = await image.read(MAX_SIGNATURE_IMAGE_BYTES + 1)
    try:
        stored = await asyncio.to_thread(save_signature_image, uid, content)
    except ValueError as exc:
        message = str(exc)
        status_code = 413 if "不能超过" in message else 400
        raise AppError(status_code, message)
    return {
        "url": str(request.url_for("get_signature_image", image_id=stored.image_id)),
    }


@router.get(
    "/api/signature-images/{image_id}",
    name="get_signature_image",
    response_class=FileResponse,
    summary="读取签名图片",
)
async def get_signature_image(image_id: str):
    image_path = resolve_signature_image(image_id)
    if not image_path or not image_path.is_file():
        raise AppError(404, "签名图片不存在")
    return FileResponse(
        image_path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ==================== 签名模板接口（多模板管理） ====================


async def _validate_signature_account(account_id: str, user_uid: str) -> str:
    """Validate that a scoped signature only references an account owned by the user."""
    normalized = (account_id or "").strip()
    if not normalized:
        return ""
    account = await get_account_by_id(normalized)
    if not account or account.user_uid != user_uid:
        raise AppError(404, "关联邮箱不存在")
    return normalized


@router.get("/api/signatures", response_model=SignatureListResponse, summary="获取所有签名模板")
async def get_signatures_api(request: Request):
    """获取当前用户的所有签名模板列表"""
    uid = await get_uid(request)
    # 按 user_uid 过滤，防止跨用户数据泄露
    sigs = await get_signatures(uid)
    return {
        "signatures": [
            {
                "id": s.id,
                "name": s.name,
                "content_html": s.content_html,
                "is_default": bool(s.is_default),
                "is_reply_default": bool(s.is_reply_default),
                "account_id": s.account_id,
            }
            for s in sigs
        ]
    }


@router.post("/api/signatures", response_model=SignatureTemplateItem, summary="创建签名模板")
async def create_signature_api(request: Request, body: SignatureTemplateRequest = Body(default_factory=SignatureTemplateRequest, description="签名模板内容")):
    """创建新的签名模板"""
    uid = await get_uid(request)
    name = body.name.strip()
    if not name:
        raise AppError(400, "签名名称不能为空")
    account_id = await _validate_signature_account(body.account_id, uid)
    sig = Signature(
        name=name,
        content_html=body.content_html,
        is_default=1 if body.is_default else 0,
        is_reply_default=1 if body.is_reply_default else 0,
        account_id=account_id,
        user_uid=uid,  # 绑定当前用户
    )
    sig = await create_signature(sig)
    return {
        "id": sig.id,
        "name": sig.name,
        "content_html": sig.content_html,
        "is_default": bool(sig.is_default),
        "is_reply_default": bool(sig.is_reply_default),
        "account_id": sig.account_id,
    }


@router.put("/api/signatures/{sig_id}", response_model=StatusResponse, summary="更新签名模板")
async def update_signature_api(request: Request, sig_id: int, body: SignatureTemplateUpdateRequest = Body(default_factory=SignatureTemplateUpdateRequest, description="签名模板内容")):
    """更新指定签名模板"""
    uid = await get_uid(request)
    # 查询时校验 user_uid 归属，防止越权修改
    existing = await get_signature_by_id(sig_id, uid)
    if not existing:
        raise AppError(404, "签名模板不存在")

    if body.name is not None:
        existing.name = body.name.strip() or existing.name
    if body.content_html is not None:
        existing.content_html = body.content_html
    if body.is_default is not None:
        existing.is_default = 1 if body.is_default else 0
    if body.is_reply_default is not None:
        existing.is_reply_default = 1 if body.is_reply_default else 0
    if body.account_id is not None:
        existing.account_id = await _validate_signature_account(body.account_id, uid)

    ok = await update_signature(existing)
    if not ok:
        raise AppError(500, "更新失败")
    return {"success": True}


@router.delete("/api/signatures/{sig_id}", response_model=StatusResponse, summary="删除签名模板")
async def delete_signature_api(request: Request, sig_id: int):
    """删除指定签名模板"""
    uid = await get_uid(request)
    # 删除时校验 user_uid 归属，防止越权删除
    ok = await delete_signature(sig_id, uid)
    if not ok:
        raise AppError(404, "签名模板不存在或删除失败")
    return {"success": True}
