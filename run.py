"""一键启动脚本。

用法：双击「启动.bat」，或在项目根目录执行 `python run.py`。
职责：确保虚拟环境与依赖 → 选一个可用的本地端口 → 启动 uvicorn → 自动打开浏览器。
仅监听 127.0.0.1，不对外暴露端口（NFR-02）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():  # Linux / macOS 兼容
    VENV_PY = ROOT / ".venv" / "bin" / "python"


def ensure_env() -> None:
    if VENV_PY.exists():
        return
    print("[1/2] 首次运行：创建 Python 虚拟环境…", flush=True)
    subprocess.check_call([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=str(ROOT))
    print("[2/2] 安装依赖（首次约需几分钟）…", flush=True)
    subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(ROOT))
    print("环境就绪。", flush=True)


def free_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("8765–8784 端口均被占用，请关闭占用程序后重试")


def main() -> None:
    ensure_env()
    # 若不是用项目虚拟环境的解释器启动，切换到它再继续执行（无需额外子进程）
    if Path(sys.executable).resolve() != VENV_PY.resolve():
        os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

    os.chdir(ROOT)
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    os.environ["APP_HOST"] = "127.0.0.1"
    os.environ["APP_PORT"] = str(port)

    print("=" * 62)
    print("  刷题学习助手 · 本地服务")
    print(f"  访问地址：{url}")
    print("  关闭方式：在本窗口按 Ctrl+C，或直接关闭窗口")
    print("  数据位置：data/quiz.db（不进 git，另有每日备份）")
    print("=" * 62, flush=True)

    def open_browser() -> None:
        for _ in range(60):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    webbrowser.open(url)
                    return
            time.sleep(0.4)

    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
