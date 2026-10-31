"""Topology choices for a description too short to say which variant.

"五电平逆变器" can be a cascaded H-bridge, an NPC or a flying-capacitor
inverter; the model would pick one silently.  These choices are shown at
once, without a model call, and each turns the description into one
explicit sentence.  Nothing is offered once the description names a variant
or is longer than a short phrase.
"""

from __future__ import annotations

import re

CN_NUM = {"三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "九": 9}
SHORT = 30          # characters; longer descriptions already say enough


def _levels(text: str) -> int | None:
    m = re.search(r"([三四五六七九]|\d+)\s*电平|(\d+)[- ]?level", text, re.I)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    return CN_NUM.get(raw) or int(raw)


def _multilevel(n: int) -> list[dict]:
    cn = {v: k for k, v in CN_NUM.items()}.get(n, str(n))
    out = []
    if n % 2 and n > 3:
        cells = (n - 1) // 2
        out.append({"label": "级联 H 桥",
                    "text": f"单相{cn}电平级联 H 桥逆变器，{cells} 个 H 桥单元，{cells} 个独立等压"
                            f"直流源，{4 * cells} 个带反并联二极管的 IGBT，阻性负载。"})
    out.append({"label": "二极管钳位（NPC）",
                "text": f"单相{cn}电平二极管钳位（NPC）半桥逆变器，直流侧 {n - 1} 个串联等值电容，"
                        f"{2 * (n - 1)} 个带反并联二极管的 IGBT，钳位二极管按标准画法，"
                        f"负载接桥臂中点与直流中点。"})
    out.append({"label": "飞跨电容",
                "text": f"单相{cn}电平飞跨电容半桥逆变器，{2 * (n - 1)} 个带反并联二极管的 IGBT，"
                        f"{n - 2} 级飞跨电容，直流侧两个串联电容，负载接桥臂中点与直流中点。"})
    if n == 3:
        out.append({"label": "T 型",
                    "text": "单相三电平 T 型半桥逆变器，直流侧两个串联电容，上下管各一个 IGBT，"
                            "中点双向开关由两个共射极 IGBT 组成，负载接桥臂中点与直流中点。"})
    return out


FAMILIES = [
    # (applies, already specific, question, options)
    (re.compile(r"谐振"), re.compile(r"llc|lcc|串联谐振|并联谐振|src|prc", re.I),
     "哪种谐振变换器？",
     [{"label": "LLC 全桥", "text": "全桥 LLC 谐振变换器，4 个 MOSFET，谐振电感 Lr、谐振电容 Cr，"
                                    "励磁电感由变压器体现，中心抽头副边全波二极管整流，输出电容和负载。"},
      {"label": "LLC 半桥", "text": "半桥 LLC 谐振变换器，2 个 MOSFET，谐振电感 Lr、谐振电容 Cr，"
                                    "中心抽头副边全波二极管整流，输出电容和负载。"},
      {"label": "LCC", "text": "全桥 LCC 谐振变换器，4 个 MOSFET，串联 Lr、Cs，变压器原边并联 Cp，"
                               "副边全桥二极管整流，输出电容和负载。"},
      {"label": "串联谐振", "text": "全桥串联谐振变换器，4 个 MOSFET，串联 Lr、Cr，"
                                    "副边全桥二极管整流，输出电容和负载。"}]),
    (re.compile(r"隔离"), re.compile(r"反激|正激|推挽|全桥|半桥|dab|双有源|llc|flyback|forward", re.I),
     "哪种隔离型变换器？",
     [{"label": "反激", "text": "单管反激变换器，一个 MOSFET，变压器异名端，副边一个二极管，输出电容和负载。"},
      {"label": "正激", "text": "单管正激变换器，带复位绕组，一个 MOSFET，副边整流二极管、续流二极管、"
                                "输出电感和电容，负载。"},
      {"label": "移相全桥", "text": "移相全桥变换器，原边 4 个 MOSFET 和串联电感，"
                                    "中心抽头副边全波整流，输出电感、电容和负载。"},
      {"label": "双有源桥 DAB", "text": "双有源桥 DAB 变换器，两侧各一个 MOSFET 全桥，"
                                        "串联电感和变压器，两侧直流电容，副边接负载。"}]),
    (re.compile(r"升降压|buck[- ]?boost", re.I), re.compile(r"反相|四开关|4 ?开关|cuk|sepic|zeta", re.I),
     "哪种升降压电路？",
     [{"label": "反相 Buck-Boost", "text": "反相 Buck-Boost 变换器，一个 MOSFET、一个二极管、电感、"
                                           "输出电容和负载，输出极性与输入相反。"},
      {"label": "四开关 Buck-Boost", "text": "同向四开关 Buck-Boost 变换器，4 个 MOSFET，"
                                             "一个电感居中，输入电容、输出电容和负载。"},
      {"label": "Cuk", "text": "Cuk 变换器，一个 MOSFET、一个二极管、两个电感、耦合电容，输出电容和负载。"},
      {"label": "SEPIC", "text": "SEPIC 变换器，一个 MOSFET、一个二极管、两个电感、耦合电容，"
                                 "输出电容和负载。"}]),
    (re.compile(r"整流"), re.compile(r"二极管|pwm|vienna|晶闸管|可控|同步|单相|三相", re.I),
     "哪种整流电路？",
     [{"label": "单相二极管桥", "text": "单相二极管桥式整流，4 个二极管，交流电源，输出电容和负载。"},
      {"label": "三相二极管桥", "text": "三相二极管桥式整流，6 个二极管，三相交流电源，输出电容和负载。"},
      {"label": "单相 PWM 整流", "text": "单相 PWM 整流器，交流电源经电感接 4 个 MOSFET 全桥，"
                                         "直流电容和负载。"},
      {"label": "Vienna", "text": "三相 Vienna 整流器，三相电源和三个输入电感，每相一个双向开关与"
                                  "两个快恢复二极管，直流侧两个串联电容和负载。"}]),
    (re.compile(r"逆变"), re.compile(r"单相|三相|半桥|全桥|z ?源|准 ?z|电平|npc|h ?桥|级联", re.I),
     "哪种逆变器？",
     [{"label": "单相全桥", "text": "单相全桥电压源逆变器，直流电源和直流电容，4 个带反并联二极管的 "
                                    "IGBT，LC 滤波和阻性负载。"},
      {"label": "三相两电平", "text": "三相两电平电压源逆变器，直流电源和直流电容，6 个带反并联二极管的 "
                                      "IGBT，三相星接阻感负载。"},
      {"label": "Z 源", "text": "Z 源三相逆变器，直流电源经二极管接 X 形 Z 源网络（两电感两电容），"
                                "6 个 IGBT，三相负载。"}]),
]

VARIANT = re.compile(r"级联|h ?桥|chb|npc|钳位|飞跨|flying|t ?型|t-type|anpc|mmc|模块化", re.I)


def options(description: str) -> dict | None:
    """``{"question", "options": [{"label", "text"}]}`` or None."""
    text = description.strip()
    if not text or len(text) > SHORT:
        return None
    n = _levels(text)
    if n and n >= 3 and not VARIANT.search(text):
        return {"question": f"{n} 电平有几种常见结构，要哪一种？", "options": _multilevel(n)}
    if n:
        return None
    for applies, specific, question, opts in FAMILIES:
        if applies.search(text) and not specific.search(text):
            return {"question": question, "options": opts}
    return None
