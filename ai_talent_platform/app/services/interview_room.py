"""面试间 WebRTC 信令房间（进程内存态，FIFO 中继，离线可测）。

标签：[Agent组] [P1]

协议（全部 JSON）：
- server -> client: {"type":"joined","role","peer_connected"}
                 / {"type":"peer-joined","role"} / {"type":"peer-left","role"}
                 / {"type":"error","code","message"}（随后 close）
- client -> server: {"type":"offer"|"answer"|"ice"|"chat"|"leave", ...}
  服务器注入 "from"=<本端 role> 后原样中继给对端（不校验 SDP 内容）。
角色占用：token -> {role: ws}，同角色重复进入拒绝（4409）；
候选人持 token 进（默认 role=candidate），面试官由邀约详情取 token 加 ?role=interviewer。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

RELAY_TYPES = {"offer", "answer", "ice", "chat", "leave"}
ROLES = ("candidate", "interviewer")


def peer_role(role: str) -> str:
    return "interviewer" if role == "candidate" else "candidate"


class InterviewRoomManager:
    """token -> {role: WebSocket} 的内存注册表 + 消息中继。"""

    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, WebSocket]] = {}
        self._completed: set[str] = set()  # 已完成双人会面的 token（避免重复记账）

    # ---------------------------------------------------------------- 注册
    def roles(self, token: str) -> set[str]:
        return set(self._rooms.get(token) or {})

    async def join(self, token: str, role: str, ws: WebSocket) -> tuple[bool, str | None]:
        """注册入房；(False, 原因) 表示角色已被占用。"""
        room = self._rooms.setdefault(token, {})
        if role in room and room[role] is not ws:
            return False, "该角色已在面试间中（可能在其他标签页）"
        room[role] = ws
        return True, None

    def leave(self, token: str, role: str, ws: WebSocket | None = None) -> WebSocket | None:
        """离房；返回仍在房内的对端 socket（用于通知 peer-left）。"""
        room = self._rooms.get(token)
        if not room:
            return None
        current = room.get(role)
        if ws is not None and current is not ws:
            return current  # 旧连接残留，不动新连接
        room.pop(role, None)
        if not room:
            self._rooms.pop(token, None)
            return None
        return next(iter(room.values()), None)

    def mark_completed(self, token: str) -> bool:
        """双人曾同房则标记完成；首次返回 True（调用方据此落库一次）。"""
        if token in self._completed:
            return False
        self._completed.add(token)
        return True

    # ---------------------------------------------------------------- 中继
    async def relay(self, token: str, from_role: str, message: dict[str, Any]) -> bool:
        """把消息转发给对端；无对端返回 False。"""
        room = self._rooms.get(token) or {}
        target = room.get(peer_role(from_role))
        if target is None:
            return False
        payload = {**message, "from": from_role}
        try:
            await target.send_json(payload)
            return True
        except Exception as exc:  # noqa: BLE001 —— 对端断开由其自身连接处理
            logger.info("面试间中继失败 token=%s: %s", token, exc)
            return False

    async def notify(self, token: str, role: str, message: dict[str, Any]) -> bool:
        """向指定角色发送服务端事件（joined/peer-joined/peer-left/error）。"""
        ws = (self._rooms.get(token) or {}).get(role)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.info("面试间通知失败 token=%s role=%s: %s", token, role, exc)
            return False

    async def notify_peer(self, token: str, from_role: str, message: dict[str, Any]) -> bool:
        return await self.notify(token, peer_role(from_role), message)


# 进程级单例（测试可直接替换实例）
room_manager = InterviewRoomManager()
