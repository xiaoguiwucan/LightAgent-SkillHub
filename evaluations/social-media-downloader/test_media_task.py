import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "social-media-downloader" / "scripts" / "media_task.py"
SPEC = importlib.util.spec_from_file_location("social_media_task", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

LOGIN_SCRIPT = ROOT / "skills" / "social-media-downloader" / "scripts" / "telegram_login.py"
LOGIN_SPEC = importlib.util.spec_from_file_location("social_media_telegram_login", LOGIN_SCRIPT)
LOGIN = importlib.util.module_from_spec(LOGIN_SPEC)
assert LOGIN_SPEC.loader
LOGIN_SPEC.loader.exec_module(LOGIN)


class MediaTaskTest(unittest.TestCase):
    def test_extracts_each_supported_platform_from_share_text(self):
        cases = {
            "douyin": "文案 [https://v.douyin.com/f3hjTQpvACU/](https://v.douyin.com/f3hjTQpvACU/)",
            "tiktok": "看看 https://vm.tiktok.com/ZMexample/ 复制链接",
            "youtube": "https://www.youtube.com/shorts/abcdefghijk",
            "telegram": "媒体 https://t.me/example/123",
        }
        for expected, text in cases.items():
            with self.subTest(expected=expected):
                url, platform_name = MODULE.extract_source(text)
                self.assertEqual(expected, platform_name)
                self.assertTrue(url.startswith("https://"))

    def test_rejects_http_credentials_ports_and_lookalike_hosts(self):
        values = [
            "http://youtu.be/test",
            "https://user:pass@t.me/example/1",
            "https://v.douyin.com:8443/test",
            "https://www.youtube.com.evil.example/watch?v=x",
            "https://127.0.0.1/video",
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(MODULE.TaskError):
                MODULE.extract_source(value)

    def test_parses_douyin_video_and_gallery_router_data(self):
        video_item = {
            "aweme_id": "7665953748267482341",
            "desc": "视频",
            "video": {"play_addr": {"uri": "video-id", "url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=video-id&ratio=720p"]}},
        }
        gallery_item = {
            "aweme_id": "7665953748267482342",
            "desc": "图集",
            "images": [
                {"url_list": ["https://p3-sign.douyinpic.com/example-one.jpeg"]},
                {"url_list": ["https://p9-sign.douyinpic.com/example-two.webp"]},
            ],
        }
        for item, kind, count in ((video_item, "video", 1), (gallery_item, "image", 2)):
            payload = {"loaderData": {"page": {"videoInfoRes": {"item_list": [item]}}}}
            html = f"<script>window._ROUTER_DATA = {json.dumps(payload)}</script>"
            parsed = MODULE.parse_douyin_page(html)
            self.assertEqual(count, len(parsed["resources"]))
            self.assertTrue(all(resource["kind"] == kind for resource in parsed["resources"]))
        self.assertIn("ratio=1080p", MODULE.parse_douyin_page(
            f"<script>window._ROUTER_DATA = {json.dumps({'videoInfoRes': {'item_list': [video_item]}})}</script>"
        )["resources"][0]["url"])
        self.assertIn("ratio=720p", MODULE.parse_douyin_page(
            f"<script>window._ROUTER_DATA = {json.dumps({'videoInfoRes': {'item_list': [video_item]}})}</script>"
        )["resources"][0]["fallback_url"])

    def test_task_is_atomically_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            task = MODULE._new_task("youtube", "https://youtu.be/abcdefghijk", "张三", 1)
            MODULE.save_task(root, task)
            loaded = MODULE.load_task(root, task["task_id"])
            self.assertEqual(task["task_id"], loaded["task_id"])
            self.assertEqual("张三", loaded["requester_label"])
            self.assertFalse(list((root / "tasks").glob("*.tmp")))

    def test_disk_reservation_includes_remaining_source_segments_and_margin(self):
        gib = 1024 * 1024 * 1024
        with patch.object(MODULE.shutil, "disk_usage") as disk_usage:
            disk_usage.return_value.free = int(2.1 * gib)
            self.assertTrue(MODULE._enough_space(Path("/tmp"), gib, 0))
            disk_usage.return_value.free = int(2.09 * gib)
            self.assertFalse(MODULE._enough_space(Path("/tmp"), gib, 0))

    def test_default_concurrency_has_exactly_three_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            with MODULE.download_slot(root), MODULE.download_slot(root), MODULE.download_slot(root), self.assertRaises(MODULE.TaskError) as caught, MODULE.download_slot(root):
                pass
            self.assertEqual("concurrency_limit", caught.exception.code)

    def test_prepare_delivery_without_verified_limit_keeps_download_but_disables_send(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            task = MODULE._new_task("youtube", "https://youtu.be/abcdefghijk", "李四", 1)
            media_dir = root / "media" / "youtube" / task["media_id"]
            media_dir.mkdir(parents=True)
            original = media_dir / "original.mp4"
            original.write_bytes(b"original bytes")
            MODULE.save_task(root, task)
            MODULE._prepare_delivery(root, task, media_dir)
            self.assertEqual("ready", task["status"])
            self.assertEqual([], task["delivery_parts"])
            self.assertIn("尚未完成", task["last_error"])
            self.assertTrue(original.exists())

    def test_transport_profile_rejects_value_above_tested_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            MODULE._atomic_json(root / "transport-profile.json", {"verified_max_send_bytes": 1000 * 1024 * 1024 + 1})
            profile = MODULE._profile(root)
            self.assertEqual(0, profile["verified_max_send_bytes"])
            self.assertIn("1000 MiB", profile["profile_error"])

    def test_delivery_cursor_and_retry_are_isolated_in_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            MODULE._atomic_json(root / "transport-profile.json", {"verified_max_send_bytes": 1024})
            task = MODULE._new_task("youtube", "https://youtu.be/abcdefghijk", "王五", 1)
            media_dir = root / "media" / "youtube" / task["media_id"]
            media_dir.mkdir(parents=True)
            first = media_dir / "王五_youtube_x_第001-002段.mp4"
            second = media_dir / "王五_youtube_x_第002-002段.mp4"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            task.update({"status": "ready", "delivery_parts": [str(first), str(second)], "total_parts": 2})
            MODULE.save_task(root, task)
            delivered = MODULE.next_delivery([task["task_id"], "--data-root", directory])
            retried = MODULE.retry_delivery([task["task_id"], "--data-root", directory])
            self.assertEqual(1, delivered["part_number"])
            self.assertEqual(str(first), retried["file"])
            self.assertIn("王五", delivered["message"])
            self.assertEqual(2, MODULE.load_task(root, task["task_id"])["next_part"])

    def test_collection_limit_is_one_to_twenty(self):
        with patch.object(MODULE, "_download_task", side_effect=lambda root, task: task), tempfile.TemporaryDirectory() as directory, self.assertRaises(MODULE.TaskError) as caught:
            MODULE.prepare_media(["https://youtu.be/abcdefghijk", "用户", "21", "--data-root", directory])
        self.assertEqual("invalid_item_count", caught.exception.code)

    def test_default_item_count_is_five_for_youtube_and_one_elsewhere(self):
        captured = []

        def capture(_root, task):
            captured.append(dict(task))
            return task

        with patch.object(MODULE, "_download_task", side_effect=capture):
            with tempfile.TemporaryDirectory() as directory:
                MODULE.prepare_media(["https://youtu.be/abcdefghijk", "用户", "--data-root", directory])
            with tempfile.TemporaryDirectory() as directory:
                MODULE.prepare_media(["https://t.me/example/100", "用户", "--data-root", directory])
        self.assertEqual(5, captured[0]["requested_items"])
        self.assertEqual(1, captured[1]["requested_items"])
        self.assertFalse(captured[0]["explicit_count"])
        self.assertFalse(captured[1]["explicit_count"])

    def test_yt_options_preserve_resume_and_cap_playlist(self):
        task = MODULE._new_task("youtube", "https://www.youtube.com/playlist?list=test", "用户", 20)
        options = MODULE._yt_options(task, Path("/tmp/media"), lambda value: None)
        self.assertTrue(options["continuedl"])
        self.assertEqual(20, options["playlistend"])
        self.assertEqual(["-threads", "1"], options["postprocessor_args"]["ffmpeg"])
        self.assertNotIn("recode_video", options)

    def test_tiktok_complete_video_url_skips_short_link_resolution(self):
        source = "https://www.tiktok.com/@jul.spamz.fr/video/7608248907960814879?is_from_webapp=1"
        with patch.object(MODULE, "_resolve_platform_url", side_effect=AssertionError("完整作品链接不应预解析")) as resolver:
            self.assertEqual(source, MODULE._resolve_tiktok_url(source))
        resolver.assert_not_called()

    def test_tiktok_short_url_uses_guarded_resolution(self):
        source = "https://vm.tiktok.com/ZMexample/"
        destination = "https://www.tiktok.com/@example/video/7608248907960814879"
        with patch.object(MODULE, "_resolve_platform_url", return_value=destination) as resolver:
            self.assertEqual(destination, MODULE._resolve_tiktok_url(source))
        resolver.assert_called_once_with(source, "tiktok")

    def test_telegram_command_uses_persistent_storage_group_and_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            binary = root / "tools" / "tdl"
            binary.write_bytes(b"#!/bin/sh\n")
            binary.chmod(0o700)
            MODULE._atomic_json(root / "tools" / "tdl-manifest.json", {"version": "v0.20.3", "binary_sha256": MODULE.hashlib.sha256(binary.read_bytes()).hexdigest()})
            task = MODULE._new_task("telegram", "https://t.me/example/123", "用户", 1)
            command = MODULE._telegram_command(root, task, root / "media" / "telegram" / task["media_id"])
            self.assertIn("--group", command)
            self.assertIn("--continue", command)
            self.assertTrue(any(value.startswith("type=bolt,path=") for value in command))

    def test_telegram_message_range_is_capped_and_expanded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            binary = root / "tools" / "tdl"
            binary.write_bytes(b"#!/bin/sh\n")
            binary.chmod(0o700)
            MODULE._atomic_json(root / "tools" / "tdl-manifest.json", {"version": "v0.20.3", "binary_sha256": MODULE.hashlib.sha256(binary.read_bytes()).hexdigest()})
            task = MODULE._new_task("telegram", "https://t.me/example/100", "用户", 3)
            task["explicit_count"] = True
            command = MODULE._telegram_command(root, task, root / "media" / "telegram" / task["media_id"])
            urls = [command[index + 1] for index, value in enumerate(command) if value == "-u"]
            self.assertEqual(["https://t.me/example/100", "https://t.me/example/101", "https://t.me/example/102"], urls)

    def test_telegram_default_does_not_expand_message_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = MODULE.data_root(directory)
            binary = root / "tools" / "tdl"
            binary.write_bytes(b"#!/bin/sh\n")
            binary.chmod(0o700)
            MODULE._atomic_json(root / "tools" / "tdl-manifest.json", {"version": "v0.20.3", "binary_sha256": MODULE.hashlib.sha256(binary.read_bytes()).hexdigest()})
            task = MODULE._new_task("telegram", "https://t.me/example/100", "用户", 5)
            command = MODULE._telegram_command(root, task, root / "media" / "telegram" / task["media_id"])
            urls = [command[index + 1] for index, value in enumerate(command) if value == "-u"]
            self.assertEqual(["https://t.me/example/100"], urls)

    def test_tdl_status_validation_does_not_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = LOGIN._root(directory)
            with patch.object(LOGIN, "install") as install:
                self.assertIsNone(LOGIN.binary_valid(root))
            install.assert_not_called()

    def test_tdl_asset_map_has_fixed_hash_for_primary_platforms(self):
        expected = {("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "x86_64"), ("darwin", "arm64"), ("windows", "amd64"), ("windows", "arm64")}
        self.assertEqual(expected, set(LOGIN.ASSETS))
        for filename, digest in LOGIN.ASSETS.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertTrue(filename.endswith((".tar.gz", ".zip")))

    def test_tdl_safe_extract_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                item = tarfile.TarInfo("../escape")
                payload = b"bad"
                item.size = len(payload)
                handle.addfile(item, io.BytesIO(payload))
            destination = Path(directory) / "output"
            destination.mkdir()
            with self.assertRaises(SystemExit):
                LOGIN._safe_extract(archive, destination)
            self.assertFalse((Path(directory) / "escape").exists())


if __name__ == "__main__":
    unittest.main()
