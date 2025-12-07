
import sys
from unittest.mock import MagicMock

# Mock tortoise and db before importing handlers
sys.modules["tortoise"] = MagicMock()
sys.modules["tortoise.fields"] = MagicMock()
sys.modules["tortoise.expressions"] = MagicMock()
sys.modules["tortoise.functions"] = MagicMock()
sys.modules["db"] = MagicMock()
sys.modules["db.db"] = MagicMock()
sys.modules["config"] = MagicMock()
sys.modules["config.config"] = MagicMock()
# Set up a dummy cfg dictionary
sys.modules["config.config"].cfg = {"proxy": None, "eh_cookie": "test_cookie", "telegraph_token": "TEST_TOKEN"}
sys.modules["utils.http_client"] = MagicMock()

# Mock other handlers to avoid transitive import errors
sys.modules["handlers.clientmgr"] = MagicMock()
sys.modules["handlers.inline_query"] = MagicMock()
sys.modules["handlers.resolver"] = MagicMock()
sys.modules["handlers.statistics"] = MagicMock()
sys.modules["handlers.user_action"] = MagicMock()
sys.modules["handlers.usermgr"] = MagicMock()

import pytest
from unittest.mock import AsyncMock, patch
from handlers.telegraph import publish_to_telegraph

# Sample data
SAMPLE_GALLERY_DATA = {
    "title": "Test Gallery Title",
    "title_jpn": "テストギャラリータイトル",
    "category": "Doujinshi",
    "uploader": "TestUploader",
    "posted": "1678886400",  # 2023-03-15 16:00:00 UTC
    "filecount": "42",
    "filesize": 1024 * 1024 * 50,  # 50 MB
    "rating": "4.5",
    "tags": ["female:catgirl", "male:glasses", "original"],
    "thumb": "https://s.exhentai.org/t/123.jpg",
    "error": None
}

SAMPLE_HTML = """
<html>
<body>
    <div class="gdtm" style="height: 201px; width: 143px">
        <div style="margin: 1px auto 0; width: 141px; height: 200px">
            <a href="https://exhentai.org/s/123/456-1">
                <img alt="01" title="Page 1: 01" src="https://ehgt.org/t/123/1.jpg" style="height: 200px; width: 141px" />
            </a>
        </div>
    </div>
    <div class="gdtm" style="height: 201px; width: 143px">
        <div style="margin: 1px auto 0; width: 141px; height: 200px">
            <a href="https://exhentai.org/s/123/456-2">
                <img alt="02" title="Page 2: 02" src="https://ehgt.org/t/123/2.jpg" style="height: 200px; width: 141px" />
            </a>
        </div>
    </div>
</body>
</html>
"""

@pytest.fixture
def mock_tag_map():
    return {
        "female": {
            "name": "女性",
            "data": {
                "catgirl": "猫耳"
            }
        },
        "male": {
            "name": "男性",
            "data": {
                "glasses": "眼镜"
            }
        }
    }

@pytest.mark.asyncio
async def test_publish_to_telegraph_success(mock_tag_map):
    with patch("handlers.telegraph.get_gdata", new_callable=AsyncMock) as mock_get_gdata, \
         patch("handlers.telegraph.get_gallery_html", new_callable=AsyncMock) as mock_get_html, \
         patch("handlers.telegraph.resolve") as mock_resolve, \
         patch("handlers.telegraph.publish_text") as mock_publish:
        
        # Setup mocks
        mock_get_gdata.return_value = SAMPLE_GALLERY_DATA
        mock_get_html.return_value = SAMPLE_HTML
        mock_resolve.tag_map = mock_tag_map
        mock_publish.return_value = "https://telegra.ph/Test-Gallery-03-15"
        
        # Execute
        url, error = await publish_to_telegraph("123", "abc")
        
        # Verify
        assert url == "https://telegra.ph/Test-Gallery-03-15"
        assert error is None
        
        # Check if content generation is correct
        args, _ = mock_publish.call_args
        content = args[0]
        
        # Check basic info
        assert "# Test Gallery Title" in content
        assert "**日文标题**: テストギャラリータイトル" in content
        assert "**类型**: Doujinshi" in content
        assert "**大小**: 50.00 MB" in content
        
        # Verify token was passed
        kwargs = mock_publish.call_args[1]
        assert kwargs.get("token") == "TEST_TOKEN"
        
        # Check tags translation
        assert "**女性**: 猫耳" in content
        assert "**男性**: 眼镜" in content
        assert "**其他**: original" in content
        
        # Check cover image
        assert "<img src='https://ehgt.org/t/123.jpg'/>" in content
        
        # Check previews
        assert "## 预览" in content
        assert "<img src='https://ehgt.org/t/123/1.jpg'/>" in content
        assert "<img src='https://ehgt.org/t/123/2.jpg'/>" in content

