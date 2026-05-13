# CargoFlow Agent Guide

This repository is the working record for CargoFlow, a smart logistics product. Keep this file short and use the linked documents for durable detail.

## Start Here

- Tech architecture: [docs/exec-plans/2026-05-13-tech-architecture-constraints.md](docs/exec-plans/2026-05-13-tech-architecture-constraints.md)
- Design constraints: [DESIGN.md](DESIGN.md)
- Execution plans: [docs/exec-plans/](docs/exec-plans/)
- Knowledge scope: [docs/knowledge/qa-knowledge-scope-and-citations.md](docs/knowledge/qa-knowledge-scope-and-citations.md)

## Task System

Tasks are tracked in the CargoFlow Feishu Base task table.

- Base token: `UYc2bRhxla7a5EszGkycFBvwnWb`
- Table ID: `tblbrap4E9Jk5Vng`
- Default env vars:
  - `CARGOFLOW_BASE_TOKEN=UYc2bRhxla7a5EszGkycFBvwnWb`
  - `CARGOFLOW_TASK_TABLE_ID=tblbrap4E9Jk5Vng`
  - `CARGOFLOW_LARK_AS=user`

Do not rely on chat history as the task source. Create or update task records when work is split, claimed, blocked, near due, or ready for acceptance.

## Task Claiming Rule

When starting implementation work from the task table, create a git worktree under `../CargoFlow-worktrees` and always base each new worktree on the latest remote `main` branch. Select one task from the table that is not already in an active or terminal state such as `In Progress`, `Done`, `进行中`, `待验收`, or `已完成`, then claim it and begin work. After completing the task, submit a PR and ensure the CI pipeline passes.

If there is no selectable task, or all available tasks are waiting on prerequisites, first check whether any task can be moved to `待领取` and then retry task selection. If no task can be made claimable, do not execute implementation work.

## Task Flow

1. Convert PRD work into a parent task or execution-plan slice.
2. Split parent work into testable subtasks with a clear owner, module, priority, source, due time, and acceptance criteria.
3. For UI or frontend work, read `DESIGN.md` before implementation and include design-specific acceptance criteria.
4. Use the Base status flow:
   `待拆分 -> 待领取 -> 进行中 -> 待验收 -> 已完成`
5. Use `阻塞` when progress depends on an external decision, credential, environment, API, or unresolved product question.
6. Use `已关闭` for discarded or superseded work. Keep the close reason in `阻塞原因` or the task notes if available.

## Scripts

Run from the repository root.

## 飞书通知规则

- 领取任务后、开始修改前、检查失败、准备提交 PR、Review 修改完成时，使用飞书脚本给任务作者发送状态通知。
- 遇到需要用户输入、人工确认或人工授权的步骤时，必须私聊通知本人，再暂停等待输入或授权。
- 任务执行完成时，必须私聊通知本人，说明完成范围和验证结果。
- 需要用户补充信息、确认范围或人工介入时，先发送飞书通知，再停止当前执行并等待输入。

```bash
scripts/check.sh
scripts/lark/init-task-table.sh --dry-run
scripts/lark/create-task.sh --title "FR-01 货物位置追踪接口切片" --type 拆分任务 --status 待领取 --priority P1 --module 数据监测 --source "PRD FR-01" --acceptance "接口可返回最新位置、状态和延迟提示" --dry-run
scripts/lark/list-my-tasks.sh --owner ou_xxx
scripts/lark/notify-task.sh --record-id rec_xxx --event due-soon --dry-run
```

## Quality Gate

Before handing work back:

```bash
scripts/check.sh
```

The check script validates repository structure, design guideline wiring, Markdown links, common conflict markers, and likely secret leaks. Add project-specific tests to `scripts/check.sh` when code enters the repository.

## Engineering Notes

- Keep implementation plans small enough to verify.
- Treat `DESIGN.md` as the source of truth for UI visual constraints; update it before changing product-wide design rules.
- Prefer explicit task records over implicit TODOs.
- Keep PRD changes in `docs/`; keep execution sequencing in `docs/exec-plans/`.
- Do not add package managers or project dependencies for repository housekeeping scripts unless the product implementation requires them.
