# PineFlow

[English](README.md)

<p align="center">
  <img src="apps/desktop/src/assets/pineflow-wordmark.png" alt="PineFlow" width="360">
</p>

PineFlow 是一个面向 QGIS 工作流的 GIS 自动化智能体平台。它通过大语言模型驱动的 agentic loop 和原生工具调用能力，将用户的自然语言 GIS 需求转化为经过规则校验的 QGIS Processing 操作。

PineFlow 的目标不是生成一次性的临时代码脚本，也不是依赖固定的大 planner 机械执行。系统采用 ReAct-first 的 agentic loop：观察当前 GIS 状态，选择一个工具调用，校验并执行，读取 observation，验证结果，然后继续下一步，直到任务完成、失败或需要用户确认。

## 项目特性

- 自然语言驱动 GIS 工作流
- 基于 agentic loop 的逐步执行流程：观察、选择工具、校验、执行、验证、继续
- 使用 LLM 原生 tool calling
- 通过 PyQGIS runtime 调用 QGIS Processing 工具
- 使用 ToolKit 机制按需暴露工具能力，减少模型上下文噪音
- 使用 Skills 机制注入 GIS 领域知识
- 通过规则网关进行语义校验和执行前检查
- 支持会话状态、运行事件、输出产物和工作区状态管理
- 提供 FastAPI 后端服务
- 提供 Tauri v2 + React 桌面端界面

## 项目结构

```text
src/
  pineflow_agent/
    core/              智能体核心模型、工作区状态、消息、产物记录
    llm/               LLM 客户端、模型适配器、上下文构建
    orchestration/     ReAct 执行循环、恢复流程、运行事件、结果生成
    policies/          输出、坐标系、自主性等策略
    risks/             风险诊断、风险分类和转换逻辑
    rules/             语义校验、执行前检查、恢复规则
    tools/             工具定义、工具注册、ToolKits、QGIS 工具封装

  pineflow_api/
    application/       运行任务、会话、状态查询等应用服务
    contracts/         API 契约、运行生命周期、事件、快照模型
    entrypoints/       FastAPI 应用入口和 PyQGIS worker 入口
    persistence/       SQLite 会话状态、事件流、运行快照持久化
    routing/           Slash command、意图路由、会话路由

  pineflow_runtime/
    runtime.py         具体 PyQGIS 执行逻辑
    errors.py          运行时错误定义

apps/
  desktop/
    src/               React 前端源码
    src-tauri/         Tauri v2 原生桌面端工程
    package.json       桌面端依赖和脚本
    vite.config.js     Vite 配置

resources/
  skills/              智能体加载的 GIS 领域知识指导文件
  toolkits/            ToolKit YAML 定义文件

.pineflow/             本地运行状态和默认会话输出，不进入 Git
```

## 系统架构

PineFlow 由四个主要层次组成：

```text
Desktop UI
   ↓
FastAPI Backend
   ↓
ReAct GIS Agent
   ↓
QGIS / PyQGIS Runtime
```

### 桌面端

桌面端位于 `apps/desktop/`，使用 Tauri v2 和 React 构建。它负责提供用户交互界面，包括会话列表、数据源管理、聊天输入、运行状态、工作流步骤展示和结果展示。

桌面端不直接执行 GIS 操作。它通过后端 API 创建运行任务、轮询运行事件，并渲染会话状态和工作区状态。

### 后端 API

后端位于 `src/pineflow_api/`，使用 FastAPI 构建。它负责管理 session、run、事件流、状态快照、slash command、意图路由和执行编排。

### Agent 核心

Agent 位于 `src/pineflow_agent/`。它负责把用户请求转化为一系列经过校验的 GIS 工具调用。

执行流程大致如下：

```text
读取当前工作区状态
  ↓
构建 ReAct prompt
  ↓
调用 LLM 选择一个原生工具
  ↓
通过规则网关校验工具参数
  ↓
执行工具
  ↓
记录 observation
  ↓
继续下一轮、请求用户确认，或输出最终答案
```

工具选择通过 LLM provider 的原生 tool-calling 接口完成。

### PyQGIS Runtime

Runtime 位于 `src/pineflow_runtime/`。它负责真正执行 QGIS 操作，例如 buffer、clip、fix geometries、raster calculator、结果导出等。

API 和 Agent 可以运行在普通 Python 环境中。具体 QGIS 操作会在需要时委托给 PyQGIS runtime 边界执行。

## 智能体机制

