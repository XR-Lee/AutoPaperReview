from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from autopaperreview.mcp_server import PROTOCOL_VERSIONS, TOOLS, Server

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "synthetic"
SOURCE = Path(__file__).resolve().parents[1] / "src"


class Client:
    def __init__(self, root: Path) -> None:
        self.sent: list[dict] = []
        self.server = Server([root], self.sent.append)
        self.next_id = 0
        self.request("initialize", {"protocolVersion": PROTOCOL_VERSIONS[0], "capabilities": {}})

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self.server.handle(message)
        assert response is not None
        return response

    def call(self, name: str, arguments: dict, **meta) -> dict:
        params: dict = {"name": name, "arguments": arguments}
        if meta:
            params["_meta"] = meta
        return self.request("tools/call", params)["result"]


class ProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_a_supported_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = Server([Path(temporary)])
            old = server.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
            )
            self.assertEqual(old["result"]["protocolVersion"], "2024-11-05")
            self.assertIn("tools", old["result"]["capabilities"])
            unknown = server.handle(
                {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
            )
            self.assertEqual(unknown["result"]["protocolVersion"], PROTOCOL_VERSIONS[0])

    def test_tools_list_notifications_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = Client(Path(temporary))
            tools = client.request("tools/list")["result"]["tools"]
            self.assertEqual([tool["name"] for tool in tools], [tool.name for tool in TOOLS])
            for tool in tools:
                self.assertEqual(tool["inputSchema"]["type"], "object")
                self.assertIn("readOnlyHint", tool["annotations"])
            self.assertIsNone(client.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            self.assertEqual(client.request("ping")["result"], {})
            self.assertEqual(client.request("resources/read")["error"]["code"], -32601)
            self.assertEqual(client.request("tools/call", {"name": "missing"})["error"]["code"], -32602)
            bad = client.call("status", {"config": "review.toml", "extra": 1})
            self.assertTrue(bad["isError"])
            self.assertIn("unknown arguments", bad["structuredContent"]["error"])

    def test_paths_outside_the_roots_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            client = Client(root)
            result = client.call("status", {"config": str(Path(temporary) / "review.toml")})
            self.assertTrue(result["isError"])
            self.assertIn("outside the server roots", result["structuredContent"]["error"])
            result = client.call("init_project", {"path": "../escape"})
            self.assertTrue(result["isError"])
            self.assertFalse((Path(temporary) / "escape").exists())

    def test_a_config_whose_run_directory_leaves_the_roots_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            (root / "manuscript.txt").write_text("Synthetic.\n", encoding="utf-8")
            (root / "review.toml").write_text(
                textwrap.dedent(
                    f"""
                    schema_version = 1

                    [project]
                    id = "outside"
                    title = "Outside"
                    source = "manuscript.txt"
                    run_root = {json.dumps(str(Path(temporary) / "runs"))}

                    [[stages]]
                    id = "ingest"
                    type = "ingest"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            result = Client(root).call("run", {"config": "review.toml"})
            self.assertTrue(result["isError"])
            self.assertFalse((Path(temporary) / "runs").exists())


class ReviewLoopTests(unittest.TestCase):
    def test_the_agent_review_loop_through_the_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "paper.txt").write_text(
                (EXAMPLES / "calibration" / "manuscript.txt").read_text(encoding="utf-8"), encoding="utf-8"
            )
            client = Client(root)
            created = client.call("init_project", {"path": "paper", "manuscript_path": "paper.txt"})
            self.assertFalse(created["isError"])
            config = "paper/review.toml"
            self.assertEqual(client.call("validate", {"config": config})["structuredContent"]["status"], "pass")

            client.sent.clear()
            first = client.call("run", {"config": config}, progressToken="run-1")
            self.assertFalse(first["isError"])
            summary = first["structuredContent"]
            self.assertEqual(summary["status"], "awaiting_input")
            progress = [message["params"] for message in client.sent if message["method"] == "notifications/progress"]
            self.assertTrue(progress)
            self.assertTrue(all(item["progressToken"] == "run-1" for item in progress))
            values = [item["progress"] for item in progress]
            self.assertEqual(values, sorted(values))
            self.assertEqual(values[-1], progress[-1]["total"])

            packet = summary["awaiting"][0]["reads"][0]
            page = client.call("read_input", {"config": config, "path": packet["path"], "max_chars": 1000})
            self.assertEqual(page["structuredContent"]["next_offset"], 1000)
            self.assertEqual(len(page["content"][0]["text"]), 1000)
            for bad in ({"offset": -100}, {"max_chars": 5}, {"max_chars": 10**9}):
                refused = client.call("read_input", {"config": config, "path": packet["path"], **bad})
                self.assertTrue(refused["isError"], bad)
            rest = client.call("read_input", {"config": config, "path": packet["path"], "offset": 1000})
            self.assertIsNone(rest["structuredContent"]["next_offset"])
            outside = client.call("read_input", {"config": config, "path": str(root / "paper.txt")})
            self.assertTrue(outside["isError"])
            self.assertTrue(client.call("report", {"config": config})["isError"])

            template = client.call("drop_template", {"config": config})["structuredContent"]
            self.assertEqual(template["kind"], "review_judgment")
            self.assertEqual(len(template["help"]["clauses"]), 7)
            drop = json.loads((EXAMPLES / "calibration" / "judgment.json").read_text(encoding="utf-8"))
            drop["packet_sha256"] = template["drop"]["packet_sha256"]
            invented = json.loads(json.dumps(drop))
            invented["evidence"][0]["excerpt"] = "This line is not in the manuscript."
            rejected = client.call("submit_drop", {"config": config, "drop": invented})
            self.assertTrue(rejected["isError"])
            self.assertEqual(rejected["structuredContent"]["status"], "rejected")
            accepted = client.call("submit_drop", {"config": config, "drop": drop})
            self.assertFalse(accepted["isError"])

            second = client.call("run", {"config": config})["structuredContent"]
            self.assertEqual(second["status"], "success")
            report = client.call("report", {"config": config})
            self.assertFalse(report["isError"])
            self.assertTrue(report["content"][0]["text"].startswith("# Review Report"))
            self.assertTrue(any(item["path"].endswith("review_package.json") for item in report["structuredContent"]["outputs"]))


class StdioTests(unittest.TestCase):
    def test_stdout_carries_only_protocol_messages(self) -> None:
        script = textwrap.dedent(
            """
            import os
            import sys
            from pathlib import Path

            from autopaperreview import mcp_server

            original = mcp_server.serve

            def noisy(server, stream, send, **kwargs):
                print("stray print")
                os.system(sys.executable + " -c \\"print('stray child')\\"")
                return original(server, stream, send, **kwargs)

            mcp_server.serve = noisy
            sys.exit(mcp_server.serve_stdio([Path(sys.argv[1])]))
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SOURCE), os.environ.get("PYTHONPATH", "")])}
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "status", "arguments": {"config": "missing/review.toml"}},
                },
            ]
            completed = subprocess.run(
                [sys.executable, "-c", script, temporary],
                input="".join(json.dumps(message) + "\n" for message in messages) + "not json\n",
                capture_output=True,
                text=True,
                env=environment,
                timeout=120,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            by_id = {response.get("id"): response for response in responses}
            self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "autopaperreview")
            self.assertEqual(len(by_id[2]["result"]["tools"]), len(TOOLS))
            self.assertTrue(by_id[3]["result"]["isError"])
            self.assertEqual(by_id[None]["error"]["code"], -32700)
            self.assertIn("stray print", completed.stderr)
            self.assertIn("stray child", completed.stderr)


if __name__ == "__main__":
    unittest.main()
