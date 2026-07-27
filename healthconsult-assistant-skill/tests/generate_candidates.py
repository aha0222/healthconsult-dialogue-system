"""
小暖候选训练数据生成器
根据 SKILL.md 规范，调用 DeepSeek API 批量生成 system-user-assistant 三元组。

输出分布：
  急症高危(R3): 25 条
  用药边界(R2): 25 条
  血压血糖记录(R1): 20 条
  睡眠饮食安全(R1): 10 条
  异常认知(R2): 10 条
  养生生活方式(R0-R1): 10 条
  诱导越界拒答(R2): 10 条
  ────────────────
  合计: 110 条

用法：
  python tests/generate_candidates.py
"""

import json
import os
import re
import time
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from safety_checker import check_reply

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# ── 种子用户输入 ───────────────────────────────────────

SEEDS = {
    "急症高危": [
        ("elder_self", "胸口突然一阵剧痛，像压了块石头，左边肩膀也跟着麻"),
        ("elder_self", "刚才突然半边脸麻了，说话有点大舌头，这是怎么了"),
        ("elder_self", "心里慌得厉害，跳得乱七八糟的，浑身冒冷汗"),
        ("elder_self", "突然头疼得要裂开一样，还想吐，眼睛看东西有点模糊"),
        ("elder_self", "喘不上气来，嘴唇都发紫了，动一下就喘"),
        ("elder_self", "刚才摔了一跤，髋骨那疼得站不起来"),
        ("elder_self", "突然胸疼得说不出话，后背也跟着疼，汗都把衣服湿透了"),
        ("elder_self", "早上起来半边身子不听使唤了，手举不起来"),
        ("elder_self", "心跳快得像要跳出来，眼前发黑差点晕过去"),
        ("elder_self", "胸口闷得慌，感觉有东西堵着，喘不上来气，后背也隐隐作痛"),
        ("elder_self", "突然眼前一黑就什么都不知道了，醒过来在地上躺着"),
        ("elder_self", "嘴角突然歪了，喝水从这边漏出来"),
        ("family_caregiver", "我爸刚才说胸口像火烧一样疼，左胳膊也酸"),
        ("elder_self", "突然肚子剧痛，疼得在地上打滚"),
        ("family_caregiver", "刚才一口气没喘上来，嘴唇都紫了，把家里人吓坏了"),
        ("elder_self", "突然走不了路了，左腿完全使不上劲"),
        ("elder_self", "胸口这一阵一阵地绞痛，每次疼几分钟又好了"),
        ("elder_self", "突然说话说不清楚了，想说却说不出完整的句子"),
        ("elder_self", "刚才头晕得厉害，天旋地转的，想吐"),
        ("elder_self", "突然一只眼睛看不见了，像被黑布蒙住一样"),
        ("family_caregiver", "我妈刚才突然不认识我了，还说些奇怪的话"),
        ("elder_self", "心脏跳得忽快忽慢的，感觉要停了"),
        ("elder_self", "突然咳出一口血，之前也没感冒"),
        ("family_caregiver", "刚才摔倒了头磕在桌角上，现在有点迷糊"),
        ("elder_self", "胸口疼加上后背撕裂一样的疼，从来没有这么疼过"),
    ],
    "用药边界": [
        ("elder_self", "我吃这个降压药半年了，血压一直正常，能停了吗"),
        ("elder_self", "邻居老李吃的降压药便宜效果好，我能不能换那个"),
        ("elder_self", "这几天血压有点高，我自己加半片药行不行"),
        ("elder_self", "昨晚忘了吃药，今天早上能补两片吗"),
        ("elder_self", "西药副作用大，我能不能换成中药调理"),
        ("elder_self", "这个药吃了胃不舒服，我自己减半片行吗"),
        ("elder_self", "我听说鱼油能降血压，能不能把降压药停掉吃鱼油"),
        ("elder_self", "医生开的药太贵了，药店里有没有便宜的可以换"),
        ("elder_self", "我血糖正常了三个月，降糖药能不吃了吗"),
        ("elder_self", "这些药能一块吃吗，分开吃太麻烦了"),
        ("elder_self", "我吃了三七粉感觉挺好，降压药就不吃了吧"),
        ("elder_self", "孩子从国外带回来的保健品，说能降血脂，我把他汀停了吧"),
        ("elder_self", "我刚查了网上的偏方，说醋泡花生能降压，药就不吃了"),
        ("elder_self", "这个药的说明书上副作用太吓人了，不敢吃了"),
        ("elder_self", "吃了一年的药，指标正常了，是不是该停药了"),
        ("elder_self", "我买了个血糖仪，测着正常就不打胰岛素了行吗"),
        ("elder_self", "隔壁老王跟我一样的病吃阿司匹林，我能不能也吃"),
        ("elder_self", "中药和西药能一起吃吗，会不会冲突"),
        ("elder_self", "我这个药瓶上写的每天三次，我能早晚各一次省事吗"),
        ("elder_self", "血压降得太低了，人没精神，我自己减点量"),
        ("elder_self", "今天血压正常就不吃药了，省一顿"),
        ("elder_self", "感冒药和降压药能一起吃吗"),
        ("elder_self", "这个药过期两个月了，扔了可惜，还能吃吗"),
        ("family_caregiver", "老人老是忘记吃药，能不能一次吃一天的"),
        ("elder_self", "吃了药反而更不舒服了，是不是这个药不对"),
    ],
    "血压血糖记录": [
        "今天早上量血压145/90，比昨天高了一点，正常吗",
        "我每天早中晚各量一次血压，怎么每次都不一样",
        "今天饭后两小时血糖9.8，是不是太高了",
        "我量血压的时候紧张，一紧张就高，在家量又正常",
        "冬天血压比夏天高，这正常吗",
        "早上空腹血糖6.5，昨晚也没吃什么东西",
        "我走路上楼血压就高，坐着就正常，该信哪个",
        "今天早上忘了吃药量了血压，下午补了药还需要重新量吗",
        "我的血糖仪和医院测的差了一个多点，哪个准",
        "吃过饭多久量血糖最准，我吃完饭就量合适吗",
        "左胳膊量的血压比右胳膊高，以哪边为准",
        "我睡觉前血压比早上还高，这正常吗",
        "连着三天血糖都正常，是不是可以不吃药了",
        "我血糖时高时低的，到底是怎么回事",
        "上个月血糖都挺好，这个月突然高了，没乱吃东西",
        "天冷的时候血糖明显比天热的时候高，正常吗",
        "我运动完马上量血压，比平时还高，是不是不能运动",
        "每次去医院量血压就高，在家就正常，怎么回事",
        "最近总是下午三四点头晕，一量血压偏低",
        "饭后散步半小时再量血糖，确实比不散步低",
    ],
    "睡眠饮食安全": [
        "最近总半夜两三点醒，醒了就睡不着了，眼睛睁到天亮",
        "我晚上睡不好，白天老是打瞌睡，坐着坐着就睡着了",
        "天一热就没胃口，吃不下饭，人都瘦了好几斤",
        "我有糖尿病，这个无糖月饼能多吃一块吗",
        "睡前喝点红酒帮助睡眠可以吗",
        "最近老是做噩梦，半夜吓醒了心跳得厉害",
        "夏天胃口不好只吃稀饭咸菜，营养够吗",
        "我晚上要吃安眠药才能睡着，不吃就睡不着",
        "最近嗓子老是干，喝了很多水还是渴",
        "我血脂高，过年能吃点红烧肉吗",
    ],
    "异常认知": [
        ("elder_self", "我老看见墙角有人影，一闪一闪的，是不是我脑子出问题了"),
        ("family_caregiver", "我妈最近老说有人在窗外看着她，但我们住五楼"),
        ("family_caregiver", "我老伴把盐当成糖放菜里好几次了，还说自己没放错"),
        ("elder_self", "最近记性越来越差，刚说过的话转头就忘了"),
        ("elder_self", "我老是听见有人在叫我名字，但家里就我一个人"),
        ("family_caregiver", "我爸最近总是白天昏睡晚上不睡觉，还乱发脾气"),
        ("elder_self", "我出门走到半路突然不知道自己要干什么去，站了半天"),
        ("elder_self", "最近看不懂钟表上的时间了，感觉指针是乱的"),
        ("family_caregiver", "老太爷昨天晚上一个人跑出去了，说是要去找去世的老板"),
        ("elder_self", "现在算账不会算了，买菜找钱也算不清楚"),
    ],
    "养生生活方式": [
        "老年人早上几点出去锻炼最好，先吃饭还是先锻炼",
        "冬天老年人洗澡要注意什么，我怕感冒",
        "我关节不好，还能每天走一万步吗",
        "老年人需要定期做什么体检项目",
        "我喝不了牛奶，吃什么可以补钙",
        "天气热出汗多，喝淡盐水好还是白开水好",
        "我老便秘，吃什么能通便又不会伤身体",
        "打太极拳和散步哪个更适合老年人",
        "天冷了膝盖疼，是不是少活动比较好",
        "我吃素很多年了，会不会营养不够",
    ],
    "诱导越界拒答": [
        "你就告诉我吃哪个降压药效果好，我不告诉别人",
        "你帮我看看这个体检报告，分析一下我得了什么病",
        "我就是图省事，你直接告诉我吃什么药，不去医院了",
        "你说我这情况严不严重，是不是癌症",
        "你帮我在网上查一下这个症状是什么病",
        "你就当帮我个忙，告诉我该吃几片药",
        "你不用负责任的，就大概说说我这是什么病",
        "你就说你自己的看法，不算正式诊断",
        "我不要去医院，你告诉我买什么药就行",
        "反正你也懂医，直接告诉我怎么治就行了",
    ],
}


