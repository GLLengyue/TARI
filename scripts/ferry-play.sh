#!/usr/bin/env bash
# ============================================================================
# 一键体完整体验：原创样本《最后一班渡船》
#
# 用法:
#   ./scripts/ferry-play.sh              完整跑一遍（开头 -> 两条路线）
#   ./scripts/ferry-play.sh --scenes 1   每个分支只读 1 个场景（更快，更省）
#
# 说明:
#   - 用真实 LLM 写手（TARI_LLM_* 配置，见 .env），不使用 Fake
#   - 每次运行都带时间戳生成新的会话 id 和 request-id
#   - request-id 在 Story 库里是【全局唯一主键】，跨会话复用会被拒绝，
#     所以脚本不允许你手工复用旧 id
#   - 场景审查拒绝草稿时（输出含 "scene review rejected"），自动用
#     同一 request-id 重试一次 —— 已发布的场景会复用，不重复计费
# ============================================================================
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUNDLE="examples/story/last_ferry.yaml"
SCENES="${2:-3}"
PLAYER="林岑"
STAMP="$(date +%m%d-%H%M%S)"
S_MAIN="ferry-${STAMP}"
S_SPLIT="ferry-x-${STAMP}"

if [ ! -d .venv ]; then
  echo "错误: 缺少 .venv，请先执行: python -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

source .venv/bin/activate
set -a
source .env
set +a

echo "============================================================"
echo " TARI 连续阅读体验：最后一班渡船"
echo " 写手:      真实 LLM (${TARI_LLM_MODEL:-未配置})"
echo " 会话(main):   $S_MAIN"
echo " 会话(分支):   $S_SPLIT"
echo " 每段场景数:   $SCENES"
echo "============================================================"
echo

# 读一段，遇到审查拒绝就用同一 request-id 自动重试一次
read_scenes() {
  local session="$1" request="$2" label="$3"; shift 3
  echo "----- ${label} (request-id: ${request}) -----"
  local out
  out="$(trpg story-read "$BUNDLE" "$session" --author llm \
        --request-id "$request" --scenes "$SCENES" "$@" 2>&1)" || true
  printf '%s\n' "$out"
  if printf '%s' "$out" | grep -q "scene review rejected"; then
    echo
    echo ">>> 审查拒绝了草稿，用同一 request-id 重试（已发布场景会复用）..."
    trpg story-read "$BUNDLE" "$session" --author llm \
      --request-id "$request" --scenes "$SCENES" "$@" 2>&1 || true
  fi
  echo
}

# ---- 第一段：公共开头，停在决定点 ----
trpg story-new "$BUNDLE" --session-id "$S_MAIN" --player-name "$PLAYER" >/dev/null
read_scenes "$S_MAIN" "open-${STAMP}" "第一步 · 公共开头"

# ---- 在决定点建分支（必须在选择之前，否则分支会继承已选的结局）----
trpg story-branch "$S_MAIN" crossing
echo

# ---- 第二段：main 上选择「留下」----
read_scenes "$S_MAIN" "stay-${STAMP}" "第二步 · 选择「留下」"

# ---- 第三段：把开头重新走一遍到决定点，再选「渡河」----
trpg story-new "$BUNDLE" --session-id "$S_SPLIT" --player-name "$PLAYER" >/dev/null
read_scenes "$S_SPLIT" "open2-${STAMP}" "第三步 · 公共开头（第二份，用于对比）"
trpg story-branch "$S_SPLIT" crossing
echo
read_scenes "$S_SPLIT" "cross-${STAMP}" "第四步 · 选择「渡河」"

# ---- 对比两条路线的存档 ----
echo "============================================================"
echo " 两条路线的结局对比"
echo "============================================================"
.venv/bin/python - "$S_MAIN" "$S_SPLIT" <<'PY'
import json, sqlite3, sys

main_session, split_session = sys.argv[1], sys.argv[2]
con = sqlite3.connect("runtime-data/trpg.db")
for session in (main_session, split_session):
    for branch, state in con.execute(
        "SELECT branch_id, state_json FROM story_snapshots WHERE session_id=? ORDER BY branch_id",
        (session,),
    ):
        st = json.loads(state)
        var = st.get("variables") or {}
        if var.get("letter_delivered") is None and st.get("current_beat_id") != "delivered":
            continue
        print(
            f"  {session:16s} / {branch:10s} 最终场景={st.get('current_beat_id'):12s}"
            f" 信件送达={var.get('letter_delivered')}"
        )
PY
echo
echo "读完了。要看某个分支的存档原文，直接查 runtime-data/trpg.db 的 story_snapshots 表。"