@pytest.mark.asyncio
async def test_publish_to_telegraph_missing_params():
    url, error = await publish_to_telegraph("", "")
    assert url is None
    assert "画廊 ID 或 token 为空" in error

@pytest.mark.asyncio
async def test_publish_to_telegraph_api_error():
    with patch("handlers.telegraph.get_gdata", new_callable=AsyncMock) as mock_get_gdata:
        mock_get_gdata.return_value = {"error": "Invalid Key"}
        
        url, error = await publish_to_telegraph("123", "abc")
        
        assert url is None
        assert "画廊错误: Invalid Key" in error

@pytest.mark.asyncio
async def test_publish_to_telegraph_no_previews(mock_tag_map):
    with patch("handlers.telegraph.get_gdata", new_callable=AsyncMock) as mock_get_gdata, \
         patch("handlers.telegraph.get_gallery_html", new_callable=AsyncMock) as mock_get_html, \
         patch("handlers.telegraph.resolve") as mock_resolve, \
         patch("handlers.telegraph.publish_text") as mock_publish:
        
        mock_get_gdata.return_value = SAMPLE_GALLERY_DATA
        # HTML with no images
        mock_get_html.return_value = "<html><body>No images here</body></html>"
        mock_resolve.tag_map = mock_tag_map
        mock_publish.return_value = "https://telegra.ph/Test"
        
        url, error = await publish_to_telegraph("123", "abc")
        
        assert url is not None
        
        # Check content
        args, _ = mock_publish.call_args
        content = args[0]
        
        # Should not have previews section
        assert "## 预览" not in content

@pytest.mark.asyncio
async def test_publish_to_telegraph_tag_map_uninitialized():
    with patch("handlers.telegraph.get_gdata", new_callable=AsyncMock) as mock_get_gdata, \
         patch("handlers.telegraph.get_gallery_html", new_callable=AsyncMock) as mock_get_html, \
         patch("handlers.telegraph.resolve") as mock_resolve, \
         patch("handlers.telegraph.publish_text") as mock_publish:
        
        mock_get_gdata.return_value = SAMPLE_GALLERY_DATA
        mock_get_html.return_value = SAMPLE_HTML
        # Simulate tag_map not existing or being None/empty
        del mock_resolve.tag_map 
        # Or if it raises AttributeError when accessed, handled by try-except block in code
        
        mock_publish.return_value = "https://telegra.ph/Test"
        
        # We need to ensure the code handles missing tag_map gracefully
        # In the code we added:
        # try:
        #     tag_map = resolve.tag_map
        # except AttributeError:
        #     tag_map = {}
        
        # Because we are patching 'handlers.telegraph.resolve', accessing .tag_map on the mock will default to another MagicMock unless we delete it or specify it raises
        # Let's configure the mock to raise AttributeError
        type(mock_resolve).tag_map = property(fget=MagicMock(side_effect=AttributeError))

        url, error = await publish_to_telegraph("123", "abc")
        
        assert url is not None
        
        # Content should have untranslated tags
        args, _ = mock_publish.call_args
        content = args[0]
        
        assert "**female**: catgirl" in content
        assert "**male**: glasses" in content

