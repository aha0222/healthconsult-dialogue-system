"""修正 v0.3.0 语料中 5 条「近期跌倒 / 活动后气促」样本的风险定级。

复核依据（老年医学常见判断）：
- 独居、近期跌倒且遗留行动不便者，再跌倒风险高，应至少 R1（提示风险、建议评估），
  而非 R0（无需特殊处置）。涉及 L1 饮食、L4 社交四条。
- 七十岁老人「走一会儿就喘」是心脏/肺问题的常见信号，也应至少 R1。

本脚本把对应 risk_level 由 R0 改为 R1，并同步为回复补上「建议专业评估」的话术与
末尾 `[RISK:R1]` 标签；不动用户输入与其它 493 条。幂等，可重复执行。

用法：
    python tools/fix_fall_risk_labels.py            # 预演，只打印将修改的条目
    python tools/fix_fall_risk_labels.py --apply    # 写回语料
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import DEFAULT_EXEMPLAR_CORPUS_PATH

CORRECTIONS = {
    "exp_L1_R0_b0_02": (
        "您这份心老人能感受到。腿脚恢复，饭桌上重点抓两样：蛋白质和钙。每天给她安排一个鸡蛋、"
        "一袋牛奶，再加一两左右的瘦肉或者鱼，豆腐也行，这些帮着长肌肉、强骨头。绿叶菜每天半斤，"
        "焯水后凉拌或清炒，钙和维生素都够。主食别光吃白米饭，掺点小米、燕麦，防便秘。喝水量别少，"
        "一天1500毫升左右，分次喝。\n\n"
        "另外，她前阵子摔过、腿脚又不利索，属于容易再摔跤的情况，建议带她去社区医院或骨科做一次"
        "跌倒风险和骨密度评估，家里地面防滑、该装扶手就装，起身转身都慢一点。要是胃口差、吃得少，"
        "或者腿肿、疼得厉害，别拖着，尽早带她去医院。\n\n[RISK:R1]\n[SCENE:L1]"
    ),
    "exp_L1_R0_b2_03": (
        "一个人住又腿脚不便，吃饭这事确实得想个省心的办法。\n\n"
        "给您两个实在建议。一是让子女或者社区帮忙，一周集中买一次菜，鸡蛋、豆腐、冻鱼、牛奶这些"
        "好存好做，放冰箱里慢慢吃。二是每天保证一个鸡蛋、一杯牛奶、一块巴掌大的豆腐或鱼肉，"
        "这三样凑齐了，蛋白质就差不离了，腿脚才有劲。\n\n"
        "青菜不好天天买，可以备点干香菇、木耳、紫菜，泡一泡就能下锅。主食别光吃白米饭，蒸点南瓜、"
        "红薯一起，省事还顶饱。\n\n"
        "要是实在做不动饭，社区一般有老年助餐点，或者订份老年餐，别嫌麻烦，吃好比啥都强。您一个人住、"
        "又刚摔过，安全上更要多留神：地上别留水，卫生间床边装上扶手，走路拄个拐更稳。方便的话去社区"
        "医院做个跌倒风险评估，看看要不要康复锻炼。\n\n[RISK:R1]\n[SCENE:L1]"
    ),
    "exp_L1_R0_b4_02": (
        "摔过一跤的老人，肉可得吃够，不然腿上没劲，容易再摔。牙口不好就换个做法，把肉剁成末蒸蛋、"
        "炖成烂糊的肉粥都行。\n\n"
        "给您个实在数：一天差不多一两半到二两，大概一个鸡蛋那么大，鱼肉、鸡肉、瘦猪肉轮着来。"
        "再配一个鸡蛋、一杯牛奶，蛋白质就够了。青菜煮软点，一天半斤。\n\n"
        "另外，她摔过一跤，建议顺便带她去查一下骨密度、做个跌倒风险评估，家里做好防滑，起夜开个小灯。"
        "要是嚼着费劲、咽不下去，或者体重一个月掉了好几斤，就去营养科看看，别硬扛着。\n\n"
        "[RISK:R1]\n[SCENE:L1]"
    ),
    "exp_L2_R0_b3_01": (
        "您这份小心是对的，一个人出门，稳当最要紧。不过走一会儿就喘，在您这个年纪不一定只是缺锻炼，"
        "也可能是心脏或肺的事，得先查清楚。建议您先去医院查个心电图、必要时查肺功能，大夫说能走，"
        "咱们再按量走，更稳妥。\n\n"
        "平时可以在小区里绕着楼走，一回十五分钟，步速就是能跟人说话不喘得厉害那种，手边拄个拐棍或"
        "推个小推车。走之前扶墙踮踮脚、转转脚踝，活动开了再迈步；走着要是胸口发闷、腿发软，就赶紧"
        "坐下歇着，别硬撑。回头让闺女陪您走两回，心里更有底。\n\n[RISK:R1]\n[SCENE:L2]"
    ),
    "exp_L4_R0_b2_01": (
        "一个人在家确实容易闷得慌，您这腿脚不便，咱们挑些省劲儿又热闹的活动挺好。社区活动中心一般"
        "有书法班、手工编织组，坐着就能参加，还能跟人唠唠嗑。楼下的棋牌角、合唱队也可以搬个凳子坐"
        "旁边听，慢慢熟了自然就搭上话了。要是出门不方便，可以跟社区工作人员说一声，有些地方会上门接、"
        "或者组织楼里的老伙伴在楼道口聚聚。\n\n"
        "您拄拐出门，安全得放在心上：尽量选离得近、地面平的活动场地，最好结伴、带上手机；摔过之后"
        "一直拄拐，建议去医院做个骨密度和康复评估，看能不能通过训练恢复些。您先去看看热闹，不用急着"
        "加入，觉得哪个合心意再说。您平时是喜欢安静点的，还是喜欢有人聊天的？\n\n[RISK:R1]\n[SCENE:L4]"
    ),
}


def apply_corrections(path, apply=False):
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"找不到语料文件：{path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = []
    output = []
    for line in lines:
        if not line.strip():
            output.append(line)
            continue
        row = json.loads(line)
        sid = row.get("sample_id")
        if sid not in CORRECTIONS:
            output.append(line)
            continue
        new_reply = CORRECTIONS[sid]
        messages = row.get("messages", [])
        old_reply = None
        for message in messages:
            if message.get("role") == "assistant":
                old_reply = message.get("content")
                message["content"] = new_reply
                break
        row["risk_level"] = "R1"
        row["human_edited"] = True
        changed.append(sid)
        output.append(json.dumps(row, ensure_ascii=False))
    if apply and changed:
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="修正 5 条跌倒/气促样本的 R0→R1 定级")
    parser.add_argument("--input", default=str(DEFAULT_EXEMPLAR_CORPUS_PATH))
    parser.add_argument("--apply", action="store_true", help="写回文件；缺省只预演")
    args = parser.parse_args()

    changed = apply_corrections(args.input, apply=args.apply)
    print(f"{'已写回' if args.apply else '将修改'} {len(changed)} 条：{changed}")
    if not args.apply:
        print("（预演模式，未写入；加 --apply 生效）")


if __name__ == "__main__":
    main()
