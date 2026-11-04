"""
flush_oper_log_for_test：测试里等待异步操作日志写完的受控方法（替代"轮询若干秒"）。
生产代码不调用它；@log 仍然异步写库，接口不等日志。
"""
import threading
import time

import pytest

from app.core import oper_log


def test_flush_waits_for_slow_background_write(monkeypatch):
    written = []
    release = threading.Event()

    def slow_write(*args):
        release.wait(5)
        time.sleep(0.2)
        written.append(args[0])

    monkeypatch.setattr(oper_log, "_write_log", slow_write)
    oper_log.record_oper_log("冒烟标题", 0, None, {}, None, 0, "", 0)
    # 异步：提交后立即返回，日志还没写
    assert written == []
    release.set()
    oper_log.flush_oper_log_for_test()
    assert written == ["冒烟标题"]


def test_flush_waits_for_writes_submitted_while_waiting(monkeypatch):
    """等待期间又提交的日志也要等到：例如一个日志任务写完前，另一个请求又产生了日志"""
    written = []

    def write(*args):
        time.sleep(0.1)
        written.append(args[0])
        if args[0] == "第一条":
            oper_log.record_oper_log("第二条", 0, None, {}, None, 0, "", 0)

    monkeypatch.setattr(oper_log, "_write_log", write)
    oper_log.record_oper_log("第一条", 0, None, {}, None, 0, "", 0)
    oper_log.flush_oper_log_for_test()
    assert written == ["第一条", "第二条"]


def test_flush_times_out_instead_of_hanging(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(oper_log, "_write_log", lambda *args: release.wait(5))
    oper_log.record_oper_log("卡住", 0, None, {}, None, 0, "", 0)
    try:
        with pytest.raises(TimeoutError):
            oper_log.flush_oper_log_for_test(timeout=0.2)
    finally:
        release.set()
        oper_log.flush_oper_log_for_test()


def test_synchronous_write_is_not_tracked(monkeypatch):
    """background=False（智能助手）同步写，不进入待写集合"""
    monkeypatch.setattr(oper_log, "_write_log", lambda *args: None)
    oper_log.record_oper_log("同步", 0, None, {}, None, 0, "", 0, background=False)
    assert not oper_log._pending
