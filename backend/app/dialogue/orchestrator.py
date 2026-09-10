"""对话编排（骨架）。

计划流程：
    用户输入
      -> 载入 skill 作为 system prompt
      -> 调用 LLM 生成回复（要求末尾带 [RISK:Rx] / [SITUATION:Sx] / [MENTAL:Mx] / [OTHER:X]）
      -> 解析并剥离场景标记
      -> safety_checker.check_reply 兜底快检
      -> 输出 (回复正文, 风险等级)

尚未实现。
"""
