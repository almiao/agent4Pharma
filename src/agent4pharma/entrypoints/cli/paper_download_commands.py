"""按 DOI 获取全文的命令行适配器；只做参数转发，具体连接由组合层装配。"""

import json
from argparse import Namespace
from pathlib import Path

from agent4pharma.composition import chaoslib_routes, download_papers_with_chaoslib, login_chaoslib


def add_paper_download_arguments(commands) -> None:
    """注册全文获取命令组；不读取配置、不建立连接。"""
    group = commands.add_parser("paper-download", help="按 DOI 从订阅代理获取全文")
    actions = group.add_subparsers(dest="paper_download_command", required=True)
    chaos = actions.add_parser("chaoslib", help="混沌书苑账号（需要 PAPER_D_ACCOUNT/PAPER_D_PASSWORD）")
    chaos.add_argument("--doi", action="append", required=True, help="可重复，按给定顺序串行尝试")
    chaos.add_argument("--out", type=Path, default=Path("data/paper-downloads/chaoslib"))
    chaos.add_argument("--json", action="store_true", help="每行输出一个 JSON 结果")
    login = actions.add_parser("login", help="登录 Chaoslib 并复用会话；认证未通过则交人工")
    login.add_argument("--human", action="store_true", help="显示浏览器，最多等待三分钟人工完成验证")
    actions.add_parser("routes", help="列出已验证入口与已知阻断的出版商")
    imported = actions.add_parser("import", help="人工取得主文后导入已冻结全文清单")
    imported.add_argument("--run", required=True)
    imported.add_argument("--record", required=True)
    imported.add_argument("--file", type=Path, required=True)
    restored = actions.add_parser("account-restored", help="人工确认账号已恢复后解除本地暂停；不会联网或下载")
    restored.add_argument("--confirm", action="store_true", required=True)
    actions.add_parser("browser-setup", help="安装锁定版本的官方 Playwright MCP 运行库，不安装扩展或连接网站")
    actions.add_parser("browser-status", help="扩展授权后检查当前 Chaoslib 页，不登录、不下载")
    bound = actions.add_parser("browser-config", help="为绑定论文生成 DSH 获取角色的受限配置，不启动模型")
    bound.add_argument("--run", required=True)
    bound.add_argument("--record", required=True)


def paper_download_command(args: Namespace, project_root: Path) -> int:
    """执行一次或多次下载，返回进程退出码；失败原因逐条输出，不折叠成成功。"""
    if args.paper_download_command == "browser-setup":
        from agent4pharma.composition import install_daily_browser_runtime

        print(json.dumps(install_daily_browser_runtime(project_root / "data"), ensure_ascii=False))
        return 0
    if args.paper_download_command == "browser-status":
        from agent4pharma.composition import inspect_daily_browser

        result = inspect_daily_browser(project_root / "data")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ready" else 1
    if args.paper_download_command == "browser-config":
        from agent4pharma.composition import create_browser_paper_patch

        print(create_browser_paper_patch(project_root / "config/discovery.toml", args.run, args.record))
        return 0
    if args.paper_download_command == "account-restored":
        from agent4pharma.composition import confirm_chaoslib_restored

        confirm_chaoslib_restored(project_root)
        print('{"status":"local_pause_cleared","network_requested":false}')
        return 0
    if args.paper_download_command == "login":
        print(json.dumps(login_chaoslib(project_root, human=args.human), ensure_ascii=False))
        return 0
    if args.paper_download_command == "import":
        from agent4pharma.composition import import_selected_paper

        result = import_selected_paper(project_root / "data", args.run, args.record, args.file)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "downloaded" else 1
    if args.paper_download_command == "routes":
        for row in chaoslib_routes():
            reason = row.get("reason", "")
            suffix = f"\t{reason}" if reason else ""
            print(f"{row['prefix']}\t{row['status']}\t{row['publisher']}{suffix}")
        return 0

    results = download_papers_with_chaoslib(project_root, list(args.doi), args.out)
    for item in results:
        if args.json:
            print(json.dumps(item, ensure_ascii=False))
        else:
            print(f"{item['status']}\t{item['doi']}\t{item.get('reason') or item.get('file', '')}")
    return 0 if all(item.get("status") == "downloaded" for item in results) else 1
