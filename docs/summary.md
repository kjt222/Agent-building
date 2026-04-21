# Agent Retrospective and Method Notes

## Reflection
1. I did not confirm runtime mode (web vs desktop) early, which caused UI direction drift.
2. Template writes did not guarantee UTF-8, causing decoding failures; I did not verify read-back.
3. Empty-state navigation was not minimal; the chat area included extra configuration entry points.
4. KB open/edit/drag capability was not scoped against browser vs desktop limits, leading to gaps.
5. I did not label the UI as not wired to the model yet, so Send felt broken.

## Reusable Method
1. Lock runtime form and permission boundaries first (web/desktop/system calls), then design UI.
2. Force UTF-8 on key files and verify read-back after writes.
3. Keep the primary flow closed; empty state should offer only one core action.
4. For system interaction, align feasibility early (browser limits vs desktop).
5. Ship each step as a demoable closed loop and list remaining gaps.
## 2026-01-05 Update
6. I added major behavior changes (HTTP adapters + streaming) but didn��t immediately freeze and verify UI flows with the user; I should have posted a minimal verification checklist earlier.
7. I let testing be derailed by local port conflicts and client encoding quirks; I should have standardized one test path (browser or Python client) and documented it.
8. I changed provider plumbing (zhipu/deepseek) but didn��t immediately clean legacy openai_compat entries, which kept configs confusing.
9. I should have separated model latency vs UI blocking early; streaming should have been introduced as a diagnostic before deeper refactors.

## 2026-01-06 Agent3 Lesson
10. **NEVER modify code directly without user approval.** Always write analysis and proposed changes to conversation.md first, wait for user/Agent1 confirmation, then execute. I modified `_should_skip_kb` function without asking, which violated the collaboration protocol.
11. **Discuss first, code later.** Even if the fix seems obvious and beneficial, the collaborative workflow requires: (1) analyze problem → (2) write proposal to conversation.md → (3) wait for approval → (4) implement.

## 2026-01-09 Agent3 Lesson
12. **任何修改必须验收通过才能给我。** 不要实施未经测试验证的代码。在提交给用户之前，必须：(1) 语法验证通过 (2) 基本功能测试通过 (3) 不能只检查import，要实际运行核心逻辑。
13. **async/sync混用问题**：在async函数中直接调用同步阻塞函数（如数据库查询、网络请求）会阻塞整个事件循环。必须使用`asyncio.to_thread()`或`run_in_executor()`包装同步操作。

## 2026-01-13 Environment & Cleanup Rules
14. **虚拟环境管理严格规则**：
    - **当前环境状况**：项目有两个虚拟环境：
      - `D:\D\python编程\Agent-building\.venv` (项目根目录虚拟环境)
      - `D:\D\python编程\Agent-building\my-agent\.venv` (子项目虚拟环境)
    - **关键问题**：如果不激活虚拟环境，`pip install` 会污染全局环境（miniforge3）
    - **强制规则**：
      1. 安装依赖前**必须先激活虚拟环境**
      2. 使用项目根目录的虚拟环境：`. .venv/Scripts/activate` (Windows) 或 `source .venv/bin/activate` (Linux/Mac)
      3. 确认激活成功：检查 `$VIRTUAL_ENV` 环境变量或 `which python`
      4. 然后再执行 `pip install -r my-agent/requirements.txt`
    - **检查命令**：
      ```bash
      python -c "import sys; print('Python路径:', sys.executable)"
      # 应该输出: D:\D\python编程\Agent-building\.venv\Scripts\python.exe
      # 而不是: C:\Users\kjt\miniforge3\python.exe
      ```

15. **每次运行后强制清理临时文件**：
    - **必须清理的文件**：
      1. `tmpclaude-*-cwd` 文件（项目根目录下）
      2. `__pycache__` 目录（my-agent下所有）
      3. Claude任务输出文件（`C:\Users\kjt\AppData\Local\Temp\claude\D--D-python---Agent-building\tasks\*.output`）
    - **清理命令**（在项目根目录执行）：
      ```bash
      # 1. 清理tmpclaude临时文件
      rm -f tmpclaude-*-cwd

      # 2. 清理Python缓存
      find my-agent -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

      # 3. 清理任务输出（可选，不影响功能）
      rm -f "C:/Users/kjt/AppData/Local/Temp/claude/D--D-python---Agent-building/tasks/"*.output
      ```
    - **何时清理**：
      - 每次测试/运行完成后
      - 提交代码前
      - 切换工作任务前
    - **为什么要清理**：
      - 避免临时文件累积
      - 防止路径混淆
      - 保持项目整洁
      - 避免Git误提交临时文件

16. **测试文件清理规则**：
    - **原则**：测试创建的所有临时数据都必须清理
    - **包括但不限于**：
      - 测试生成的KB数据库文件
      - 测试日志文件
      - 测试配置文件备份
      - 测试生成的输出文件
    - **建议**：测试代码应使用 `pytest fixtures` 的 `teardown` 自动清理

17. **文档职责分离规则**：
    - **conversation.md**：计划、讨论、待实施方案（append-only）
    - **implementation.md**：仅记录**已完成**的实施内容
    - **summary.md**：经验教训、规则、方法论
    - **原则**：待实施内容不写入 implementation.md，只有代码改完并验证通过后才更新
