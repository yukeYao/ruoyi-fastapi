"""阶段1验收测试：项目骨架（配置、数据库、Redis、统一返回、异常处理）"""
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from app.core.response import ajax_error, ajax_success, model_to_camel_dict, table_data_info
from app.models import SysUser
from main import app


def test_docs_available():
    """Swagger 文档能正常访问，证明 main.py 能启动"""
    client = TestClient(app)
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_sys_user_seed_data_imported():
    """init_db 脚本导入的种子数据里应包含 admin / ry 两个用户"""
    db = SessionLocal()
    try:
        usernames = {u.user_name for u in db.query(SysUser).all()}
        assert {"admin", "ry"}.issubset(usernames)
    finally:
        db.close()


def test_model_to_camel_dict_converts_fields():
    """ORM 对象转 camelCase 字典：字段名要转换，时间要格式化成字符串"""
    db = SessionLocal()
    try:
        admin = db.query(SysUser).filter(SysUser.user_name == "admin").one()
        data = model_to_camel_dict(admin)
        assert data["userName"] == "admin"
        assert "user_name" not in data
        assert isinstance(data["createTime"], str)
    finally:
        db.close()


def test_redis_connection_with_resp2():
    """本机 Redis 5.0.14 不支持 RESP3，protocol=2 的客户端应能正常 ping 通"""
    assert redis_client.ping() is True


def test_ajax_result_helpers():
    assert ajax_success()["code"] == 200
    assert ajax_success(data={"a": 1})["data"] == {"a": 1}
    assert ajax_success(token="xxx")["token"] == "xxx"
    assert ajax_error("出错了")["code"] == 500
    assert table_data_info([], 0)["rows"] == []
