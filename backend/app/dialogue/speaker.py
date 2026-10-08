"""说话人角色识别（Day0 契约，签名冻结）。

┌─ 解决什么问题 ───────────────────────────────────────────────────────┐
│ 现状：系统默认「说话人一定是老人」，遇到家属提问（"我该怎么照顾老人"）│
│       会输出很不合理的话（把家属当老人安抚、称呼也不对）。             │
│ 目标：对输入做一次**轻量**判断，区分「老人本人 / 家属 / 未知」，        │
│       据此调整称呼与内容侧重。                                        │
└──────────────────────────────────────────────────────────────────────┘

实现（启发式打分，零 LLM 调用）：
  0) 引号剥离：先剔除引号/书名号内的内容再打分。"我妈说“我不吃药”"里的
     "我"指老人本人，不算说话人的自述——引内内容属于被转述者。
  1) 家属信号（+2 分/项）：长辈亲属称谓（我爸/我母亲/爷爷…，含方言
     我爹/我娘/姥爷/姥姥/阿婆/我爷）、泛指家中老人（我家老人/老爷子…）、
     照护动词 + 老人/第三人称。
  2) 老人信号（+2 分/项）：我 + 症状/用药（带亲属字屏蔽，"我爸不肯吃药"
     不计老人分）、向下亲属（我儿子/我女儿/我老伴）、独居自述。
  3) 弱家属信号（+1 分）：第三人称 + 症状（"她血糖高"），仅在近期
     历史**最近一条明确信号**为家属时才升级为 family。
  4) 判定：family_score >= 2 且高于 elder_score 才判家属；
     平局、不足阈值、空输入一律回退 SPEAKER_ELDER（宁可不切，不可误切）。

历史延续取"最近一条明确信号"而非"窗口内存在家属信号"：双人对话里老人会
接过话头（"我爸血压高" 之后老人说 "我头晕"），"存在即升级"会把老人误判成
家属——那是本模块唯一会伤害体验的方向（系统改口称"您可以怎么照顾老人"）。

安全不变式（任何角色下都成立）：
  用药 / 急症 / 心理危机 / 人身安全红线**不因说话人是谁而改变**。
  家属问"能不能给老人加药"与老人自己问，红线判定必须一致。
  本模块只输出角色，不参与风险判定；角色仅影响称呼与内容侧重提示。
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# 角色枚举（对外契约，写入 /api/chat 的 speaker_role 字段）
SPEAKER_ELDER = "elder"  # 老人本人
SPEAKER_FAMILY = "family"  # 家属 / 照护者
SPEAKER_UNKNOWN = "unknown"  # 无法判断

VALID_SPEAKER_ROLES = (SPEAKER_ELDER, SPEAKER_FAMILY, SPEAKER_UNKNOWN)

# 各角色的"内容侧重"提示（供 prompt.py 注入；组长维护）
ROLE_HINTS = {
    SPEAKER_ELDER: "",
    SPEAKER_FAMILY: (
        "【当前说话人：家属/照护者】对方是在替家中老人咨询。称呼用“您”，"
        "侧重“您可以怎么照顾/观察/陪伴老人”，不要对家属本人做身体不适的安抚；"
        "涉及用药、急症、心理危机的安全红线与对老人本人一致，不得放松。"
    ),
    SPEAKER_UNKNOWN: "",
}

# ── 家属信号 ────────────────────────────────────────────────────────

# 长辈亲属称谓（含可选前缀）：我爸 / 我家母亲 / 爷爷 / 外婆 / 岳父 / 公婆…
_KINSHIP_RE = re.compile(
    r"(?:我家?|家里|家中|替|我)?(?:老)?(?:爸|妈|父亲|母亲|爷爷|奶奶|外公|外婆|岳父|岳母|公公|婆婆)"
)
# 方言 / 口语称谓。**不通用的词必须带人称前缀**，否则会误命中非亲属词
# （"姑娘""大爷""老娘我"）。例：我爹 / 俺娘 / 我老娘 / 我爷 / 我姥爷 / 我阿婆。
# 「老娘」同理只在带前缀时算（"老娘我…"是老人自称，不算家属信号）。
_DIALECT_KINSHIP_RE = re.compile(
    r"(?:我|俺|你|咱|他|她|家里|家中)(?:老)?(?:爹|娘|爷)"
    r"|(?:我|俺|你|咱|他|她)?(?:姥爷|姥姥|姥娘|阿婆)"
)
# 泛指家中老人（第三人称）：我家老人 / 家里老人 / 老爷子 / 老太太 / 老人家
_ELDER_REF_RE = re.compile(r"(?:我家|家里|家中|他家|她家)?(?:老人|老人家|老爷子|老太太)")
# 照护动词 + 被照护对象：照顾老人 / 护理他 / 照料奶奶…
_CARE_VERB_RE = re.compile(
    r"(?:照顾|护理|照料|陪护|照看|看护|伺候|照护)[^，。！？]{0,8}"
    r"(?:老人|老人家|老爷子|老太太|他|她|爸|妈|父亲|母亲|爷爷|奶奶|外公|外婆)"
)
# 弱信号：第三人称代词 + 症状/用药（"她血糖高"）
_PRONOUN_SYM_RE = re.compile(
    r"(?:他|她)(?:[^，。！？]{0,6})(?:不肯吃药|不肯就医|不肯吃|摔|忘事|健忘|糊涂|血压|血糖|吃药|头晕|难受|睡不着|吃不下|瘦|情绪|心情|说胡话|走失|摔了一跤|腿疼|腰疼|咳嗽|发烧|恶心|呕吐|手抖|发麻)"
)

# ── 老人信号 ────────────────────────────────────────────────────────

# 症状 / 用药 / 身体自述词（老人视角常见）
_SYMPTOMS = (
    "头晕|头疼|头痛|心慌|胸闷|气短|喘不上气|乏力|没力气|没劲|恶心|想吐|呕吐|"
    "耳鸣|眼花|看不清|眼睛干|腿疼|腿痛|腰疼|腰痛|膝盖疼|关节疼|手抖|发麻|麻木|"
    "水肿|睡不着|睡不好|失眠|多梦|起夜|便秘|拉肚子|肚子疼|肚子痛|咳嗽|发烧|"
    "发热|发冷|出虚汗|盗汗|心悸|心口疼|胸口疼|血压|血糖|胃口|吃不下|没胃口|"
    "瘦了|体重|晕倒|摔了一跤|摔跤|忘事|健忘|记性|糊涂|难受|不舒服|不对劲|"
    "心烦|犯困|没精神|浑身疼|身上疼|腿脚不利索|走路喘|呛咳|说胡话|发慌|"
    "降压药|降糖药|止痛药|安眠药|吃药|服药|停药|加药|量血压|量血糖|化验单|体检"
)
# 引号 / 书名号内的内容：属于被转述者，打分前先剔除（见模块文档第 0 条）
_QUOTED_RE = re.compile(r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|\"[^\"]*\"")

# 亲属 / 第三人称字：紧跟在"我"之后出现时，说明"我"修饰的是亲属而不是说话人
# 自己（"我爸不肯吃药""我爷爷头晕""我岳母腿疼"都不算老人自述）。
# 必须覆盖 _KINSHIP_RE 与 _DIALECT_KINSHIP_RE 里的全部单字，否则新增的方言
# 称谓会被反向读成老人自述（"我爹不肯吃药"曾因此判成老人）。
_KIN_CHARS = "爸妈父母亲爷奶爹娘姥阿婆公外岳"

# "我 + 症状"：中间最多 4 个非标点字符，且后面 5 个字符内不得出现亲属/第三人称字，
# 避免"我爸不肯吃药""我爷爷头晕"误计为老人自述；
# "我家…"开头的自述同样不计分（"我家老人摔了一跤"指向家属称谓）。
_SELF_SYM_RE = re.compile(
    r"我(?!家)(?![^，。！？]{0,5}[" + _KIN_CHARS + r"孙子女他她它])"
    r"[^，。！？]{0,4}(?:" + _SYMPTOMS + r")"
)
# 单字不适自述兜底："我腿也疼""我腰也酸""我手也麻"。
# 复合症状词（腿疼/腰疼）被"也/还/又"隔开时固定词表必然漏，而漏掉之后
# 历史判定会一路回退到更早的家属信号，把老人误判成家属——那是唯一伤害
# 体验的方向，所以这里宁可宽一点。
_SELF_HURT_RE = re.compile(
    r"我(?!家)(?![^，。！？]{0,5}[" + _KIN_CHARS + r"孙子女他她它])"
    r"[^，。！？]{0,4}(?:疼|痛|酸|麻|晕|胀|咳|喘|痒|木)"
)
# 向下亲属 / 配偶（说话人辈分更高）：我儿子 / 我女儿 / 我老伴 / 我家孩子
_DOWNKIN_RE = re.compile(r"(?:我|我家)(?:老伴|儿子|女儿|孩子|孙子|孙女|外孙|媳妇|女婿)")
# 老人自述状态："我老了"前面不能是亲属字（"我妈老了"是家属在说母亲）
_SELF_OLD_RE = re.compile(r"(?<=[我自])老了|我一个人住|我这把年纪|我年纪大了|我岁数大了")

# 判为家属的最低分差：家属分至少 2（一个明确信号）且严格高于老人分
_FAMILY_THRESHOLD = 2

# 历史确认时最多回看的最近用户消息条数
_HISTORY_WINDOW = 5


def _score(text: str) -> tuple[int, int]:
    """对单条文本打分，返回 (家属分, 老人分)。"""
    text = text or ""
    # 引内内容属于被转述者，先剔除再打分（见模块文档第 0 条）
    spoken = _QUOTED_RE.sub("", text)

    family = 0
    if _KINSHIP_RE.search(spoken) or _DIALECT_KINSHIP_RE.search(spoken):
        family += 2
    if _ELDER_REF_RE.search(spoken):
        family += 2
    if _CARE_VERB_RE.search(spoken):
        family += 2
    if _PRONOUN_SYM_RE.search(spoken):
        family += 1

    elder = 0
    if _SELF_SYM_RE.search(spoken) or _SELF_HURT_RE.search(spoken):
        elder += 2
    if _DOWNKIN_RE.search(spoken):
        elder += 2
    if _SELF_OLD_RE.search(spoken):
        elder += 2
    return family, elder


def _history_last_signal(history) -> Optional[str]:
    """近期历史里**最近一条明确信号**的角色；没有明确信号返回 None。

    明确信号 = 家属分或老人分达到 _FAMILY_THRESHOLD（一条实打实的分）；
    弱信号（+1 分的第三人称 + 症状）不算。

    为什么取"最近一条"而不是"窗口内存在家属信号"：双人对话里老人会接过
    话头。["我爸血压高", "我头晕"] 两条都在 5 条窗口内，但最近一条是老人
    自述，应当判老人；按"存在即升级"会把每一句都判成家属——那是本模块
    唯一会伤害老人体验的方向。
    """
    messages = [item for item in (history or []) if isinstance(item, str) and item]
    for msg in reversed(messages[-_HISTORY_WINDOW:]):
        family, elder = _score(msg)
        if family >= _FAMILY_THRESHOLD or elder >= _FAMILY_THRESHOLD:
            # 同一句里两边都够分时按平局处理 -> 老人（与当前句的平局规则一致）
            return SPEAKER_FAMILY if family > elder else SPEAKER_ELDER
    return None


def detect_speaker_role(text: str, history: Optional[Iterable[str]] = None) -> str:
    """判断说话人角色。

    参数：
        text    —— 当前用户输入（必填）
        history —— 可选的历史用户消息（字符串可迭代），用于多轮延续判定

    返回：SPEAKER_ELDER / SPEAKER_FAMILY（低置信一律回退 elder）。

    判定规则：
        - 家属分 >= 2 且严格高于老人分 -> 家属；
        - 弱家属信号（或无信号）但当前无老人信号、且近期历史**最近一条明确
          信号**是家属 -> 家属（多轮延续，如先说"我爸80岁了"再问"他总忘事
          怎么办"）；老人中途接过话头则随之切回老人；
        - 其余（平局、不足阈值、空输入、异常）-> 老人（＝现状）。
    """
    try:
        family, elder = _score(text)
        if family >= _FAMILY_THRESHOLD and family > elder:
            return SPEAKER_FAMILY
        history_items = [item for item in (history or []) if isinstance(item, str)]
        if elder == 0 and _history_last_signal(history_items) == SPEAKER_FAMILY:
            return SPEAKER_FAMILY
    except Exception:  # 识别失败不阻断对话，维持现状
        return SPEAKER_ELDER
    return SPEAKER_ELDER


def role_hint(role: str) -> str:
    """角色 -> 注入 system prompt 的提示片段（未知/老人返回空串）。"""
    return ROLE_HINTS.get(role, "")


__all__ = [
    "SPEAKER_ELDER",
    "SPEAKER_FAMILY",
    "SPEAKER_UNKNOWN",
    "VALID_SPEAKER_ROLES",
    "ROLE_HINTS",
    "detect_speaker_role",
    "role_hint",
]
