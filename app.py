"""Daemon-managed local UI; every device operation uses the injected channel."""
from __future__ import annotations

import asyncio
import os
import socket
import webbrowser
from pathlib import Path

import uvicorn
from watcherobot.application import ApplicationContext

from service import ConversationService, create_web_app


ROOT = Path(__file__).resolve().parent


async def _finish(task: asyncio.Task, logger) -> None:
    """Give cleanup time to release device resources, then retire the task."""
    try:
        await asyncio.wait_for(asyncio.shield(task), 45)
    except asyncio.TimeoutError:
        logger.warning("Application 清理等待超时，正在取消剩余任务。")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    except (asyncio.CancelledError, Exception):
        # The main loop propagates worker failures. Cleanup must finish even if
        # that already-failed worker is awaited a second time here.
        pass


async def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        async with ApplicationContext.from_environment() as context:
            service = ConversationService(context)
            server = uvicorn.Server(uvicorn.Config(
                create_web_app(service, ROOT / "web"),
                host="127.0.0.1", port=port, access_log=False,
                log_level="warning", timeout_graceful_shutdown=5,
            ))
            http_task = asyncio.create_task(server.serve(sockets=[listener]), name="voice-ui")
            runner = asyncio.create_task(service.run(), name="voice-lifecycle")
            controls = asyncio.create_task(service.desktop_controls(), name="voice-desktop")
            tasks = (http_task, runner, controls)
            url = f"http://127.0.0.1:{port}"
            url_file = None
            try:
                while not server.started:
                    for task in tasks:
                        if task.done():
                            await task
                            return
                    await asyncio.sleep(.02)
                configured_file = os.environ.get("GROK_VOICE_URL_FILE", "").strip()
                if configured_file:
                    url_file = Path(configured_file)
                    url_file.parent.mkdir(parents=True, exist_ok=True)
                    url_file.write_text(url + "\n", encoding="utf-8")
                context.logger.info("Watche Voice 对话页面：%s", url)
                if os.environ.get("GROK_VOICE_OPEN_BROWSER", "1") != "0":
                    await asyncio.to_thread(webbrowser.open, url)
                while not context.shutdown_requested and not any(task.done() for task in tasks):
                    await asyncio.sleep(.1)
                for task in tasks:
                    if task.done():
                        await task
            finally:
                service.shutdown()
                controls.cancel()
                await asyncio.gather(controls, return_exceptions=True)
                await _finish(runner, context.logger)
                server.should_exit = True
                await _finish(http_task, context.logger)
                if url_file:
                    try:
                        if url_file.read_text(encoding="utf-8").strip() == url:
                            url_file.unlink()
                    except OSError:
                        context.logger.warning("未能清理本次 Application URL 文件。")


if __name__ == "__main__":
    asyncio.run(main())
