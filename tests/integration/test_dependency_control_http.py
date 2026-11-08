"""依赖控制复用本机HTTP同源/令牌入口，不运行真实服务或模型。"""

import threading
from unittest.mock import Mock

import httpx

from agent4pharma.composition import build_maintenance_workbench
from agent4pharma.entrypoints.web.server import WorkbenchHTTPServer
from agent4pharma.runtime.dependency_status import DependencyOperation
from agent4pharma.runtime.settings import ApplicationSettings


def test_service_control_requires_token_and_fixed_request(tmp_path):
    app = build_maintenance_workbench(tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path))
    server = WorkbenchHTTPServer(app, 0)
    operation = DependencyOperation(
        id="accepted", service="mineru", action="start", status="running", detail="starting", started_at="now"
    )
    control = Mock(return_value=operation)
    server.api.dependency_control.control = control
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            route = "/api/dependencies/control"
            assert client.post(route, content=b"").status_code == 403
            client.headers["X-Workbench-Token"] = server.token
            assert (
                client.post(
                    route,
                    headers={"Origin": "https://example.org"},
                    content=b"",
                ).status_code
                == 403
            )
            for payload in (
                {"service": "backend", "action": "start"},
                {"service": "mineru", "action": "restart"},
                {"service": "decider", "action": "start", "command": "arbitrary"},
            ):
                assert client.post(route, json=payload).status_code == 400
            control.assert_not_called()
            response = client.post(route, json={"service": "mineru", "action": "start"})
            assert response.status_code == 200 and response.json()["status"] == "running"
            assert control.call_count == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        app.close()
