import time
import uuid

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel, Field

from db import (
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user_avatar,
    update_user_password,
    update_user_profile,
    update_user_status,
)
from deps import require_admin
from errors import AppError
from models import User
from services.security import hash_password
from services.user_profiles import MAX_AVATAR_BYTES, delete_user_avatar, save_user_avatar

router = APIRouter(prefix="/api/admin/users", tags=["管理员"])


class CreateUserRequest(BaseModel):
    username: str = Field(description="用户名")
    password: str = Field(description="初始密码")


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(description="新密码")


class UpdateUserRequest(BaseModel):
    username: str = Field(description="用户名")
    nickname: str = Field(default="", description="昵称")


def _user_payload(user) -> dict:
    nickname = (user.nickname or "").strip()
    return {
        "id": user.id,
        "username": user.username,
        "nickname": nickname,
        "display_name": nickname or user.username,
        "avatar_url": f"/api/auth/avatar/{user.id}?v={int(user.updated_at or 0)}" if user.avatar_path else "",
        "role": user.role,
        "status": user.status,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


@router.get("")
async def get_users(request: Request):
    await require_admin(request)
    users = await list_users()
    return {
        "users": [_user_payload(user) for user in users]
    }


@router.post("")
async def add_user(request: Request, body: CreateUserRequest):
    await require_admin(request)
    username = body.username.strip()
    password = body.password.strip()
    if len(username) < 3:
        raise AppError(400, "用户名至少 3 位")
    if len(password) < 6:
        raise AppError(400, "密码至少 6 位")
    if await get_user_by_username(username):
        raise AppError(400, "用户名已存在")
    now = time.time()
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        role="user",
        status="active",
        created_at=now,
        updated_at=now,
    )
    await create_user(user)
    return {"success": True}


@router.patch("/{user_id}")
async def edit_user(request: Request, user_id: str, body: UpdateUserRequest):
    await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    username = body.username.strip()
    nickname = body.nickname.strip()
    if len(username) < 3:
        raise AppError(400, "用户名至少 3 位")
    if len(username) > 191 or len(nickname) > 191:
        raise AppError(400, "用户名或昵称过长")
    existing = await get_user_by_username(username)
    if existing and existing.id != target.id:
        raise AppError(400, "用户名已存在")
    await update_user_profile(target.id, username, nickname)
    updated = await get_user_by_id(target.id)
    return {"success": True, "user": _user_payload(updated or target)}


@router.post("/{user_id}/avatar")
async def upload_user_avatar(request: Request, user_id: str, avatar: UploadFile = File(...)):
    await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    data = await avatar.read(MAX_AVATAR_BYTES + 1)
    try:
        stored_path = save_user_avatar(target.id, data)
    except ValueError as exc:
        raise AppError(400, str(exc)) from exc
    await update_user_avatar(target.id, stored_path)
    updated = await get_user_by_id(target.id)
    return {"success": True, "user": _user_payload(updated or target)}


@router.delete("/{user_id}/avatar")
async def remove_user_avatar(request: Request, user_id: str):
    await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    delete_user_avatar(target.avatar_path)
    await update_user_avatar(target.id, "")
    updated = await get_user_by_id(target.id)
    return {"success": True, "user": _user_payload(updated or target)}


@router.post("/{user_id}/reset-password")
async def reset_password(request: Request, user_id: str, body: ResetPasswordRequest):
    admin = await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    if target.id == admin.id:
        raise AppError(400, "请使用修改密码功能更新自己的密码")
    if len(body.new_password.strip()) < 6:
        raise AppError(400, "密码至少 6 位")
    await update_user_password(target.id, hash_password(body.new_password.strip()))
    return {"success": True}


@router.post("/{user_id}/toggle-status")
async def toggle_user_status(request: Request, user_id: str):
    admin = await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    if target.id == admin.id:
        raise AppError(400, "不能禁用当前管理员")
    new_status = "disabled" if target.status == "active" else "active"
    await update_user_status(target.id, new_status)
    return {"success": True, "status": new_status}


@router.delete("/{user_id}")
async def remove_user(request: Request, user_id: str):
    admin = await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    if target.id == admin.id or target.role == "admin":
        raise AppError(400, "不能删除管理员用户")
    delete_user_avatar(target.avatar_path)
    await delete_user(target.id)
    return {"success": True}