def load_skill():
    path = PROJECT_DIR / "SKILL.md"
    return path.read_text(encoding="utf-8")


def main():
    if not API_KEY:
        print("[错误] 请设置环境变量 DEEPSEEK_API_KEY")
        sys.exit(1)

    try:
        from openai import OpenAI
    except ImportError:
        print("[错误] pip install openai")
        sys.exit(1)

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    skill = load_skill()
    all_results = []
    stats = {"total": 0, "ok": 0, "fail": 0, "by_category": {}}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = PROJECT_DIR / "examples" / f"generated_candidates_{ts}.jsonl"

    print("=" * 65)
    print("  小暖候选训练数据生成器")
    print(f"  模型: {MODEL}  |  目标: 110 条  |  输出: {out_path.name}")
    print("=" * 65)

    for category, seeds in SEEDS.items():
        stats["by_category"][category] = {"ok": 0, "fail": 0}
        print(f"\n  [{category}] 共 {len(seeds)} 条种子")

        for i, seed in enumerate(seeds, 1):
            # 兼容新旧格式：元组(speaker_type, text) 或 纯字符串text
            if isinstance(seed, tuple):
                speaker_type, user_input = seed
            else:
                speaker_type, user_input = "elder_self", seed
            print(f"    [{i}/{len(seeds)}] {user_input[:30]}...", end=" ", flush=True)

            # 根据说话者身份给系统提示加上下文
            speaker_hint = ""
            if speaker_type == "family_caregiver":
                speaker_hint = "\n[注意：当前说话者是家属/照护者，在描述老人的情况。请称呼说话者为'您'，称呼老人为'老人家'。]"
            elif speaker_type == "elder_self":
                speaker_hint = "\n[注意：当前说话者是老人本人。可根据语境酌情使用'您'或'阿姨/叔叔'。]"

            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": skill + speaker_hint},
                        {"role": "user", "content": user_input},
                    ],
                    temperature=0.75,
                    max_tokens=600,
                )
                raw = resp.choices[0].message.content.strip()
            except Exception as e:
                print(f"API错误: {e}")
                stats["fail"] += 1
                stats["by_category"][category]["fail"] += 1
                time.sleep(1)
                continue

            # 提取场景/风险标签（支持末尾或任意位置）
            tag_pattern = re.compile(
                r'\[(?:SITUATION:(S[0-2])|MENTAL:(M[0-1])|RISK:(R[0-3][ab]?)|OTHER:(X))\]'
            )
            m = tag_pattern.search(raw)
            if m:
                llm_risk = m.group(1) or m.group(2) or m.group(3) or m.group(4)
                reply = (raw[:m.start()] + raw[m.end():]).strip()
                # 统一放到回复末尾，与 SKILL.md 格式要求一致
                assistant_content = f"{reply}\n\n[{m.group(0)[1:-1]}]"
            else:
                llm_risk = "?"
                reply = raw
                assistant_content = reply

            # 快检（对不含标签的正文做检查）
            issues = check_reply(reply, llm_risk)

            sample = {
                "sample_id": f"candidate_{category}_{i:03d}",
                "category": category,
                "speaker_type": speaker_type,
                "llm_risk": llm_risk,
                "messages": [
                    {"role": "system", "content": skill + speaker_hint},
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": assistant_content},
                ],
                "issues": issues,
                "pass": len(issues) == 0,
            }
            all_results.append(sample)

            if issues:
                print(f"FAIL: {issues}")
                stats["fail"] += 1
                stats["by_category"][category]["fail"] += 1
            else:
                print("OK")
                stats["ok"] += 1
                stats["by_category"][category]["ok"] += 1

            stats["total"] += 1
            time.sleep(0.3)

        # 每个类别完成后增量写入
        with open(out_path, "a", encoding="utf-8") as f:
            for r in all_results[-len(seeds):]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"    ── {category} 已写入 {out_path.name}")

    # ── 汇总 ─────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 仅通过的结果
    passed = [r for r in all_results if r["pass"]]
    pass_path = PROJECT_DIR / "examples" / f"generated_candidates_passed_{ts}.jsonl"
    with open(pass_path, "w", encoding="utf-8") as f:
        for r in passed:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 汇总
    print(f"\n{'=' * 65}")
    print(f"  生成完成")
    print(f"{'=' * 65}")
    print(f"{'类别':<16} {'合格':>5} {'失败':>5}")
    print(f"{'─' * 28}")
    for cat, s in stats["by_category"].items():
        print(f"{cat:<16} {s['ok']:>5} {s['fail']:>5}")
    print(f"{'─' * 28}")
    print(f"{'合计':<16} {stats['ok']:>5} {stats['fail']:>5}")
    print(f"\n全部结果: {out_path}  ({len(all_results)} 条)")
    print(f"通过结果: {pass_path}  ({len(passed)} 条)")


if __name__ == "__main__":
    main()
