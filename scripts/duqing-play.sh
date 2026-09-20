#!/usr/bin/env bash
# ============================================================================
# 编译完成后的阅读入口: 用真实 LLM 写手生成《多情剑客无情剑》开场
#
# 用法:
#   ./scripts/duqing-play.sh                  # 用默认工作区的 bundle
#   ./scripts/duqing-play.sh <bundle.yaml>    # 指定 bundle 文件
#
# 说明:
#   - 先创建会话 duqing-demo (玩家身份: 李寻欢)
#   - 然后请求阅读 3 个场景, 真实 LLM 写手逐场景生成
#   - 中断后用同一 request-id (opening) 重跑即可恢复已发布的场景
# ============================================================================
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUNDLE="${1:-runtime-data/story-books/duqing/bundle.yaml}"
SESSION="duqing-demo"

if [ ! -f "$BUNDLE" ]; then
  echo "错误: 找不到故事包 $BUNDLE"
  echo "请先完成编译:  ./scripts/duqing-compile.sh"
  exit 1
fi

source .venv/bin/activate
set -a
source .env
set +a

echo "============================================================"
echo " 阅读: $BUNDLE"
echo " 会话: $SESSION   写手: 真实 LLM (${TARI_LLM_MODEL:-?})"
echo "============================================================"

# 会话已存在时 story-new 会报错, 这里容忍 (重跑场景)
trpg story-new "$BUNDLE" --session-id "$SESSION" --player-name 李寻欢 \
  || echo "提示: 会话 $SESSION 已存在, 直接继续"

trpg story-read "$BUNDLE" "$SESSION" --author llm --request-id opening --scenes 3

echo
echo "阅读输出在上方。继续读:  ./scripts/duqing-play.sh  (换新 request-id 前, 先 trpg story-branch 或新建会话)"
