"""构建第三阶段新场景的手工种子锚点（v0.4.0_seed_anchors.jsonl）。

用途：expand 阶段的 few-shot anchors。新场景（S5/M3/L5/N4/F1-F4）在
v0.3.0_corpus500 里完全没有样本，直接放量会让模型在没有范例的情况下写
正文，质量与安全分寸都不可控。本脚本把组长人工编写的种子样本组装成
与训练语料一致的行格式（system 内嵌当前 SKILL.md 快照 + 说话人提示），
供 generate_corpus.py --anchors 与最终入库共用。

用法：
    python tools/build_seed_anchors.py            # 生成（覆盖）种子文件
    python tools/build_seed_anchors.py --check    # 只做质检不写文件

种子内容的要求与 SCENE_MUST / RISK_MUST 一致；改动正文后重跑本脚本即可。
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

REPO_ROOT = cc.REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.taxonomy import format_tags  # noqa: E402
from generate_corpus import build_system_prompt  # noqa: E402

OUT_PATH = cc.CORPUS_DIR / "v0.4.0_seed_anchors.jsonl"

# ── 种子样本（组长人工编写，改文案只动这里）─────────────────────────
# 字段：sample_id / scene 主场景 / scenes 全部标签 / risk / speaker_type /
#       user 老人或家属的原话 / assistant 小暖回复正文（不带标签、纯口语）

SEEDS = [
    {
        "sample_id": "seed_F1_R0_01",
        "scenes": ["F1"], "risk": "R0", "speaker_type": "family_caregiver",
        "user": "我爸中风以后右手不听使唤，现在得人喂饭，可他吃两口就呛，饭也洒得到处都是，我该怎么喂才顺一点？",
        "assistant": (
            "您别急，喂饭呛咳要先防着点。让他坐直、稍微前倾，饭后别马上躺下。"
            "勺子小一点，往他健侧嘴角送，一勺别太满。饭做得稠一些，太稀反而容易呛，"
            "真呛得厉害就把稀的换成软烂的干食。要是呛得直咳嗽、脸色发青，先停一停，"
            "缓过来再喂。要是呛得越来越频繁，或者发起了低烧，得带他去医院看看，别拖。"
            "平时慢慢练他自己用勺子，哪怕洒点也没关系。"
        ),
    },
    {
        "sample_id": "seed_F1_R1_01",
        "scenes": ["F1"], "risk": "R1", "speaker_type": "family_caregiver",
        "user": "我妈去年摔过一跤，现在腿脚更不利索了，我想把家里弄弄，从哪下手比较好？",
        "assistant": (
            "您有这个心很好，防跌倒的家确实值得好好弄。先抓三个地方：卫生间装扶手、"
            "铺防滑垫，这是最容易摔的地方；过道和床边留个夜灯，起夜不用摸黑走；"
            "把地上的电线、小凳子、散乱的地毯这些绊脚物都收起来。要是她起夜多，"
            "床边放个便桶椅更稳妥。椅子最好带扶手，方便她撑着起身。"
            "改完您陪她在家里走两圈试试，看哪儿还打绊。要是她最近站不稳比以前明显，"
            "建议带去医院做个检查，把腿脚的底子摸清楚。"
        ),
    },
    {
        "sample_id": "seed_F1_R2a_01",
        "scenes": ["F1", "S2"], "risk": "R2a", "speaker_type": "family_caregiver",
        "user": "社区医院的护士说我爸在家插着尿管，我看快到一个月了，我能自己在家给他拔了换吗？",
        "assistant": (
            "这个千万别自己动手，换尿管是无菌操作，在家自己做很容易引起尿路感染，"
            "严重的还会损伤尿道，这个险冒不得。您先打社区医院的电话问问，"
            "很多社区卫生服务中心能约护士上门换管，一般四周到一个月换一次，"
            "正好到时间了。要是约不上，就带他去医院的门诊换。这几天多留意，"
            "要是他尿浑了、发烧了、说尿道疼，那就别等换管的日子了，赶紧去医院。"
            "去的时候把医保卡和住院小结一起带上。"
        ),
    },
    {
        "sample_id": "seed_F2_R0_01",
        "scenes": ["F2"], "risk": "R0", "speaker_type": "family_caregiver",
        "user": "我家老爷子整天躺着不想动，我就想问问他这个岁数每天躺着行不行？",
        "assistant": (
            "长期躺着真不行，越躺越没劲，肌肉掉得快，还容易生褥疮、便秘，"
            "情绪也会越来越蔫。您不用给他安排大运动，先从床上活动开始："
            "让他自己翻翻身、抬抬腿，一天分几组做；坐着的时候帮他揉揉小腿，"
            "能动就能慢慢练。要是他自己不肯动，您陪他坐到床边晃晃腿，"
            "跟他唠着嗑就把动作做了。要是他这几天腿肿了、一动就喘，"
            "那就先别硬练，带去医院看看再说。"
        ),
    },
    {
        "sample_id": "seed_F2_R1_01",
        "scenes": ["F2"], "risk": "R1", "speaker_type": "family_caregiver",
        "user": "我妈今年79，这两天老说头晕，晕起来天旋地转的，缓一会儿又好点，我要不要带她去医院？",
        "assistant": (
            "您带她去医院看看更稳妥，79岁反复头晕不敢大意，建议挂神经内科，"
            "查清楚晕的缘由。去之前您帮她记一记：晕起来是天旋地转还是头昏沉？"
            "每次持续几分钟还是几小时？什么姿势容易犯？有没有耳鸣、听不清？"
            "这些都记下来给医生，看病能少走弯路。要是晕的时候站都站不住、"
            "说话不清楚、一边手脚没劲，那就别等了，马上送急诊。"
            "路上让她坐着，起卧都慢一点，别摔着。"
        ),
    },
    {
        "sample_id": "seed_F2_R2b_01",
        "scenes": ["F2", "E1"], "risk": "R2b", "speaker_type": "family_caregiver",
        "user": "我爸刚才说话突然大舌头，右边胳膊也抬不起来，过了几分钟缓过来一点了，我想着先观察一晚上行吗？",
        "assistant": (
            "不行，一晚上都不能等。您爸这个是典型的脑缺血发作，哪怕自己缓过来了，"
            "接下来两天里再发作甚至发展成脑梗的风险很高，必须今天就去医院，"
            "挂急诊，跟医生说清楚刚才发作的时间和表现。现在就动身，让家里别离人，"
            "路上让他坐着别乱动，带上他平时吃的药和病历。要是路上再犯，"
            "说话更含糊、一边脸垮下来、半边身子抬不动，直接打 120，"
            "千万别自己开车硬撑。"
        ),
    },
    {
        "sample_id": "seed_F3_R0_01",
        "scenes": ["F3"], "risk": "R0", "speaker_type": "family_caregiver",
        "user": "最近为了照顾我爸的事跟姐吵了好几回，心里挺堵的，跟您念叨念叨。",
        "assistant": (
            "跟您念叨念叨挺好，憋着才难受。长期照顾的事，兄弟姐妹吵起来太常见了，"
            "多半不是为照顾本身，是累和委屈堆出来的。您俩找个都心平气和的时候，"
            "把照顾的事一项一项列出来——谁管钱、谁出力、谁盯着看病，"
            "写明白比互相埋怨强。您自己也留点喘气的空，哪怕每天出门遛个弯。"
            "心里堵得慌就随时来跟我说说，我一直在。"
        ),
    },
    {
        "sample_id": "seed_F3_R1_01",
        "scenes": ["F3"], "risk": "R1", "speaker_type": "family_caregiver",
        "user": "我照顾我妈三年了，她夜里老闹腾，我已经快两年没睡过整觉了，有时候真想摔门走人，我是不是特别不孝？",
        "assistant": (
            "您不是不孝，撑了三年还撑着，已经非常不容易了，这些念头谁长期照顾都会有，"
            "别再拿这个责备自己。您得先把自己顾上：跟兄弟姐妹或老伴排个班，"
            "一周哪怕让您完整睡两晚；社区要是能申请喘息服务或短托，就把她送几天，"
            "您歇口气。夜里她闹，可能是身体不舒服，带去医院查查是不是疼痛或憋尿，"
            "别都硬扛。要是您觉得情绪已经快到极限了，也可以打心理援助热线 "
            "400-161-9995 聊聊，这不丢人。"
        ),
    },
    {
        "sample_id": "seed_F4_R0_01",
        "scenes": ["F4"], "risk": "R0", "speaker_type": "family_caregiver",
        "user": "闺女让我自己学着挂号，我想带老伴去市医院看高血压复查，手机上怎么操作？",
        "assistant": (
            "您这么学很好，学会了以后复诊不求人。您照着来：先让孩子帮您在"
            "医院公众号上用老伴的身份证注册好，绑定就诊卡；然后打开微信，"
            "搜「市人民医院」的公众号，点「预约挂号」，选「心血管内科」，"
            "普通号就行，专家号不好抢。选好日期点确认，用医保卡或手机付款。"
            "去的那天带上医保卡、身份证和平时吃的降压药，提前半小时到自助机取号。"
            "要是手机上弄不明白，别着急，让窗口的工作人员帮您点，都不难。"
        ),
    },
    {
        "sample_id": "seed_F4_R1_01",
        "scenes": ["F4"], "risk": "R1", "speaker_type": "family_caregiver",
        "user": "我爸上个月住院花了小两万，医保能报多少？报销的手续麻烦吗？",
        "assistant": (
            "能报的，您别嫌手续麻烦，一趟就能办利索。出院结算时要是用了医保，"
            "能报的部分医院直接就扣掉了，不用您再跑；自付的部分要是符合规定还能再报，"
            "比如他有慢性病门诊或者商业保险，材料备齐：出院小结、费用清单、发票原件、"
            "医保卡和银行卡。拿不准的话，打医院医保办的电话或者市医保局的咨询电话，"
            "把住院时间和金额说清楚，人家会告诉您能报哪些。要留意出院后报销一般有时限，"
            "别放太久。办的时候把材料复印一份留底，心里踏实。"
        ),
    },
    {
        "sample_id": "seed_S5_R1_01",
        "scenes": ["S5"], "risk": "R1", "speaker_type": "elder_self",
        "user": "姑娘，我体检查出来尿蛋白两个加号，护士说让复查，这个事大吗？我心里直打鼓。",
        "assistant": (
            "您先别自己吓自己，两个加号说明尿里蛋白偏多了，可能是肾脏的问题，"
            "也可能是体检那天累着、发烧或者运动过，一次结果说明不了定论。"
            "不过这个确实要重视，别拖，按护士说的尽快去复查个尿常规，"
            "最好再查个肾功能和血压，带上这次的体检报告，挂肾内科。"
            "复查前一两天别剧烈运动，正常喝水，早晨的尿最准。"
            "要是复查还有加号，就听医生安排接着查。这几天留意尿里泡沫多不多、"
            "腿肿不肿，有就记下来告诉医生。"
        ),
    },
    {
        "sample_id": "seed_M3_R1_01",
        "scenes": ["M3"], "risk": "R1", "speaker_type": "elder_self",
        "user": "我今年七十了，最近老忘事，东西放哪儿转身就找不着，昨天还把烧着水的事忘了，我是不是老年痴呆了？",
        "assistant": (
            "您先别急着给自己下「痴呆」的结论，这个年纪忘事很多人都有，"
            "尤其操心的事多、睡得不好的时候。不过您说的烧水忘关这件事得当心，"
            "也正因为它有危险，值得早点查清楚。建议您去医院的记忆门诊或神经内科"
            "做个记忆评估，带上平时吃的药，最好让家里人陪着去，"
            "把您忘事的具体例子讲给医生听。查清楚是啥原因，该调理调理，心里也踏实。"
            "在那之前，燃气灶用完就关阀，手机定个提醒闹钟，先把安全兜住。"
        ),
    },
    {
        "sample_id": "seed_M3_R1_02",
        "scenes": ["M3", "F2"], "risk": "R1", "speaker_type": "family_caregiver",
        "user": "我爸75，最近老重复问同一个问题，说过的转身就忘，还把存折藏起来自己都找不着，我是不是该带他去看看？",
        "assistant": (
            "该带去看了，您观察得也细，这些表现值得认真查一查，"
            "挂神经内科或记忆门诊都行。去看之前您帮他准备三样：平时吃的药清单、"
            "血压血糖的情况、还有像重复问话、藏东西找不到这样让您担心的具体例子，"
            "什么时候开始的、越来越重还是时好时坏，记下来给医生，"
            "比现场问他自己管用。检查时您陪着，帮着补充。"
            "先别在他跟前说「痴呆」这类词，他自己听了会慌。"
            "家里这段时间把存折、房本这些重要的东西替他理好收好，"
            "燃气和门锁多留个心。"
        ),
    },
    {
        "sample_id": "seed_L5_R0_01",
        "scenes": ["L5"], "risk": "R0", "speaker_type": "elder_self",
        "user": "闺女给我买了个智能手表，说是能看心率，我鼓捣半天没弄明白，您能教教我不？",
        "assistant": (
            "能教您，这表不难，咱们一步步来。先看表盘上有没有一颗爱心的图案，"
            "手指点它，屏幕上就会跳出一个数字，那就是您现在一分钟的心跳数，"
            "一般安静时候在60到100之间都算正常。要是表盘上没有，"
            "您按侧面的那个按钮，来回翻一翻，找到带爱心的那页。"
            "想看更细的，让闺女帮您在手机上装好配套的应用，跟手表连上，"
            "里头有每天的心率曲线，字还能调大。您要是哪一步卡住了，"
            "随时来问我，我再给您慢慢讲。"
        ),
    },
    {
        "sample_id": "seed_N4_R1_01",
        "scenes": ["N4"], "risk": "R1", "speaker_type": "family_caregiver",
        "user": "我妈82了，上礼拜出门买菜差点找不着回家的路，多亏邻居送回来，我怕她哪天真丢了，该怎么办？",
        "assistant": (
            "您先别慌，这事能提前防。几样东西都做上：给她兜里、衣服领口各缝一张联系卡，"
            "写上您的电话和住址，字写大点；有条件配一个防走失定位手环，"
            "您手机上能看到她在哪儿；再给她办一张带照片的身份证明随身带着。"
            "家里这边，把常用路线带她多走几遍，认认楼栋和标志物，"
            "比如门口的超市和小广场。她出门尽量挑人多的时间段，"
            "跟邻里打好招呼互相照应着点。要是她最近忘事明显加重了，"
            "建议带去医院看看，走失有时候跟记性变差有关系。"
        ),
    },
    {
        "sample_id": "seed_N4_R2b_01",
        "scenes": ["N4"], "risk": "R2b", "speaker_type": "family_caregiver",
        "user": "我爷爷下午三点出门到现在人还没回来，电话也没人接，天都快黑了，急死我了，我该怎么办？",
        "assistant": (
            "您现在就行动，别再等了：先打110报警，跟警察说清楚爷爷的年龄、"
            "穿的什么衣服、最后从哪儿出去的、有没有认知方面的问题；"
            "同时让家里人分头去他常去的地方找——老小区门口、公园、菜市场、"
            "以前的单位附近，都问一圈。天黑降温，老人在外面时间长容易出事，"
            "报警不丢人，越早越好。您把他近期的照片准备好，方便大家辨认。"
            "找到以后别埋怨他，慢慢问问他去了哪儿。"
            "要是他平时有忘事、犯糊涂的情况，这事之后记得带他去查一查。"
        ),
    },
]


def build_rows(skill_md: str) -> list:
    rows = []
    for seed in SEEDS:
        speaker = seed["speaker_type"]
        category = cc.SCENE_LABELS.get(seed["scenes"][0], seed["scenes"][0])
        # 与 corpus500 格式一致：正文后追加双维度标记块（SKILL.md 第 13 节）
        assistant = seed["assistant"] + format_tags(seed["risk"], seed["scenes"])
        rows.append({
            "sample_id": seed["sample_id"],
            "category": category,
            "speaker_type": speaker,
            "risk_level": seed["risk"],
            "scenes": seed["scenes"],
            "messages": [
                {"role": "system", "content": build_system_prompt(skill_md, speaker)},
                {"role": "user", "content": seed["user"]},
                {"role": "assistant", "content": assistant},
            ],
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="构建新场景手工种子锚点")
    parser.add_argument("--check", action="store_true", help="只质检不写文件")
    args = parser.parse_args()

    skill_md = cc.SKILL_MD.read_text(encoding="utf-8")
    rows = build_rows(skill_md)

    bad = []
    for row in rows:
        scene = row["scenes"][0]
        reply = row["messages"][2]["content"]
        if not cc.length_ok(reply, row["risk_level"], scene, tolerance=0.25):
            lo, hi = cc.length_range(row["risk_level"], scene)
            bad.append(f"{row['sample_id']}: {len(reply)} 字，规范区间 {lo}-{hi}")
    if bad:
        print("[篇幅异常]")
        print("\n".join(bad))
        sys.exit(1)

    print(f"种子 {len(rows)} 条，篇幅检查全部通过")
    if args.check:
        return
    cc.write_jsonl(OUT_PATH, rows)
    print(f"已写入 {OUT_PATH.relative_to(cc.REPO_ROOT)}")


if __name__ == "__main__":
    main()
