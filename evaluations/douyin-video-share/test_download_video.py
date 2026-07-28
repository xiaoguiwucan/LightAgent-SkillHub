import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "douyin-video-share" / "scripts" / "download_video.py"
SPEC = importlib.util.spec_from_file_location("douyin_video_share", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class DouyinVideoShareTest(unittest.TestCase):
    def test_extracts_only_allowed_douyin_url(self):
        text = (
            "3.05 rEH:/ J@v.Fu 04/17 :6pm 37度高温在家开16度的空调吃铜锅涮肉是什么体验 "
            "# 铜锅涮肉 # 自制羊肉卷 # 贵州珍酒 # 茅台姊妹酒  "
            "[https://v.douyin.com/f3hjTQpvACU/](https://v.douyin.com/f3hjTQpvACU/) "
            "复制此链接，打开Dou音搜索，直接观看视频！"
        )
        self.assertEqual("https://v.douyin.com/f3hjTQpvACU/", MODULE.extract_share_url(text))
        with self.assertRaises(MODULE.DownloadError):
            MODULE.extract_share_url("https://example.com/video.mp4")
        self.assertFalse(MODULE._is_allowed_url("http://v.douyin.com/a"))
        self.assertFalse(MODULE._is_allowed_url("https://v.douyin.com.evil.example/a"))
        self.assertFalse(MODULE._is_allowed_url("https://127.0.0.1/video"))

    def test_parses_router_data(self):
        payload = {
            "loaderData": {
                "page": {
                    "videoInfoRes": {
                        "item_list": [
                            {
                                "aweme_id": "7665953748267482341",
                                "desc": "测试视频",
                                "author": {"nickname": "作者"},
                                "video": {
                                    "duration": 1234,
                                    "play_addr": {
                                        "url_list": [
                                            "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=test"
                                        ]
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }
        html = f"<html><script>window._ROUTER_DATA = {json.dumps(payload)}</script></html>"
        result = MODULE.parse_video_page(html)
        self.assertEqual("7665953748267482341", result["aweme_id"])
        self.assertEqual("作者", result["author"])

    def test_rejects_gallery(self):
        payload = {
            "videoInfoRes": {
                "item_list": [
                    {
                        "aweme_id": "7665953748267482341",
                        "images": [{"url_list": ["https://p.example/image.jpg"]}],
                        "video": {"play_addr": {"url_list": ["https://aweme.snssdk.com/a"]}},
                    }
                ]
            }
        }
        html = f"<script>window._ROUTER_DATA = {json.dumps(payload)}</script>"
        with self.assertRaises(MODULE.DownloadError) as caught:
            MODULE.parse_video_page(html)
        self.assertEqual("unsupported_item_type", caught.exception.code)

    def test_recognizes_small_mp4_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            self.assertTrue(MODULE._looks_like_mp4(path))

    def test_send_bitrate_stays_within_target(self):
        bitrate = MODULE._target_video_bitrate_kbps(271603)
        estimated_bytes = (bitrate + 72) * 1000 * (271603 / 1000) / 8
        self.assertLessEqual(estimated_bytes, MODULE.TARGET_SEND_BYTES)
        self.assertGreaterEqual(bitrate, 180)


if __name__ == "__main__":
    unittest.main()
