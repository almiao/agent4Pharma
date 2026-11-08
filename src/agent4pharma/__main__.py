"""通过 python -m agent4pharma 启动命令行；业务逻辑由 CLI 入口分派。"""

from agent4pharma.entrypoints.cli.main import main

if __name__ == "__main__":
    main()
