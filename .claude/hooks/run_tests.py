"""
Claude Code 的 Stop hook：AI 每次结束回复时自动运行后端测试。

规则：
- backend/ 下没有未提交的改动时跳过（只是聊天、没改代码，就不浪费时间跑测试）；
- 本机 MySQL(3306) 或 Redis(6379) 没启动时跳过，并提示（环境问题不该让 AI 去"修代码"）；
- 测试失败时以退出码 2 结束，并把失败信息写到 stderr。Claude Code 会把这段信息反馈给 AI，
  AI 会继续修复，而不是直接结束回复；
- 如果这次结束本身就是 hook 触发后的继续（stop_hook_active=true），不再重复拦截，防止死循环。
"""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
TAIL_LINES = 60


def _say(msg: str) -> None:
    sys.stderr.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
    sys.stderr.flush()


def _read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _backend_changed() -> bool:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        ).stdout
        return bool(out.strip())
    except Exception:
        return True  # 拿不到 git 状态时保守处理：照常跑测试


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _python() -> str:
    for candidate in (BACKEND / ".venv" / "Scripts" / "python.exe", BACKEND / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main() -> int:
    if _read_hook_input().get("stop_hook_active"):
        return 0
    if not _backend_changed():
        return 0
    missing = [name for name, port in (("MySQL", 3306), ("Redis", 6379)) if not _port_open(port)]
    if missing:
        print(f"[自动测试] 跳过：{'、'.join(missing)} 未启动")
        return 0

    result = subprocess.run(
        [_python(), "-m", "pytest", "-q", "-x", "-p", "no:warnings", "--no-header"],
        cwd=BACKEND, capture_output=True, timeout=600,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    if result.returncode == 0:
        last = output.strip().splitlines()[-1] if output.strip() else "通过"
        print(f"[自动测试] {last}")
        return 0

    tail = "\n".join(output.strip().splitlines()[-TAIL_LINES:])
    _say("[自动测试] 后端测试未通过，请先修复再结束（遇到第一个失败即停止）：\n" + tail)
    return 2


if __name__ == "__main__":
    sys.exit(main())
