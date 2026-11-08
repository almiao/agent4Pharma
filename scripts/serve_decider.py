"""独立 GPU 环境运行官方 Decider；只监听回环地址，主项目不依赖 torch。"""

import argparse
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def main() -> None:
    """加载已校验的本地权重与官方协议，响应有界判别请求。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=Path("data/models/decider-flash-v1.2"))
    parser.add_argument("--port", type=int, default=18895)
    args = parser.parse_args()
    # 启动前验证端口，避免加载完数GB权重才发现系统拒绝监听。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", args.port))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from pplx_decider import Decider

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; refusing an unrequested CPU/cloud fallback")
    installation = json.loads((args.checkpoint / "installation.json").read_text(encoding="utf-8"))
    model = Decider.from_pretrained(
        args.checkpoint.resolve(), device="cuda", fast=False, max_length=8192, batch_tokens=4096
    )
    identity = {
        "model": installation["model"],
        "capabilities": ["decide", "rank_logits"],
        "revision": installation["revision"],
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "attention": "noncausal_full_attention",
        "temperature": model.temperature,
    }

    class Handler(BaseHTTPRequestHandler):
        def allowed(self):
            hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {"http://" + host for host in hosts}

        def respond(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                self.respond(403, {"error": "local_origin_required"})
                return
            self.respond(200, identity) if self.path == "/health" else self.respond(
                404, {"error": "not_found"}
            )

        def do_POST(self):
            if not self.allowed():
                self.respond(403, {"error": "local_origin_required"})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.respond(415, {"error": "json_required"})
                return
            if self.path not in {"/decide", "/rank"}:
                self.respond(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 2_000_000:
                self.respond(413, {"error": "request_size"})
                return
            try:
                request = json.loads(self.rfile.read(length))
                requests = request["requests"]
                if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
                    raise ValueError("request_count")
                if any(item.get("images") for item in requests):
                    raise ValueError("text_only_service")
                if self.path == "/rank":
                    if any(
                        set(item["questions"]) != {"priority"}
                        or item["questions"]["priority"]["type"] != "noul"
                        for item in requests
                    ):
                        raise ValueError("ranking_question")
                    logits = model.logits(requests)
                    margins = [row["priority"][1] - row["priority"][0] for row in logits]
                    self.respond(200, {"identity": identity, "margins": margins})
                else:
                    responses = model.decide(requests)
                    self.respond(200, {"identity": identity, "responses": responses})
            except Exception as exc:
                self.respond(422, {"error": type(exc).__name__})

        def log_message(self, format, *args):
            pass

    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(json.dumps({"ready": identity, "port": args.port}), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
