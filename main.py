import time
import re
import asyncio

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api import AstrBotConfig


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self._waking: dict[str, float] = {}
        self._compiled_regexes: list[re.Pattern] = []
        self._build_regexes()
        self._cleanup_task: asyncio.Task | None = None

        self._keyword_set: set[str] = set()
        self._build_keywords()

    def _build_regexes(self):
        self._compiled_regexes.clear()
        for pattern in self.config.get("wake_regexes", []):
            try:
                self._compiled_regexes.append(re.compile(pattern))
            except re.error as e:
                logger.warning(f"[唤醒增强] 正则编译失败: {pattern!r}, {e}")

    def _build_keywords(self):
        self._keyword_set.clear()
        for kw in self.config.get("wake_keywords", []):
            if kw:
                self._keyword_set.add(kw)

    async def _periodic_cleanup(self):
        while self._waking:
            await asyncio.sleep(10)
            now = time.time()
            duration = self._active_duration()
            expired = [
                gid for gid, last in self._waking.items()
                if now - last > duration
            ]
            for gid in expired:
                self._waking.pop(gid, None)
                logger.info(f"[唤醒增强] 群 {gid} 持续唤醒超时，自动退出")
        self._cleanup_task = None

    def _start_cleanup(self):
        if self._cleanup_task is None and self._waking:
            self._cleanup_task = asyncio.get_event_loop().create_task(
                self._periodic_cleanup()
            )

    def _active_duration(self) -> float:
        ca = self.config.get("continuous_awakening", {})
        return float(ca.get("duration", 60))

    def _active_enabled(self) -> bool:
        ca = self.config.get("continuous_awakening", {})
        return bool(ca.get("enable", True))

    def _reset_on_reply(self) -> bool:
        ca = self.config.get("continuous_awakening", {})
        return bool(ca.get("reset_on_reply", True))

    def _check_whitelist(self, group_id: str) -> bool:
        whitelist = self.config.get("whitelist", [])
        if not whitelist:
            return True
        return group_id in whitelist

    def _match_wake(self, text: str) -> bool:
        for kw in self._keyword_set:
            if kw in text:
                return True
        for regex in self._compiled_regexes:
            if regex.search(text):
                return True
        return False

    @filter.command("wbegin")
    async def wbegin(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result("私聊无需持续唤醒~")
            return
        group_id = event.message_obj.group_id
        if not self._check_whitelist(group_id):
            yield event.plain_result("此群聊不在白名单内~")
            return

        if group_id in self._waking:
            yield event.plain_result("已经在持续唤醒状态啦~")
            return

        self._waking[group_id] = time.time()
        self._start_cleanup()
        yield event.plain_result("进入持续唤醒~")

    @filter.command("wexit")
    async def wexit(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result("私聊无需此功能~")
            return
        group_id = event.message_obj.group_id
        if group_id not in self._waking:
            yield event.plain_result("当前不在持续唤醒状态~")
            return
        self._waking.pop(group_id, None)
        yield event.plain_result("已退出持续唤醒~")

    @filter.command("wgid")
    async def wgid(self, event: AstrMessageEvent):
        if event.is_private_chat():
            yield event.plain_result("私聊没有群聊 ID~")
            return
        group_id = event.message_obj.group_id
        yield event.plain_result(f"当前群聊 ID: {group_id}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if event.is_private_chat():
            if self.config.get("enable_private_chat", True):
                if self._match_wake(event.message_str):
                    event.is_at_or_wake_command = True
            return

        group_id = event.message_obj.group_id
        if not self._check_whitelist(group_id):
            return

        # 如果是指令（以/开头），不处理，让命令处理器处理
        if event.message_str and event.message_str.strip().startswith("/"):
            return

        if self._match_wake(event.message_str):
            event.is_at_or_wake_command = True
            if self._active_enabled():
                self._waking[group_id] = time.time()
                self._start_cleanup()

        if group_id in self._waking:
            if time.time() - self._waking[group_id] > self._active_duration():
                self._waking.pop(group_id, None)
                return

            event.is_at_or_wake_command = True
            if self._reset_on_reply():
                self._waking[group_id] = time.time()

    async def terminate(self):
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._waking.clear()
        self._compiled_regexes.clear()
        self._keyword_set.clear()