PineFlow 的核心是一个面向 GIS 工作流的 agentic loop。它不是一次性生成完整脚本，也不是先生成固定计划再机械执行，而是持续观察当前 workspace，选择一个工具调用，经过规则校验后执行，再根据 observation 和结果校验决定下一步。

### Agentic Loop

PineFlow 使用 ReAct-first 的 agentic loop：

```text
观察 workspace state
  ↓
推理下一步 GIS 操作
  ↓
选择一个原生 tool call
  ↓
经过 rules gateway 校验
  ↓
通过 tool registry / PyQGIS runtime 执行
  ↓
把工具结果记录为 observation
  ↓
验证状态和输出，然后继续或结束
```

这种方式让每一步都可检查、可恢复，也更容易进行规则校验和调试。

### Native Tool Calling

PineFlow 使用 LLM 原生工具调用能力。模型返回结构化工具调用，Agent runtime 再对其进行校验和执行。

不同模型供应商之间的差异集中在 LLM adapter 层处理。

### Rules Gateway

每个 GIS action 在执行前都必须经过规则网关。

规则网关主要执行：

- Semantic validation：检查 action 形状、必填 slots、枚举值等
- Preflight validation：检查图层是否存在、字段是否存在、几何类型是否匹配、CRS 是否合理等 GIS 状态风险

### ToolKits

ToolKits 用于控制当前有哪些 GIS 工具对模型可见。它可以减少上下文噪音，让模型更稳定地选择工具。

定义文件位于：

```text
resources/toolkits/
```

常见 ToolKits 包括：

- `data_io`
- `vector_transform`
- `vector_analysis`
- `vector_overlay`
- `raster`
- `qgis_generic`

### Skills

Skills 是带 YAML frontmatter 的 Markdown 指导文件。它们不是可执行 workflow，而是提供给 Agent 参考的 GIS 领域知识。

定义文件位于：

```text
resources/skills/
```

示例场景包括米制距离 buffer、CSV 转点图层、边界过滤、空间连接、栅格基础处理和 CRS 选择。

### 会话状态

PineFlow 会维护本地 session state、run events、snapshots、output artifacts 和 workflow state。工具失败也会作为 observation 保留，后续轮次可以根据具体错误反馈继续调整。

### ReAct 上下文管理

PineFlow 是 ReAct-first，不是 planner-first。长期状态由 session store、run snapshot、transcript projection、artifact index 和 workflow state 维护。agent 会收到当前 GIS 状态、相关 artifacts、可见工具、最近 observations，以及存在时的当前 workflow step。

这个模型里：

- ReAct loop 负责选择具体工具。
- RulesGateway、preflight checks、pending tasks 和 repair flows 负责校验、恢复和继续执行。
- Workflow state 是上下文锚点，但不替代 ReAct 的工具选择。
- 历史 observations 应沉淀为紧凑状态，而不是每轮完整塞回 prompt。

这样上下文压缩会更稳定：prompt 可以保留紧凑状态、当前 workflow step、最近 observations 和 artifacts，避免完整历史不断增长。单纯增加一个更大的 planner 不能解决 token 增长；PineFlow 保持 agentic execution。

## 适用场景

PineFlow 适合用于探索自然语言驱动的 GIS 自动化流程，例如：

- 加载矢量、栅格和 CSV 数据
- 执行 buffer、clip、intersect、dissolve 等空间分析
- 将 CSV 表格转换为空间点图层
- 检查 CRS 并执行重投影
- 执行基础栅格处理流程
- 导出 GeoJSON、GeoPackage、Shapefile 等结果

## 环境要求

- Python 3.10+
- Node.js 18+
- Rust toolchain
- QGIS LTR
- OpenAI-compatible LLM provider，例如 DeepSeek、OpenAI-compatible APIs、Qwen 或 GLM

真实 GIS 处理需要本地安装 QGIS。普通代码检查和部分 UI 开发不一定需要启动 QGIS。

## 环境配置

从模板创建本地 `.env`：

```powershell
Copy-Item .env.example .env
```

常用配置：

```env
PINEFLOW_LLM_PROVIDER=deepseek
PINEFLOW_LLM_BASE_URL=https://api.deepseek.com
PINEFLOW_LLM_MODEL=deepseek-v4-pro
PINEFLOW_LLM_API_KEY=

QGIS_LAUNCHER=D:\software\QGIS\bin\python-qgis-ltr.bat
QGIS_PREFIX_PATH=D:\software\QGIS\apps\qgis-ltr
```

