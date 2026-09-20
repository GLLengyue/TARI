#!/usr/bin/env bash
# ============================================================================
# 一键编译：古龙《多情剑客无情剑》(89 章, ~44 万字) -> TARI 故事包
#
# 用法:
#   ./scripts/duqing-compile.sh            开始编译 / 中断后重跑续编
#   ./scripts/duqing-compile.sh --status   只看当前进度, 不启动
#   ./scripts/duqing-compile.sh --fresh    清空工作区从头再来
#
# 说明:
#   - 进度实时打印到终端, 同时记入 logs/duqing-compile-<日期>.log
#   - 中断(Ctrl-C / 断网 / 关机)后重跑本脚本即可从断点续编, 已完成的
#     章节不会重新生成
#   - 本地 27B 模型整本预计数小时, 建议挂着过夜
# ============================================================================
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SOURCE="runtime-data/sources/duqing/多情剑客无情剑.md"
WORKSPACE="runtime-data/story-books/duqing"
STORY_ID="duqing"
TITLE="多情剑客无情剑"
LOCKFILE="$WORKSPACE/.compile.lock"

mkdir -p logs "$WORKSPACE"
LOGFILE="logs/duqing-compile-$(date +%Y%m%d).log"

show_status() {
  echo "================ 当前状态: $WORKSPACE ================"
  if [ ! -f "$WORKSPACE/manifest.json" ]; then
    echo "  尚未开始 (还没有编译记录)"
    echo "=================================================="
    return
  fi
  .venv/bin/python - "$WORKSPACE" <<'PY'
import json, sys

m = json.load(open(sys.argv[1] + "/manifest.json"))
print(f"  status:     {m.get('status')}")
print(f"  规模:       全书 {m.get('total_chapters')} 章 / 目标 {m.get('target_chapters')} 章")
stages = m.get("stages") or {}
cards_done = stages.get("chapter_cards_completed", 0)
print(f"  ① 章节事实卡: {cards_done}/{m.get('target_chapters', '?')} 完成"
      + ("  [阶段完成]" if stages.get("chapter_cards") else ""))
for key, label in (
    ("story_arcs", "② 情节弧"),
    ("world_knowledge", "③ 世界知识"),
    ("structures", "④ 结构大纲"),
    ("novel_outline", "⑤ 全书总纲"),
):
    if stages.get(key):
        extra = stages.get(key + "_completed") or stages.get(key + "_completed_through") or ""
        print(f"  {label}: 完成 {('章节 ' + str(extra)) if extra else ''}".rstrip())
failures = m.get("failures") or {}
if failures:
    print(f"  失败点:     {json.dumps(failures, ensure_ascii=False)}")
if m.get("bundle_path"):
    print(f"  故事包:     {m.get('bundle_path')}")
PY
  echo "=================================================="
}

# --status: 只看进度, 不启动编译
if [ "${1:-}" = "--status" ]; then
  show_status
  exit 0
fi

# ---- 前置检查 ----
if [ ! -d .venv ]; then
  echo "错误: 缺少 .venv, 请先在项目根目录执行:  python -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi
if [ ! -f "$SOURCE" ]; then
  echo "错误: 缺少原文 $SOURCE (应为 89 章完整文本)"
  exit 1
fi

source .venv/bin/activate
set -a
source .env
set +a

echo "============================================================"
echo " TARI 一键编译: $TITLE (古龙, 89 章)"
echo "============================================================"
echo " 原文:      $SOURCE ($(wc -c < "$SOURCE") 字节)"
echo " LLM:       ${TARI_LLM_BASE_URL:-未设置}  model=${TARI_LLM_MODEL:-未设置}  thinking=${TARI_LLM_THINKING_LEVEL:-off}"
echo " 工作区:    $WORKSPACE"
echo " 日志:      $LOGFILE"
echo "============================================================"

if [ -z "${TARI_LLM_MODEL:-}" ]; then
  echo "错误: TARI_LLM_MODEL 未设置 (检查 .env)"
  exit 1
fi

# 探活必须打一次真实的 chat 请求: /models 只能证明 server 活着,
# 证明不了模型 id 存在。2026-09-19 曾因 .env 里写了不存在的 `qwen3.8-27b`
# 而白跑一章 —— server 会接受连接、拒绝请求。
if ! curl -sf -m 60 "${TARI_LLM_BASE_URL:-http://invalid}/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TARI_LLM_API_KEY:-sk-no-key}" \
  -d "{\"model\":\"${TARI_LLM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":4}" \
  >/dev/null 2>&1; then
  echo "错误: endpoint 或模型不可用: ${TARI_LLM_BASE_URL:-?} / model=${TARI_LLM_MODEL}"
  echo "常见原因: 模型 id 写错 (server 会返回 400 model not found)、服务未启动。"
  echo "查看 server 上的有效 id:  curl -s ${TARI_LLM_BASE_URL:-http://invalid}/models"
  echo "修好后重跑本脚本即可续跑。"
  exit 1
fi

# 防止重复启动
if [ -f "$LOCKFILE" ]; then
  PID="$(cat "$LOCKFILE" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "已有编译在运行 (PID $PID)。查进度: $0 --status"
    exit 1
  fi
fi
echo "$$" > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

if [ "${1:-}" = "--fresh" ]; then
  echo "--fresh: 清空工作区 $WORKSPACE, 从头开始"
  rm -rf "$WORKSPACE"
  mkdir -p "$WORKSPACE"
fi

echo
show_status
echo

# ---- 编译 (进度实时打印; 中断后重跑续编) ----
trpg story-compile "$SOURCE" \
  --output-dir "$WORKSPACE" \
  --story-id "$STORY_ID" \
  --title "$TITLE" \
  --lang zh \
  --parallelism 1 \
  2>&1 | tee -a "$LOGFILE"

echo
echo "================ 本次运行结束 ================"
show_status
echo

if [ -f "$WORKSPACE/bundle.yaml" ]; then
  cat <<'NEXT'

============================================================
 编译完成! 下一步: 用真实 LLM 写手读第一幕

   ./scripts/duqing-play.sh

 或手动:

   trpg story-new runtime-data/story-books/duqing/bundle.yaml \
     --session-id duqing-demo --player-name 李寻欢
   trpg story-read runtime-data/story-books/duqing/bundle.yaml \
     duqing-demo --author llm --request-id opening
============================================================
NEXT
else
  echo "故事包尚未生成 (编译未完成或有失败点)。"
  echo "修复后重跑本脚本即可续编:  ./scripts/duqing-compile.sh"
fi
