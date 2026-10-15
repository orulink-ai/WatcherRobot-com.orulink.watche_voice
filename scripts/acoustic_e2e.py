"""Speaker-to-robot smoke test; never substitutes a synthetic Bot response."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path


class E2EError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise E2EError("测试接口不允许 HTTP 重定向。")


class HTTPClient:
    def __init__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise E2EError("--url 必须是当前 Application 的本机 HTTP 根地址。")
        self.url = url.rstrip("/")
        self.timeout = 10.0
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, path: str, post: bool = False):
        request = urllib.request.Request(
            self.url + path, data=b"{}" if post else None,
            headers={"Origin": self.url, "Content-Type": "application/json"},
            method="POST" if post else "GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            raise E2EError(f"Application {path} 返回 HTTP {error.code}，请检查页面状态。") from error
        except urllib.error.URLError as error:
            raise E2EError("无法连接 Application，请确认它仍在运行且 URL 正确。") from error
        if len(raw) > 2 * 1024 * 1024:
            raise E2EError("Application 状态响应超出上限。")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise E2EError("Application 未返回有效 JSON。") from error
        if not isinstance(result, dict):
            raise E2EError("Application 状态不是 JSON 对象。")
        return result

    def get(self):
        return self._request("/api/state")

    def post(self, path: str):
        return self._request(path, True)


def _healthy(state):
    if not state.get("configured"):
        raise E2EError("尚未配置可用 Grok Bot，请在页面确认本机登录并选择 Bot。")
    if not state.get("connected"):
        raise E2EError("机器人未连接；请先通过 SDK 完成连接，本工具不会配对或切换固件模式。")
    if state.get("phase") == "error" or state.get("error"):
        raise E2EError("Application 报错，请先检查页面和应用日志。")


def _wait(client, predicate, deadline, clock, sleep, device_id=None):
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise E2EError("本轮声学测试超时，未观察到完整语音轮次。")
        client.timeout = min(10.0, remaining)
        current = client.get()
        _healthy(current)
        if device_id is not None and current.get('device_id') != device_id:
            raise E2EError('设备身份已改变，停止本轮声学测试。')
        if predicate(current):
            return current
        if not current.get("enabled"):
            raise E2EError("Application 语音对话已停止，本轮未完成。")
        sleep(min(.5, max(0.0, deadline - clock())))


def run_e2e(client, player, input_wav: Path, *, turns=2, turn_timeout=300,
            expected_device_id=None, clock=time.monotonic, sleep=time.sleep):
    report = {"success": False, "evidence_kind": "synthetic speaker acoustic",
              "scope": "真实设备麦克风 → JoyInside STT → 所选 Grok Bot → JoyInside TTS 完成事件",
              "limitations": "由电脑喇叭播放录音；不等于人工对话或人工听感验收，不证明扬声器听感质量。",
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "requested_turns": turns, "turns": [], "stop_attempted": False}
    started = False
    beginning = clock()
    try:
        if not 1 <= turns <= 20 or not 1 <= turn_timeout <= 300:
            raise E2EError("--turns 须为 1–20，--turn-timeout 须为 1–300 秒。")
        initial = client.get()
        _healthy(initial)
        if expected_device_id is not None and initial.get('device_id') != expected_device_id:
            raise E2EError('设备身份不匹配，未开始声学测试。')
        if initial.get("enabled") or initial.get("text_active"):
            raise E2EError("请先结束正在进行的对话或文字验证，避免测试接管已有会话。")
        report["device_id"] = initial.get("device_id")
        report["bot_name"] = initial.get("bot_name") or initial.get("bot")
        started = True  # A failed start may have reached the server; always stop.
        client.post("/api/start")
        for index in range(turns):
            turn_start = clock()
            deadline = turn_start + turn_timeout
            listening = _wait(client, lambda row: row.get("phase") == "listening",
                              deadline, clock, sleep, report['device_id'])
            baseline = int(listening.get("metrics", {}).get("turns", 0))
            old_ids = {str(message.get("id")) for message in listening.get("messages", [])}
            player(input_wav, max(.1, deadline - clock()))
            completed = _wait(client, lambda row: int(row.get("metrics", {}).get("turns", 0)) > baseline,
                              deadline, clock, sleep, report['device_id'])
            fresh = [message for message in completed.get("messages", [])
                     if message.get("id") is not None and str(message["id"]) not in old_ids]
            transcripts = [row["text"].strip() for row in fresh
                           if row.get("role") == "user" and isinstance(row.get("text"), str) and row["text"].strip()]
            answers = [row["text"].strip() for row in fresh
                       if row.get("role") == "assistant" and isinstance(row.get("text"), str) and row["text"].strip()]
            if not transcripts or not answers:
                raise E2EError("轮次递增但缺少本轮新鲜的真实转写或 assistant 回复，不能判定声学闭环通过。")
            report["turns"].append({"index": index + 1, "transcripts": transcripts, "assistant": answers,
                                    "completed_turn_counter": completed["metrics"]["turns"],
                                    "elapsed_seconds": round(clock() - turn_start, 3)})
        report["success"] = True
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = "用户中断测试。" if isinstance(error, KeyboardInterrupt) else str(error)
    finally:
        if started:
            report["stop_attempted"] = True
            try:
                client.timeout = 45.0
                stopped = client.post("/api/stop")
                if (not isinstance(stopped, dict) or stopped.get("cleanup_pending")
                        or stopped.get("enabled") or stopped.get("text_active")
                        or stopped.get("phase") != "idle"):
                    raise E2EError("停止请求返回后仍未确认语音资源释放。")
                report["stop_completed"] = True
            except Exception as error:
                report["success"] = False
                report["stop_completed"] = False
                report["stop_error"] = str(error)
        report["elapsed_seconds"] = round(clock() - beginning, 3)
    return report


def wave_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as recording:
            duration = recording.getnframes() / recording.getframerate()
            if recording.getcomptype() != "NONE" or duration <= 0:
                raise E2EError("--input-wav 必须是非空 PCM WAV。")
            return duration
    except (OSError, wave.Error, EOFError) as error:
        raise E2EError("无法读取 --input-wav，请提供有效的本地 PCM WAV。") from error


def play_wav(path: Path, timeout: float) -> None:
    duration = wave_duration(path)
    if duration + .2 >= timeout:
        raise E2EError("录音时长超出本轮剩余时间。")
    system = platform.system()
    if system == "Windows":
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        try:
            time.sleep(duration + .2)
        finally:
            winsound.PlaySound(None, 0)
    else:
        command = "afplay" if system == "Darwin" else "aplay" if system == "Linux" else None
        if not command or not shutil.which(command):
            raise E2EError("找不到系统 WAV 播放器（macOS afplay / Linux aplay）。")
        subprocess.run([command, str(path)], check=True, timeout=timeout)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="真实机器人声学两轮测试；需要已选 Bot、设备在线和本地 WAV。")
    parser.add_argument("--url", required=True, help="当前 Application 的本机根 URL")
    parser.add_argument("--input-wav", required=True, type=Path, help="旁边喇叭播放的本地 PCM WAV")
    parser.add_argument("--turns", type=int, default=2)
    parser.add_argument('--expected-device-id', help='指定台架设备身份；不匹配时禁止开始或播放录音')
    parser.add_argument("--turn-timeout", type=int, default=300, help="每轮上限秒数，1–300，默认 300")
    parser.add_argument("--output", required=True, type=Path, help="保存真实结果的本地 JSON 文件")
    args = parser.parse_args(argv)
    try:
        input_wav = args.input_wav.resolve(strict=True)
        if wave_duration(input_wav) + .2 >= args.turn_timeout:
            raise E2EError("录音时长必须小于每轮超时。")
        report = run_e2e(HTTPClient(args.url), play_wav, input_wav,
                         turns=args.turns, turn_timeout=args.turn_timeout,
                         expected_device_id=args.expected_device_id)
    except (Exception, KeyboardInterrupt) as error:
        report = {"success": False, "evidence_kind": "synthetic speaker acoustic", "error": str(error)}
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print("无法保存结果 JSON，请检查 --output 路径。", file=sys.stderr)
        return 3
    print("声学测试通过。" if report["success"] else "声学测试失败：" + report.get("error", report.get("stop_error", "未知原因")))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
