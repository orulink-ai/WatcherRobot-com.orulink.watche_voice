"""Entry lifecycle uses only injected channels and cleans up on server failure."""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_entry(monkeypatch, *, fail_server=False):
    context = types.SimpleNamespace(shutdown_requested=True, logger=Mock())

    class ContextManager:
        @classmethod
        def from_environment(cls):
            return cls()

        async def __aenter__(self):
            return context

        async def __aexit__(self, *args):
            return None

    class Service:
        def __init__(self, received):
            assert received is context
            self.closed = asyncio.Event()
            self.controls_cancelled = False
            self.run_finished = False
            services.append(self)

        async def run(self):
            await self.closed.wait()
            self.run_finished = True

        async def desktop_controls(self):
            try:
                await asyncio.Event().wait()
            finally:
                self.controls_cancelled = True

        def shutdown(self):
            self.closed.set()

    class Server:
        started = False
        should_exit = False

        def __init__(self, config):
            self.config = config

        async def serve(self, sockets):
            assert sockets[0].getsockname()[0] == "127.0.0.1"
            if fail_server:
                raise RuntimeError("server failed")
            self.started = True
            while not self.should_exit:
                await asyncio.sleep(.001)

    services = []
    application_module = types.ModuleType("watcherobot.application")
    application_module.ApplicationContext = ContextManager
    monkeypatch.setitem(sys.modules, "watcherobot.application", application_module)
    service_module = types.ModuleType("service")
    service_module.ConversationService = Service
    service_module.create_web_app = lambda service, web: object()
    monkeypatch.setitem(sys.modules, "service", service_module)
    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", Server)
    spec = importlib.util.spec_from_file_location("watche_voice_entry_test", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, services, context


def test_shutdown_releases_workers_and_removes_only_its_url_file(monkeypatch, tmp_path):
    module, services, context = load_entry(monkeypatch)
    url_file = tmp_path / "voice.url"
    monkeypatch.setenv("GROK_VOICE_OPEN_BROWSER", "0")
    monkeypatch.setenv("GROK_VOICE_URL_FILE", str(url_file))
    opened = Mock()
    monkeypatch.setattr(module.webbrowser, "open", opened)
    asyncio.run(module.main())
    assert services[0].run_finished
    assert services[0].controls_cancelled
    assert not url_file.exists()
    opened.assert_not_called()
    assert context.logger.info.called


def test_failed_web_server_still_closes_service(monkeypatch):
    module, services, _ = load_entry(monkeypatch, fail_server=True)
    monkeypatch.setenv("GROK_VOICE_OPEN_BROWSER", "0")
    with pytest.raises(RuntimeError, match="server failed"):
        asyncio.run(module.main())
    assert services[0].run_finished
    assert services[0].controls_cancelled


def test_shutdown_preserves_url_file_replaced_by_another_run(monkeypatch, tmp_path):
    module, services, context = load_entry(monkeypatch)
    url_file = tmp_path / "voice.url"
    monkeypatch.setenv("GROK_VOICE_OPEN_BROWSER", "0")
    monkeypatch.setenv("GROK_VOICE_URL_FILE", str(url_file))
    context.logger.info.side_effect = lambda *args: url_file.write_text("http://127.0.0.1:9999\n", encoding="utf-8")
    asyncio.run(module.main())
    assert services[0].run_finished
    assert url_file.read_text(encoding="utf-8") == "http://127.0.0.1:9999\n"
