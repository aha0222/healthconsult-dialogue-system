"""
小暖健康陪护 - 对话脚本
========================

回复逻辑：
  API模式：用户输入 → LLM根据SKILL.md自主判断风险 → 回复末尾带[RISK:Rx]标记 → 前端提取 → 质检兜底
  本地模式：预置场景走查（关键词匹配仅供参考）

用法：
  # 本地测试模式（预置场景，无需API）
  python scripts/chat.py --mode local

  # API 对话模式
  python scripts/chat.py --mode api --api-key sk-xxx --model deepseek-chat --base-url https://api.deepseek.com/v1

  # 单次问答
  python scripts/chat.py --mode api --api-key sk-xxx --ask "我血压有点高，该加药吗"
"""

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent  # scripts/ → 项目根目录


def load_skill():
    skill_path = SCRIPT_DIR / "SKILL.md"
    if not skill_path.exists():
        print("[错误] 找不到 SKILL.md")
        sys.exit(1)
    return skill_path.read_text(encoding="utf-8")


# ── 安全快检（关键词 + 否定句式过滤，事后兜底） ──

NEGATION = re.compile(
    r'(?:不要|不能|不可|别|切勿|禁止|避免|不是|不会|不一定).{0,30}?'
    r'(?:自己加|自己减|自己停|换药|调药|幻觉|老年痴呆|抑郁症)'
)

FORBIDDEN = {
    "我给你开药": "开药", "我给您开药": "开药",
    "药量你自己调": "引导调药",
    "你自己加": "引导加药", "你自己减": "引导减药", "你自己停": "引导停药",
    "停掉试试": "引导停药", "先不吃看看": "引导停药",
    "不用去医院": "劝退就医", "没必要看医生": "劝退就医",
    "喝点热水就好": "轻视症状", "睡一觉就好了": "延误就医",
    "躺一会儿看看": "延误急症",
    "你这是高血压": "诊断", "你这是糖尿病": "诊断",
    "你这是老年痴呆": "标签", "你就是幻觉": "否定感知",
    "先吃片药观察": "急症延误-建议吃药观察", "先吃药看看": "急症延误-建议吃药观察",
}


def quick_check(reply, llm_risk):
    """LLM回复的事后安全兜底检查（已过滤否定句式）"""
    # 先去除否定句式中的安全警告部分，避免"不要自己加药"被误判为"引导加药"
    cleaned = NEGATION.sub('[安全警告已过滤]', reply)

    violations = []
    for phrase, reason in FORBIDDEN.items():
        if phrase in cleaned:
            violations.append(f"禁止话术: {reason}({phrase})")

    if llm_risk == "R3":
        if not any(k in reply for k in ("120", "急救", "急诊")):
            violations.append("LLM判定R3但回复缺失120/急救/急诊")

    return violations


# ── 本地测试模式（仅作快速预览，风险分级用正则参考） ──

LOCAL_SCENARIOS = [
    ("R0", "今天天气好，我出去遛弯回来膝盖有点酸"),
    ("R0", "我有糖尿病，无糖饼干能吃几块"),
    ("R0", "今天是我老伴走了三年的日子，心里空落落的"),
    ("R1", "医生开的药饭前饭后不一样，我老记混，能一块吃吗"),
    ("R1", "最近老是半夜两三点醒，醒了就再也睡不着了"),
    ("R2", "邻居老张吃的降压药好，我能换那个吗"),
    ("R2", "老看见墙角有人影一闪一闪的，是不是脑子出问题了"),
    ("R3", "胸口闷得慌，喘不上气，后背也疼，是不是累着了"),
    ("R3", "半边身子突然麻了，嘴也有点歪"),
]


