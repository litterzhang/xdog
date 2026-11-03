"""WeChat protocol types ported from openclaw-weixin src/api/types.ts.

All dataclasses are frozen for immutability.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class MessageType(IntEnum):
    NONE = 0
    USER = 1
    BOT = 2


class MessageItemType(IntEnum):
    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class MessageState(IntEnum):
    NEW = 0
    GENERATING = 1
    FINISH = 2


@dataclass(frozen=True)
class TextItem:
    text: str = ""


@dataclass(frozen=True)
class CDNMedia:
    encrypt_query_param: str = ""
    aes_key: str = ""
    encrypt_type: int = 0
    full_url: str = ""


@dataclass(frozen=True)
class ImageItem:
    media: CDNMedia | None = None
    thumb_media: CDNMedia | None = None
    aeskey: str = ""
    url: str = ""
    mid_size: int = 0
    thumb_size: int = 0
    thumb_height: int = 0
    thumb_width: int = 0
    hd_size: int = 0


@dataclass(frozen=True)
class VoiceItem:
    media: CDNMedia | None = None
    encode_type: int = 0
    bits_per_sample: int = 0
    sample_rate: int = 0
    playtime: int = 0
    text: str = ""


@dataclass(frozen=True)
class FileItem:
    media: CDNMedia | None = None
    file_name: str = ""
    md5: str = ""
    len: str = ""


@dataclass(frozen=True)
class VideoItem:
    media: CDNMedia | None = None
    video_size: int = 0
    play_length: int = 0
    video_md5: str = ""
    thumb_media: CDNMedia | None = None
    thumb_size: int = 0
    thumb_height: int = 0
    thumb_width: int = 0


@dataclass(frozen=True)
class RefMessage:
    message_item: MessageItem | None = None
    title: str = ""


@dataclass(frozen=True)
class MessageItem:
    type: int = 0
    create_time_ms: int = 0
    update_time_ms: int = 0
    is_completed: bool = False
    msg_id: str = ""
    ref_msg: RefMessage | None = None
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    voice_item: VoiceItem | None = None
    file_item: FileItem | None = None
    video_item: VideoItem | None = None


@dataclass(frozen=True)
class WeixinMessage:
    seq: int = 0
    message_id: int = 0
    from_user_id: str = ""
    to_user_id: str = ""
    client_id: str = ""
    create_time_ms: int = 0
    update_time_ms: int = 0
    delete_time_ms: int = 0
    session_id: str = ""
    group_id: str = ""
    message_type: int = 0
    message_state: int = 0
    item_list: tuple[MessageItem, ...] = ()
    context_token: str = ""


@dataclass(frozen=True)
class GetUpdatesResp:
    ret: int = 0
    errcode: int = 0
    errmsg: str = ""
    msgs: tuple[WeixinMessage, ...] = ()
    get_updates_buf: str = ""
    longpolling_timeout_ms: int = 0


@dataclass(frozen=True)
class SendMessageReq:
    msg: WeixinMessage | None = None


@dataclass(frozen=True)
class BaseInfo:
    channel_version: str = ""


def _parse_cdn_media(raw: dict[str, Any]) -> CDNMedia | None:
    if not raw:
        return None
    return CDNMedia(
        encrypt_query_param=raw.get("encrypt_query_param", ""),
        aes_key=raw.get("aes_key", ""),
        encrypt_type=raw.get("encrypt_type", 0),
        full_url=raw.get("full_url", ""),
    )


def _parse_text_item(raw: dict[str, Any] | None) -> TextItem | None:
    if not raw:
        return None
    return TextItem(text=raw.get("text", ""))


def _parse_image_item(raw: dict[str, Any] | None) -> ImageItem | None:
    if not raw:
        return None
    return ImageItem(
        media=_parse_cdn_media(raw.get("media", {})),
        thumb_media=_parse_cdn_media(raw.get("thumb_media", {})),
        aeskey=raw.get("aeskey", ""),
        url=raw.get("url", ""),
        mid_size=raw.get("mid_size", 0),
        thumb_size=raw.get("thumb_size", 0),
        thumb_height=raw.get("thumb_height", 0),
        thumb_width=raw.get("thumb_width", 0),
        hd_size=raw.get("hd_size", 0),
    )


def _parse_voice_item(raw: dict[str, Any] | None) -> VoiceItem | None:
    if not raw:
        return None
    return VoiceItem(
        media=_parse_cdn_media(raw.get("media", {})),
        encode_type=raw.get("encode_type", 0),
        bits_per_sample=raw.get("bits_per_sample", 0),
        sample_rate=raw.get("sample_rate", 0),
        playtime=raw.get("playtime", 0),
        text=raw.get("text", ""),
    )


def _parse_file_item(raw: dict[str, Any] | None) -> FileItem | None:
    if not raw:
        return None
    return FileItem(
        media=_parse_cdn_media(raw.get("media", {})),
        file_name=raw.get("file_name", ""),
        md5=raw.get("md5", ""),
        len=raw.get("len", ""),
    )


def _parse_video_item(raw: dict[str, Any] | None) -> VideoItem | None:
    if not raw:
        return None
    return VideoItem(
        media=_parse_cdn_media(raw.get("media", {})),
        video_size=raw.get("video_size", 0),
        play_length=raw.get("play_length", 0),
        video_md5=raw.get("video_md5", ""),
        thumb_media=_parse_cdn_media(raw.get("thumb_media", {})),
        thumb_size=raw.get("thumb_size", 0),
        thumb_height=raw.get("thumb_height", 0),
        thumb_width=raw.get("thumb_width", 0),
    )


def _parse_ref_message(raw: dict[str, Any] | None) -> RefMessage | None:
    if not raw:
        return None
    mi = raw.get("message_item")
    return RefMessage(
        message_item=_parse_message_item(mi) if mi else None,
        title=raw.get("title", ""),
    )


def _parse_message_item(raw: dict[str, Any]) -> MessageItem:
    return MessageItem(
        type=raw.get("type", 0),
        create_time_ms=raw.get("create_time_ms", 0),
        update_time_ms=raw.get("update_time_ms", 0),
        is_completed=raw.get("is_completed", False),
        msg_id=raw.get("msg_id", ""),
        ref_msg=_parse_ref_message(raw.get("ref_msg")),
        text_item=_parse_text_item(raw.get("text_item")),
        image_item=_parse_image_item(raw.get("image_item")),
        voice_item=_parse_voice_item(raw.get("voice_item")),
        file_item=_parse_file_item(raw.get("file_item")),
        video_item=_parse_video_item(raw.get("video_item")),
    )


def parse_weixin_message(raw: dict[str, Any]) -> WeixinMessage:
    """Parse a raw JSON dict into a WeixinMessage."""
    raw_items = raw.get("item_list") or []
    items = tuple(_parse_message_item(i) for i in raw_items)
    return WeixinMessage(
        seq=raw.get("seq", 0),
        message_id=raw.get("message_id", 0),
        from_user_id=raw.get("from_user_id", ""),
        to_user_id=raw.get("to_user_id", ""),
        client_id=raw.get("client_id", ""),
        create_time_ms=raw.get("create_time_ms", 0),
        update_time_ms=raw.get("update_time_ms", 0),
        delete_time_ms=raw.get("delete_time_ms", 0),
        session_id=raw.get("session_id", ""),
        group_id=raw.get("group_id", ""),
        message_type=raw.get("message_type", 0),
        message_state=raw.get("message_state", 0),
        item_list=items,
        context_token=raw.get("context_token", ""),
    )


def parse_get_updates_resp(raw: dict[str, Any]) -> GetUpdatesResp:
    """Parse a raw JSON dict into a GetUpdatesResp."""
    raw_msgs = raw.get("msgs") or []
    msgs = tuple(parse_weixin_message(m) for m in raw_msgs)
    return GetUpdatesResp(
        ret=raw.get("ret", 0),
        errcode=raw.get("errcode", 0),
        errmsg=raw.get("errmsg", ""),
        msgs=msgs,
        get_updates_buf=raw.get("get_updates_buf", ""),
        longpolling_timeout_ms=raw.get("longpolling_timeout_ms", 0),
    )


def send_message_req_to_dict(req: SendMessageReq) -> dict[str, Any]:
    """Serialize a SendMessageReq to a JSON-ready dict."""
    if req.msg is None:
        return {"msg": None}

    msg = req.msg
    item_list = None
    if msg.item_list:
        item_list = []
        for item in msg.item_list:
            d: dict[str, Any] = {"type": item.type}
            if item.text_item is not None:
                d["text_item"] = {"text": item.text_item.text}
            item_list.append(d)

    result: dict[str, Any] = {
        "msg": {
            "from_user_id": msg.from_user_id,
            "to_user_id": msg.to_user_id,
            "client_id": msg.client_id,
            "message_type": msg.message_type,
            "message_state": msg.message_state,
        }
    }
    if item_list:
        result["msg"]["item_list"] = item_list
    if msg.context_token:
        result["msg"]["context_token"] = msg.context_token

    return result
