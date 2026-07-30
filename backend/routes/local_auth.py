import time

from fastapi import APIRouter, File, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db import (
    get_user_by_id,
    get_user_by_username,
    update_user_avatar,
    update_user_password,
    update_user_profile,
)
from deps import get_current_user
from errors import AppError
from services.security import clear_session_cookie, set_session_cookie, verify_password, hash_password
from services.user_profiles import MAX_AVATAR_BYTES, delete_user_avatar, resolve_user_avatar, save_user_avatar

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str = Field(description="用户名")
    password: str = Field(description="密码")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(description="当前密码")
    new_password: str = Field(description="新密码")


class UpdateProfileRequest(BaseModel):
    username: str = Field(description="用户名")
    nickname: str = Field(default="", description="昵称")


def _avatar_url(user) -> str:
    if not getattr(user, "avatar_path", ""):
        return ""
    return f"/api/auth/avatar/{user.id}?v={int(user.updated_at or 0)}"


def _user_payload(user) -> dict:
    nickname = (getattr(user, "nickname", "") or "").strip()
    return {
        "id": user.id,
        "uid": user.id,
        "username": user.username,
        "nickname": nickname,
        "display_name": nickname or user.username,
        "avatar_url": _avatar_url(user),
        "role": user.role,
        "status": user.status,
    }


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    user = await get_user_by_username(body.username.strip())
    if not user or not verify_password(body.password, user.password_hash):
        raise AppError(401, "用户名或密码错误")
    if user.status != "active":
        raise AppError(403, "用户已被禁用")
    set_session_cookie(response, user.id)
    return {"success": True, "user": _user_payload(user)}


@router.post("/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"success": True}


@router.get("/me")
async def me(request: Request):
    user = await get_current_user(request)
    return _user_payload(user)


@router.patch("/profile")
async def update_profile(request: Request, body: UpdateProfileRequest):
    user = await get_current_user(request)
    username = body.username.strip()
    nickname = body.nickname.strip()
    if len(username) < 3:
        raise AppError(400, "用户名至少 3 位")
    if len(username) > 191 or len(nickname) > 191:
        raise AppError(400, "用户名或昵称过长")
    existing = await get_user_by_username(username)
    if existing and existing.id != user.id:
        raise AppError(400, "用户名已存在")
    await update_user_profile(user.id, username, nickname)
    updated = await get_user_by_id(user.id)
    return {"success": True, "user": _user_payload(updated or user)}


@router.post("/avatar")
async def upload_avatar(request: Request, avatar: UploadFile = File(...)):
    user = await get_current_user(request)
    data = await avatar.read(MAX_AVATAR_BYTES + 1)
    try:
        stored_path = save_user_avatar(user.id, data)
    except ValueError as exc:
        raise AppError(400, str(exc)) from exc
    await update_user_avatar(user.id, stored_path)
    updated = await get_user_by_id(user.id)
    return {"success": True, "user": _user_payload(updated or user)}


@router.delete("/avatar")
async def remove_avatar(request: Request):
    user = await get_current_user(request)
    delete_user_avatar(user.avatar_path)
    await update_user_avatar(user.id, "")
    updated = await get_user_by_id(user.id)
    return {"success": True, "user": _user_payload(updated or user)}


@router.get("/avatar/{user_id}")
async def get_avatar(request: Request, user_id: str):
    current = await get_current_user(request)
    if current.id != user_id and current.role != "admin":
        raise AppError(403, "无权访问该头像")
    target = await get_user_by_id(user_id)
    avatar_path = resolve_user_avatar(target.avatar_path if target else "")
    if not avatar_path or not avatar_path.is_file():
        raise AppError(404, "头像不存在")
    return FileResponse(avatar_path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/change-password")
async def change_password(request: Request, body: ChangePasswordRequest):
    user = await get_current_user(request)
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(400, "当前密码不正确")
    if len(body.new_password.strip()) < 6:
        raise AppError(400, "新密码至少 6 位")
    await update_user_password(user.id, hash_password(body.new_password.strip()))
    return {"success": True, "updated_at": time.time()}