`.env` 只用于本地开发，不应提交到 Git。

## QGIS 配置

PineFlow 会把后端/Agent 的普通 Python 环境和 PyQGIS runtime 分开。FastAPI 服务和 Agent 可以运行在普通 Python 环境中；真正的 GIS 处理会在需要时委托给本地 QGIS 安装环境执行。

QGIS 相关配置主要有两个路径：

- `QGIS Launcher`：QGIS Python 启动器，通常是一个 `.bat` 文件或可执行文件。PineFlow 用它启动 QGIS 自带 Python 环境中的 runtime worker，从而让 PyQGIS imports 和 Processing providers 可用于真实 GIS 操作。
- `QGIS Prefix Path`：QGIS application prefix directory。QGIS 通过这个路径定位自身库、插件、资源文件和 Processing algorithms。

在桌面端设置里，这两个字段对应 `QGIS Launcher` 和 `QGIS Prefix Path`。

在 Windows 的 QGIS LTR 安装中，常见配置类似：

```env
QGIS_LAUNCHER=D:\software\QGIS\bin\python-qgis-ltr.bat
QGIS_PREFIX_PATH=D:\software\QGIS\apps\qgis-ltr
```

常见 launcher 示例：

```text
D:\software\QGIS\bin\python-qgis-ltr.bat
C:\Program Files\QGIS 3.34.*/bin/python-qgis-ltr.bat
C:\Program Files\QGIS 3.40.*/bin/python-qgis.bat
```

常见 prefix path 示例：

```text
D:\software\QGIS\apps\qgis-ltr
C:\Program Files\QGIS 3.34.*/apps/qgis-ltr
C:\Program Files\QGIS 3.40.*/apps/qgis
```

launcher 和 prefix path 都不是输入数据目录。它们描述的是 PineFlow 如何找到并启动本机 QGIS runtime。

实际使用时：

- `QGIS Launcher` 用于通过 QGIS 自带 Python 环境执行子进程 runtime。这是 Windows 上推荐的方式。
- `QGIS Prefix Path` 用于告诉 QGIS 自身应用资源在哪里，也会传给子进程 runtime。
- 如果没有 launcher，PineFlow 会尝试使用 `QGIS_PREFIX_PATH` 在当前进程中初始化 runtime；这只有在当前 Python 环境可以 import 并初始化 PyQGIS 时才可用。

如果没有正确配置 QGIS，API 和桌面端界面可能仍能启动，但真实 GIS 执行会失败，例如 buffer、clip、重投影、栅格处理和结果导出等操作。

## 安装 Python 包

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
```

## 启动后端 API

```powershell
py -m pineflow_api --host 127.0.0.1 --port 8765
```

后端默认监听：

```text
http://127.0.0.1:8765
```

主要 API 路由位于：

```text
/qgis/*
```

## 启动桌面端

```powershell
cd apps/desktop
npm install
npm run dev
```

浏览器调试模式：

```powershell
npm run dev:web
```

构建 Web 版本：

```powershell
npm run build:web
```

构建原生桌面端：

```powershell
npm run build
```

## 本地运行状态和输出

PineFlow 会把本地运行状态保存在 `.pineflow/` 下。这个目录不是源码，也不会提交到 Git。

默认会话输出路径是：

```text
.pineflow/sessions/{session_id}/outputs/
```

运行目录结构大致如下：

```text
.pineflow/
  pineflow_state.db          本地 SQLite 会话存储
  sessions/
    {session_id}/
      outputs/               默认导出结果目录
      temp/                  QGIS 中间处理结果
      artifacts/             运行产物目录，存在时生成
```

新克隆项目时 `.pineflow/` 不一定存在。它通常会在本地运行时自动生成。

## 仓库提交规则

本仓库提交：

```text
README.md
README.zh-CN.md
LICENSE
.gitignore
.env.example
pyproject.toml
package.json
src/
apps/
resources/
```

本仓库不提交：

```text
.env
.pineflow/
output/
docs/
tests/
node_modules/
dist/
target/
__pycache__/
.pytest_cache/
.pytest_tmp/
AGENTS.md
CLAUDE.md
```

## 开发状态

PineFlow 目前是原型系统和毕业设计项目。核心架构已经成型，但工具定义、API 细节、UI 行为和 QGIS runtime 兼容性仍可能继续调整。

## License

本项目使用 MIT License。
