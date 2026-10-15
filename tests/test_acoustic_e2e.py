"""Speaker injection is tested as a lifecycle contract, never as real audio proof."""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location("acoustic_e2e", ROOT / "scripts" / "acoustic_e2e.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def state(*, connected=True, phase="idle", turns=0, messages=None):
    return {"configured": True, "connected": connected, "phase": phase,
            "enabled": phase != "idle", "metrics": {"turns": turns}, "messages": messages or []}


class Client:
    def __init__(self, states):
        self.states = iter(states)
        self.writes = []

    def get(self):
        return next(self.states)

    def post(self, path):
        self.writes.append(path)
        return state()


def test_disconnected_guard_never_starts_or_plays():
    client = Client([state(connected=False)])
    played = []
    result = module().run_e2e(client, lambda *args: played.append(args), Path("input.wav"))
    assert result["success"] is False
    assert "机器人未连接" in result["error"]
    assert played == []
    assert client.writes == []


def test_wrong_device_never_starts_or_plays():
    client = Client([{**state(), 'device_id': 'other-robot'}])
    played = []
    result = module().run_e2e(client, lambda *args: played.append(args), Path('input.wav'),
                             expected_device_id='expected-robot')
    assert not result['success']
    assert '设备身份' in result['error']
    assert not played
    assert not client.writes


def test_two_turns_require_new_real_user_and_assistant_messages():
    first = [{"id": 1, "role": "user", "text": "你好"}, {"id": 2, "role": "assistant", "text": "你好呀"}]
    second = first + [{"id": 3, "role": "user", "text": "你好"}, {"id": 4, "role": "assistant", "text": "又见面了"}]
    client = Client([state(), state(phase="listening"), state(phase="listening", turns=1, messages=first),
                     state(phase="listening", turns=1, messages=first), state(phase="listening", turns=2, messages=second)])
    played = []
    result = module().run_e2e(client, lambda *args: played.append(args), Path("input.wav"), sleep=lambda _: None)
    assert result["success"] is True
    assert len(played) == 2
    assert len(result["turns"]) == 2
    assert result["turns"][1]["assistant"] == ["又见面了"]
    assert client.writes == ["/api/start", "/api/stop"]
    assert result["evidence_kind"] == "synthetic speaker acoustic"


def test_missing_fresh_transcripts_is_failure_and_always_stops():
    client = Client([state(), state(phase="listening"), state(phase="listening", turns=1)])
    result = module().run_e2e(client, lambda *_: None, Path("input.wav"), turns=1)
    assert result["success"] is False
    assert "转写" in result["error"]
    assert client.writes[-1] == "/api/stop"


def test_stop_ack_without_release_confirmation_cannot_pass():
    class PendingClient(Client):
        def post(self, path):
            self.writes.append(path)
            return {**state(), 'cleanup_pending': True}
    messages = [{'id': 1, 'role': 'user', 'text': 'question'},
                {'id': 2, 'role': 'assistant', 'text': 'answer'}]
    client = PendingClient([state(), state(phase='listening'),
                            state(phase='listening', turns=1, messages=messages)])
    result = module().run_e2e(client, lambda *_: None, Path('input.wav'), turns=1)
    assert not result['success']
    assert not result['stop_completed']


def test_disconnect_mid_turn_always_stops():
    client = Client([state(), state(phase="listening"), state(connected=False, phase="error")])
    result = module().run_e2e(client, lambda *_: None, Path("input.wav"), turns=1)
    assert result["success"] is False
    assert client.writes[-1] == "/api/stop"


def test_waiting_for_completed_turn_has_a_deadline_and_stops():
    class WaitingClient:
        calls = 0
        writes = []

        def get(self):
            self.calls += 1
            return state(phase="idle" if self.calls == 1 else "listening")

        def post(self, path):
            self.writes.append(path)

    elapsed = [0.0]
    client = WaitingClient()
    result = module().run_e2e(client, lambda *_: None, Path("input.wav"), turns=1, turn_timeout=1,
                             clock=lambda: elapsed[0], sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    assert result["success"] is False
    assert "超时" in result["error"]
    assert client.writes == ["/api/start", "/api/stop"]


def entrances():
    result = []
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell:
        result.append([powershell, "-NoProfile", "-File", str(ROOT / "scripts" / "acoustic-e2e.ps1")])
    git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
    shell = str(git_bash) if sys.platform == "win32" and git_bash.is_file() else shutil.which("sh")
    if shell:
        result.append([shell, (ROOT / "scripts" / "acoustic-e2e.sh").as_posix()])
    return result


@pytest.mark.parametrize("arguments,code", [(["--help"], 0), (["--turns", "invalid"], 2)])
def test_platform_entrances_forward_arguments_and_exit_codes(arguments, code):
    commands = entrances()
    if len(commands) < 2:
        pytest.skip("Both platform interpreters are required for entrance equivalence.")
    environment = {**os.environ, "PYTHON": Path(sys.executable).as_posix(), "PYTHONIOENCODING": "utf-8"}
    results = [subprocess.run(command + arguments, capture_output=True, text=True,
                              encoding="utf-8", env=environment, timeout=20) for command in commands]
    assert all(result.returncode == code for result in results)
    if code == 0:
        assert results[0].stdout == results[1].stdout
        assert "--input-wav" in results[0].stdout
    else:
        assert all("--turns" in result.stderr for result in results)


def test_platform_entrances_return_127_if_python_missing():
    commands = entrances()
    if len(commands) < 2:
        pytest.skip("Both platform interpreters are required for entrance equivalence.")
    environment = {**os.environ, "PYTHON": "watche-python-that-does-not-exist"}
    results = [subprocess.run(command + ["--help"], capture_output=True, env=environment,
                              timeout=20) for command in commands]
    assert all(result.returncode == 127 for result in results)
