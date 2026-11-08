"""账号级下载尝试上限与熔断状态；在跨进程账号锁内使用，不保存账号或口令。"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass
class ChaosLibAccess:
    """运行重启不会清空每日预算、尝试间隔或站点封禁冷却。"""

    directory: Path
    daily_limit: int | None = None
    minimum_interval: int = 120

    def authentication(self, *, human: bool, now: datetime | None = None) -> str:
        """独立限制登录频率；人工认证不能清除限流或封禁，不占论文下载次数。"""
        now = now or datetime.now(UTC)
        state = self._read()
        if state.get("reason") == "blocked_automation":
            return "account_blocked_requires_manual"
        until = state.get("blocked_until", "")
        if until and now < datetime.fromisoformat(until) and state.get("reason") != "authentication_required":
            return "account_cooldown:" + until
        last = state.get("last_login", "")
        if not human and last and (now - datetime.fromisoformat(last)).total_seconds() < 3600:
            return "login_cooldown"
        state["last_login"] = now.isoformat()
        self._write(state)
        return ""

    def authenticated(self) -> None:
        """人工登录成功只清除认证冷却，保留当日论文次数及其他封禁。"""
        state = self._read()
        if state.get("reason") == "authentication_required":
            state.pop("blocked_until", None)
            state.pop("reason", None)
            self._write(state)

    def _restriction(self, state: dict[str, str | int], now: datetime) -> str:
        if state.get("reason") == "blocked_automation":
            return "account_blocked_requires_manual"
        until = state.get("blocked_until", "")
        if until and now < datetime.fromisoformat(until):
            return "account_cooldown:" + until
        day = now.date().isoformat()
        count = int(state.get("count", 0)) if state.get("day") == day else 0
        if self.daily_limit is not None and count >= self.daily_limit:
            return "daily_attempt_limit"
        last = state.get("last_attempt", "")
        if last and (now - datetime.fromisoformat(last)).total_seconds() < self.minimum_interval:
            return "attempt_interval"
        return ""

    def available(self, now: datetime | None = None) -> str:
        """只检查额度和暂停，不消耗次数；等待本地扩展授权不算出版商获取尝试。"""
        return self._restriction(self._read(), now or datetime.now(UTC))

    def reserve(self, now: datetime | None = None) -> str:
        """即将打开出版商时占用一次额度；之后失败也计次，调用方必须持有账号锁。"""
        now = now or datetime.now(UTC)
        state = self._read()
        blocked = self._restriction(state, now)
        if blocked:
            return blocked
        day = now.date().isoformat()
        count = int(state.get("count", 0)) if state.get("day") == day else 0
        state.update(day=day, count=count + 1, last_attempt=now.isoformat())
        self._write(state)
        return ""

    def block(self, reason: str, now: datetime | None = None) -> None:
        """限流冷却 30 分钟；封禁冷却 24 小时；认证失败一小时内不自动重试。"""
        now = now or datetime.now(UTC)
        seconds = 86400 if reason == "blocked_automation" else 1800 if "rate_limited" in reason else 3600
        state = self._read()
        state.update(blocked_until=(now + timedelta(seconds=seconds)).isoformat(), reason=reason)
        self._write(state)

    def confirm_restored(self) -> None:
        """仅由人工明确确认账号恢复的入口调用；不联网、不清除每日次数或请求间隔。"""
        state = self._read()
        state.pop("blocked_until", None)
        state.pop("reason", None)
        self._write(state)

    def status(self) -> dict[str, str | int]:
        """只读账号暂停、冷却与次数，不包含任何凭证或会话。"""
        return self._read()

    def _read(self) -> dict[str, str | int]:
        path = self.directory / "access.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _write(self, state: dict[str, str | int]) -> None:
        path = self.directory / "access.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)