def classify_risk_local(text):
    """本地模式简版风险分级（仅供参考，不用于API模式）"""
    if re.search(r'胸[口闷疼痛慌紧]', text) and re.search(r'后背|肩|臂|喘|汗|冷|压', text):
        return "R3"
    if re.search(r'半边|一侧.*[麻无力动]|嘴[歪斜]|口[歪斜角]', text):
        return "R3"
    if any(k in text for k in ["喘不上气", "呼吸困难", "说胡话", "意识不清", "叫不醒", "心梗", "中风", "卒中"]):
        return "R3"
    if re.search(r'胸[口闷疼痛慌紧]', text):
        return "R2"
    if any(k in text for k in ["咳血", "便血", "黑影", "人影", "幻觉", "不认识人", "找不到家", "摔", "换药", "停药"]):
        return "R2"
    if any(k in text for k in ["血压", "血糖", "药", "睡不着", "醒", "便秘", "头晕", "胃口"]):
        return "R1"
    return "R0"


def local_mode():
    print("=" * 60)
    print("  小暖健康陪护 · 本地测试模式")
    print("  预置场景走查 → 风险分级（仅正则参考）→ 安全规则核对")
    print("=" * 60)

    for expected_risk, user_input in LOCAL_SCENARIOS:
        risk = classify_risk_local(user_input)
        match = "[OK]" if risk == expected_risk else f"[WARN] 预期{expected_risk}"
        print(f"\n{'─' * 56}")
        print(f"老人说：{user_input}")
        print(f"正则判定：{risk} {match}")

    print(f"\n{'=' * 60}")
    print("本地测试完成。API 对话模式：")
    print("  python scripts/chat.py --mode api --api-key YOUR_KEY --model deepseek-chat --base-url https://api.deepseek.com/v1")
    print(f"{'=' * 60}")


# ── API 模式（LLM 自主分级）────────────────────────────

def api_mode(api_key, base_url, model, single_ask=None):
    try:
        from openai import OpenAI
    except ImportError:
        print("[错误] 需要安装 openai 库: pip install openai")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=base_url)
    skill_content = load_skill()

    messages = [{"role": "system", "content": skill_content}]

    print("=" * 60)
    print("  小暖健康陪护 · API 对话模式（LLM自主分级）")
    print(f"  模型：{model}")
    print("  输入 'quit' 或 '退出' 结束对话")
    print("=" * 60)

    def chat_one(user_input):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": skill_content},
                    *messages[-6:],
                    {"role": "user", "content": user_input},
                ],
                temperature=0.7,
                max_tokens=600,
            )
            raw_reply = response.choices[0].message.content.strip()
        except Exception as e:
            return f"[API 错误] {str(e)}", "?"

        # 从回复末尾提取 [RISK:Rx]
        m = re.search(r'\[RISK:(R[0-3])\]\s*$', raw_reply)
        llm_risk = m.group(1) if m else "?"
        reply = raw_reply[:m.start()].strip() if m else raw_reply

        # 安全自检
        violations = quick_check(reply, llm_risk)
        if violations:
            print(f"\n[质检警告] {'; '.join(violations)}")

        return reply, llm_risk

    if single_ask:
        reply, risk = chat_one(single_ask)
        print(f"\n老人说：{single_ask}")
        print(f"LLM判定风险：{risk}")
        print(f"\n小暖说：\n{reply}")
        return

    while True:
        try:
            user_input = input("\n您说：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见，祝您健康~")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "退出", "exit", "q"):
            print("小暖：再见，祝您健康，有事随时找我~")
            break

        reply, risk = chat_one(user_input)
        print(f"\n[系统] LLM判定风险={risk}")
        print(f"\n小暖说：\n{reply}")

        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": reply})


# ── 主入口 ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="小暖健康陪护 - 对话脚本")
    parser.add_argument("--mode", choices=["local", "api"], default="local")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--base-url", default="https://api.openai.com/v1", help="API Base URL")
    parser.add_argument("--model", default="gpt-4o-mini", help="模型名称")
    parser.add_argument("--ask", help="单次问答（api模式）")
    args = parser.parse_args()

    if args.mode == "local":
        local_mode()
    elif args.mode == "api":
        if not args.api_key:
            print("[错误] API 模式需要 --api-key 参数")
            sys.exit(1)
        api_mode(args.api_key, args.base_url, args.model, args.ask)


if __name__ == "__main__":
    main()
