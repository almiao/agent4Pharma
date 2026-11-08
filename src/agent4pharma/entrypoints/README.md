# 协议入口

协议入口适配参数、输出、错误与资源生命周期，业务校验由模块用例和工作流执行。

- `cli/argument_parser.py`定义命令语法，`cli/main.py`装配和分派，具名commands文件处理各业务命令。
- `web/server.py`负责本地HTTP、安全检查和路由分派；`api.py`、`production_api.py`、`paper_download_api.py`等适配业务请求。正式界面源码在`frontend/`，构建产物在`web/static/live-console/`；保留的旧地址转换不渲染旧后台。
- `mcp/production_server.py`按任务角色、阶段及冻结策略注册工具；参数和返回契约见`workflows/production_agent_tools.py`。
- `mcp/investigation_server.py`提供绑定调查工具；`mineru_document_server.py`只读绑定PDF；`browser_paper_server.py`只获取绑定论文。四类服务均走stdio，诊断不得污染stdout。
- 新增协议复用模块公开服务，通过`composition.py`装配依赖，不直接执行SQL。

制作执行角色为editor、writer、reviewer、publisher。reviewer包含阅读评审（reading-review）和事实核验（fact-check）；协议标识与展示名称分开，完整任务契约见`runtime/production_execution.py`。
