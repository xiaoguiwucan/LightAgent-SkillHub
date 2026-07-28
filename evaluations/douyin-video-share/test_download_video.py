import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "douyin-video-share" / "scripts" / "download_video.py"
SPEC = importlib.util.spec_from_file_location("douyin_video_share", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class DouyinVideoShareTest(unittest.TestCase):
    def test_extracts_only_allowed_douyin_url(self):
        messages = {
            "https://v.douyin.com/f3hjTQpvACU/": (
                "3.05 rEH:/ J@v.Fu 04/17 :6pm 37度高温在家开16度的空调吃铜锅涮肉是什么体验 "
                "# 铜锅涮肉 # 自制羊肉卷 # 贵州珍酒 # 茅台姊妹酒  "
                "[https://v.douyin.com/f3hjTQpvACU/](https://v.douyin.com/f3hjTQpvACU/) "
                "复制此链接，打开Dou音搜索，直接观看视频！"
            ),
            "https://v.douyin.com/BslXMEVrobc/": (
                "7.69 复制打开抖音，看看【过瘾剧场的作品】我真不是大佬啊，一口气看过瘾，"
                "我真不是大佬啊剑神学... https://v.douyin.com/BslXMEVrobc/ 03/04 m@Q.XM FHI:/ :4pm"
            ),
            "https://v.douyin.com/NbzbK9JOh18/": (
                "8.25 复制打开抖音，看看【小飞兔电竞(DOTA2大神萌妹一起开黑)的作品】"
                "dota2里的变身术 大变活人！ # dota2 https://v.douyin.com/NbzbK9JOh18/ 01/04 :7pm c@A.tR HVy:/"
            ),
            "https://v.douyin.com/hyrnk3IG0Mo/": (
                "2.51 快看 你看到了什么# 含情脉脉的眼神 # 眼神杀# 御姐# 颜值 "
                "https://v.douyin.com/hyrnk3IG0Mo/ 复制此链接，打开抖音搜索，直接观看视频！ 05/13 qRx:/ :5pm E@h.Ox"
            ),
        }
        for expected, text in messages.items():
            with self.subTest(expected=expected):
                self.assertEqual(expected, MODULE.extract_share_url(text))
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
                                    "width": 1080,
                                    "height": 1920,
                                    "play_addr": {
                                        "uri": "v0200fg10000test",
                                        "url_list": [
                                            "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=test&ratio=720p"
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
        self.assertEqual(1080, result["source_width"])
        self.assertEqual("v0200fg10000test", result["video_id"])

    def test_builds_1080p_no_watermark_candidate_before_page_fallback(self):
        info = {
            "video_id": "v0200fg10000test",
            "media_url": "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=old&ratio=720p&line=0",
        }
        candidates = MODULE.build_media_candidates(info)
        self.assertEqual("1080p-request", candidates[0][1])
        self.assertIn("/aweme/v1/play/", candidates[0][0])
        self.assertIn("video_id=v0200fg10000test", candidates[0][0])
        self.assertIn("ratio=1080p", candidates[0][0])
        self.assertEqual((info["media_url"], "page-fallback"), candidates[1])

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

    def test_parses_ffprobe_video_specifications(self):
        output = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "avg_frame_rate": "30000/1001",
                    "bit_rate": "4200000",
                },
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000"},
            ],
            "format": {"duration": "7.062", "bit_rate": "4350000"},
        }
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(output), "stderr": ""})()
        with patch.object(MODULE.shutil, "which", return_value="/usr/bin/ffprobe"), patch.object(
            MODULE.subprocess, "run", return_value=completed
        ):
            spec = MODULE.probe_video(Path("video.mp4"))
        self.assertEqual((1080, 1920), (spec["width"], spec["height"]))
        self.assertEqual(29.97, spec["fps"])
        self.assertEqual("h264", spec["video_codec"])
        self.assertEqual(7062, spec["duration_ms"])

    def test_low_resolution_cache_is_replaced_by_1080p_download(self):
        info = {
            "aweme_id": "7665953748267482341",
            "source_width": 1080,
            "source_height": 1920,
            "video_id": "video-id",
            "media_url": "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=video-id&ratio=720p",
        }
        low_spec = {"width": 720, "height": 1280}
        high_spec = {"width": 1080, "height": 1920}
        with tempfile.TemporaryDirectory() as directory:
            cached = Path(directory) / "videos" / "douyin-video-share" / f"{info['aweme_id']}.mp4"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"old")

            def fake_download(url, destination, referer):
                destination.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"new")
                return destination.stat().st_size

            with patch.object(MODULE, "probe_video", side_effect=[low_spec, high_spec]), patch.object(
                MODULE, "_download_once", side_effect=fake_download
            ) as download:
                path, _, quality, spec = MODULE.download_video(info, Path(directory), "https://v.douyin.com/test/")
        self.assertEqual(cached.resolve(), path)
        self.assertEqual(high_spec, spec)
        self.assertEqual("1080p-request-1080p", quality)
        self.assertEqual(1, download.call_count)

    def test_falls_back_to_page_video_when_1080p_request_fails(self):
        info = {
            "aweme_id": "7665953748267482341",
            "source_width": 1080,
            "source_height": 1920,
            "video_id": "video-id",
            "media_url": "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=video-id&ratio=720p",
        }
        fallback_spec = {"width": 720, "height": 1280}

        def fake_download(url, destination, referer):
            if "/play/" in url:
                raise MODULE.DownloadError("download_failed", "1080p unavailable")
            destination.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"fallback")
            return destination.stat().st_size

        with tempfile.TemporaryDirectory() as directory, patch.object(
            MODULE, "_download_once", side_effect=fake_download
        ) as download, patch.object(MODULE, "probe_video", return_value=fallback_spec):
            _, _, quality, spec = MODULE.download_video(info, Path(directory), "https://v.douyin.com/test/")
        self.assertEqual(2, download.call_count)
        self.assertEqual("page-fallback-720p", quality)
        self.assertEqual(fallback_spec, spec)

    def test_large_original_is_not_transcoded_and_reports_compatibility_warning(self):
        info = {
            "aweme_id": "7665953748267482341",
            "title": "测试",
            "author": "作者",
            "duration_ms": 7000,
            "source_width": 1080,
            "source_height": 1920,
        }
        spec = {
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "video_bitrate_bps": 4000000,
            "audio_bitrate_bps": 128000,
            "duration_ms": 7062,
        }
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "original.mp4"
            video.write_bytes(b"original")
            output = io.StringIO()
            with patch.object(MODULE, "fetch_video_info", return_value=info), patch.object(
                MODULE,
                "download_video",
                return_value=(video, MODULE.MAX_SEND_BYTES + 1, "1080p-request-1080p", spec),
            ), patch.object(
                MODULE, "extract_share_url", return_value="https://v.douyin.com/test/"
            ), patch("sys.argv", ["download_video.py", "share", "--output-root", directory]), redirect_stdout(output):
                exit_code = MODULE.main()
        result = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertFalse(result["transcoded_for_send"])
        self.assertTrue(result["quality_preserved"])
        self.assertTrue(result["large_file_compatibility_warning"])
        self.assertEqual(str(video), result["video_file"])


if __name__ == "__main__":
    unittest.main()
