"""本地初始化系统管理员账号的命令行工具。"""

from __future__ import annotations

import argparse
import getpass

from app.models.schemas import Role
from app.services.database import connection, initialize_database
from app.services.security import hash_password


def main() -> None:
    """交互式创建管理员，避免把演示密码写入源码或 README。"""

    parser = argparse.ArgumentParser(description="初始化学习支持协作实验室管理员账号")
    parser.add_argument("--email", required=True, help="管理员邮箱（本地演示可使用虚构地址）")
    parser.add_argument("--name", default="系统管理员", help="显示名称")
    args = parser.parse_args()
    password = getpass.getpass("请输入管理员密码（至少 8 位）：")
    if len(password) < 8:
        raise SystemExit("密码至少需要 8 位")

    initialize_database()
    with connection() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE email = ?", (args.email,)).fetchone()
        if exists:
            raise SystemExit("该邮箱已存在，请使用其他邮箱或先在数据库中处理")
        conn.execute(
            "INSERT INTO users(email, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
            (args.email, args.name, hash_password(password), Role.SYSTEM_ADMIN.value),
        )
    print("系统管理员账号已创建。请运行 uvicorn app.api.main:app --reload 启动服务。")


if __name__ == "__main__":
    main()
