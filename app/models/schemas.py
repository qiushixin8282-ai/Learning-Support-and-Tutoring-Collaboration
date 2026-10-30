"""Web API 的输入输出模型与统一角色、动作定义。"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Role(StrEnum):
    """平台支持的五类角色，值与数据库、JWT 中的值保持一致。"""

    STUDENT = "学生"
    TEACHING_ASSISTANT = "助教"
    MENTOR = "导师"
    GROUP_LEADER = "课题组负责人"
    SYSTEM_ADMIN = "系统管理员"


class Action(StrEnum):
    """所有业务权限只按这四种动作表达。"""

    VIEW = "view"
    EDIT = "edit"
    REVIEW = "review"
    PUBLISH = "publish"


class LoginRequest(BaseModel):
    """登录接口接收的数据；密码不会写入审计详情。"""

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class UserView(BaseModel):
    id: int
    email: str
    display_name: str
    role: Role


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserView
