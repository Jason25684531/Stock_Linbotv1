from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import requests
from PIL import Image
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessageAction,
    PostbackAction,
    RichMenuArea,
    RichMenuBounds,
    RichMenuRequest,
    RichMenuSize,
)

from config import Config


RICH_MENU_WIDTH = 2500
RICH_MENU_HEIGHT = 1686
RICH_MENU_HALF_WIDTH = 1250
RICH_MENU_HALF_HEIGHT = 843
DEFAULT_RICH_MENU_NAME = "Stocke Rich Menu"
DEFAULT_CHAT_BAR_TEXT = "開啟選單"
DEFAULT_RICH_MENU_IMAGE = Path(__file__).resolve().parents[1] / "Richmenu" / "Richmenu.png"
RICH_MENU_UPLOAD_MAX_BYTES = 1024 * 1024


def build_default_rich_menu_request() -> RichMenuRequest:
    """Build the default 2x2 rich menu definition.

    Layout (2500x1686 px canvas, each cell 1250x843):

    +-----------------+-----------------+
    |  個股診斷       |  總經摘要       |
    |  MessageAction  |  PostbackAction |
    |     診斷        |  macro_summary  |
    +-----------------+-----------------+
    |  日誌反思       |  策略選股       |
    |  PostbackAction |  PostbackAction |
    | journal_reflect | choose_strategy |
    +-----------------+-----------------+
    """
    return RichMenuRequest(
        size=RichMenuSize(width=RICH_MENU_WIDTH, height=RICH_MENU_HEIGHT),
        selected=True,
        name=DEFAULT_RICH_MENU_NAME,
        chat_bar_text=DEFAULT_CHAT_BAR_TEXT,
        areas=[
            # 左上：個股診斷（引導輸入 4 碼股票代號）
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=RICH_MENU_HALF_WIDTH, height=RICH_MENU_HALF_HEIGHT),
                action=MessageAction(
                    label="個股診斷",
                    text="診斷",
                ),
            ),
            # 右上：總經摘要（PostbackAction → macro_summary）
            RichMenuArea(
                bounds=RichMenuBounds(
                    x=RICH_MENU_HALF_WIDTH,
                    y=0,
                    width=RICH_MENU_HALF_WIDTH,
                    height=RICH_MENU_HALF_HEIGHT,
                ),
                action=PostbackAction(
                    label="總經摘要",
                    data="action=macro_summary",
                    display_text="總經摘要",
                ),
            ),
            # 左下：日誌反思（PostbackAction → journal_reflection）
            RichMenuArea(
                bounds=RichMenuBounds(
                    x=0,
                    y=RICH_MENU_HALF_HEIGHT,
                    width=RICH_MENU_HALF_WIDTH,
                    height=RICH_MENU_HALF_HEIGHT,
                ),
                action=PostbackAction(
                    label="日誌反思",
                    data="action=journal_reflection",
                    display_text="日誌反思",
                ),
            ),
            # 右下：策略選股（PostbackAction → choose_strategy）
            RichMenuArea(
                bounds=RichMenuBounds(
                    x=RICH_MENU_HALF_WIDTH,
                    y=RICH_MENU_HALF_HEIGHT,
                    width=RICH_MENU_HALF_WIDTH,
                    height=RICH_MENU_HALF_HEIGHT,
                ),
                action=PostbackAction(
                    label="策略選股",
                    data="action=choose_strategy",
                    display_text="策略選股",
                ),
            ),
        ],
    )


def resolve_rich_menu_image_path(image_path: Optional[str | Path] = None) -> Path:
    """Resolve the rich menu PNG path and validate that it exists."""
    path = Path(image_path) if image_path else DEFAULT_RICH_MENU_IMAGE
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Rich Menu 圖片不存在: {path}")
    return path


def prepare_rich_menu_upload_image(
    image_path: Optional[str | Path] = None,
    max_bytes: int = RICH_MENU_UPLOAD_MAX_BYTES,
) -> tuple[Path, str]:
    """Resize/compress the rich menu image into a LINE-upload-safe asset."""
    source = resolve_rich_menu_image_path(image_path)
    if (
        source.suffix.lower() == ".png"
        and source.stat().st_size <= max_bytes
    ):
        with Image.open(source) as img:
            if img.size == (RICH_MENU_WIDTH, RICH_MENU_HEIGHT):
                return source, "image/png"

    with Image.open(source) as original:
        resized = original.resize((RICH_MENU_WIDTH, RICH_MENU_HEIGHT), Image.LANCZOS)

        fd_png, tmp_png = tempfile.mkstemp(prefix="richmenu_", suffix=".png")
        os.close(fd_png)
        Path(tmp_png).unlink(missing_ok=True)
        png_path = Path(tmp_png)
        resized.save(png_path, format="PNG", optimize=True, compress_level=9)
        if png_path.stat().st_size <= max_bytes:
            return png_path, "image/png"

        flattened = Image.new("RGB", resized.size, (255, 255, 255))
        if resized.mode in ("RGBA", "LA"):
            flattened.paste(resized, mask=resized.getchannel("A"))
        else:
            flattened.paste(resized.convert("RGB"))

        for quality in (90, 85, 80, 75, 70, 65, 60):
            fd_jpg, tmp_jpg = tempfile.mkstemp(prefix="richmenu_", suffix=".jpg")
            os.close(fd_jpg)
            Path(tmp_jpg).unlink(missing_ok=True)
            jpg_path = Path(tmp_jpg)
            flattened.save(
                jpg_path,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
            if jpg_path.stat().st_size <= max_bytes:
                return jpg_path, "image/jpeg"

    raise RuntimeError(
        f"Rich Menu 圖片壓縮後仍超過限制 {max_bytes} bytes: {source}"
    )


def upload_rich_menu_image(
    rich_menu_id: str,
    channel_access_token: str,
    image_path: Optional[str | Path] = None,
    timeout: int = 30,
) -> None:
    """Upload rich menu image via direct HTTP to avoid SDK binary upload bug."""
    upload_file, content_type = prepare_rich_menu_upload_image(image_path)
    with upload_file.open("rb") as fp:
        response = requests.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={
                "Authorization": f"Bearer {channel_access_token}",
                "Content-Type": content_type,
            },
            data=fp.read(),
            timeout=timeout,
        )

    if not response.ok:
        raise RuntimeError(
            f"Rich Menu 圖片上傳失敗: HTTP {response.status_code} {response.text[:300]}"
        )


def sync_default_rich_menu(
    messaging_api: MessagingApi,
    channel_access_token: str,
    image_path: Optional[str | Path] = None,
) -> str:
    """Create, upload, and set the default rich menu."""
    rich_menu = build_default_rich_menu_request()
    rich_menu_id = messaging_api.create_rich_menu(rich_menu).rich_menu_id
    upload_rich_menu_image(
        rich_menu_id=rich_menu_id,
        channel_access_token=channel_access_token,
        image_path=image_path,
    )
    messaging_api.set_default_rich_menu(rich_menu_id)
    return rich_menu_id


def sync_default_rich_menu_from_token(
    channel_access_token: Optional[str] = None,
    image_path: Optional[str | Path] = None,
) -> str:
    """Sync the default rich menu using the configured LINE token."""
    token = channel_access_token or Config.LINE_CHANNEL_ACCESS_TOKEN
    if not token:
        raise ValueError("缺少 LINE channel access token，無法綁定 Rich Menu")

    configuration = Configuration(access_token=token)
    with ApiClient(configuration) as api_client:
        return sync_default_rich_menu(
            messaging_api=MessagingApi(api_client),
            channel_access_token=token,
            image_path=image_path,
        )


if __name__ == "__main__":
    rich_menu_id = sync_default_rich_menu_from_token()
    print(f"Rich Menu 綁定完成: {rich_menu_id}")
