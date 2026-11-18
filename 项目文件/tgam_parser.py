# -*- coding: utf-8 -*-
"""tgam_parser.py — 纯 Python 的 TGAM 流解析器（官方 BrainLinkParser.pyd 的替代）

## 为什么存在
官方仓库 Macrotellect/BrainLinkParser-Python 只发布**闭源二进制**（BrainLinkParser.pyd /
.so），README 明确写 "Python Version Python 3.11"。本机只有 Python 3.13 → 官方 pyd
大概率 import 失败，真机链路直接起不来。而 EEG 主干走的是 **TGAM 公开帧格式**，
完全可以自己解，不依赖任何二进制。

## 与官方 SDK 的接口一致性（零侵入替换）
同名类、同参数顺序、同回调签名，`serial_bridge.py` / `live_bridge.py` 只需换 import：

    try:
        from BrainLinkParser import BrainLinkParser      # 官方（能拿到 RR/gyro/心率）
    except ImportError:
        from tgam_parser import BrainLinkParser          # 本文件（只有 EEG 主干）

    parser = BrainLinkParser(onEEG, onExtendEEG, onGyro, onRR, onRaw)
    parser.parse(raw_bytes)          # 流式：可切任意长度，内部缓冲拼包

## 数据覆盖范围（诚实声明，不假装全支持）
| 通道 | 本文件 | 说明 |
|---|---|---|
| signal / attention / meditation | ✅ | TGAM 标准段 |
| δ θ lowα highα lowβ highβ lowγ midγ | ✅ | TGAM 0x83 段 8×24bit |
| raw（原始波） | ✅ | TGAM 0x80 段，有符号 16bit |
| **heart / ap / battery / gnaw / temperature** | ❌ | **BrainLink 私有扩展包**，无公开规范 |
| **RR 间期（rr1/rr2/rr3）/ 血氧** | ❌ | **BrainLink 私有**；Android SDK 的 onRR 返回 ArrayList+oxygen，且需 setDataType 下发开启 |

→ 因此**不要**指望本解析器填 HRV 特征。`onExtendEEG` / `onRR` / `onGyro` 三个回调
   **一律不触发**（不是 bug，是协议边界）。调用方须按降级口径使用：stress/anxiety 的
   HRV 判据降级为佐证。要拿 RR 只有两条路：官方 pyd（需 3.11 或确认 cp 绑定）或抓包逆向。

## 帧格式（来源：NeuroSky《MindSet Communications Protocol》+ TGAM1 datasheet + 实测抓包三方一致）
    AA AA <PLEN> <PAYLOAD ...> <CHK>
    PLEN = payload 字节数（不含 PLEN 自身与 CHK）；常见 32
    CHK  = 255 - (sum(PAYLOAD) & 0xFF)   ⇔  校验判 (sum(PAYLOAD) + CHK) & 0xFF == 0xFF
    payload 段：
      0x02 <signal>            poorSignalLevel：**0 最好，200 电极未接触皮肤，越大越差**
      0x04 <attention>         eSense 0–100
      0x05 <meditation>        eSense 0–100
      0x80 <hi> <lo>           原始波，有符号 int16 大端
      0x83 <len=0x18> <8×3>    8 频段，每段 3 字节大端无符号
                               顺序 δ / θ / lowα / highα / lowβ / highβ / lowγ / midγ

## 用法自检（无需真机）
    python tgam_parser.py
"""
from __future__ import annotations

import sys

__all__ = ["BrainLinkParser", "BrainLinkData", "build_frame", "BAND_FIELDS"]

SYNC0 = SYNC1 = 0xAA
MAX_PLEN = 170                      # TGAM 单帧 payload 上限（实测大包 32–36）

SEG_SIGNAL = 0x02
SEG_ATT = 0x04
SEG_MED = 0x05
SEG_RAW = 0x80
SEG_BANDS = 0x83

BAND_FIELDS = ("delta", "theta", "lowAlpha", "highAlpha",
               "lowBeta", "highBeta", "lowGamma", "highGamma")


def _checksum(payload: bytes) -> int:
    return (255 - (sum(payload) & 0xFF)) & 0xFF


def build_frame(payload: bytes) -> bytes:
    """按 TGAM 格式拼一帧（测试/回放造数据用）"""
    if not 0 < len(payload) <= MAX_PLEN:
        raise ValueError(f"payload 长度非法：{len(payload)}")
    return bytes([SYNC0, SYNC1, len(payload)]) + payload + bytes([_checksum(payload)])


class BrainLinkData:
    """仿官方 BrainLinkData：字段名与官方 README 一致，可直接喂 SdkAdapter.onEEG"""

    __slots__ = ("signal", "attention", "meditation") + BAND_FIELDS

    def __init__(self, signal: int = 200, attention: float = 0.0, meditation: float = 0.0,
                 bands: list[int] | None = None):
        self.signal = signal
        self.attention = attention
        self.meditation = meditation
        b = bands or [0] * 8
        for name, val in zip(BAND_FIELDS, b):
            setattr(self, name, float(val))

    def __repr__(self) -> str:
        return (f"BrainLinkData(signal={self.signal}, att={self.attention:.0f}, "
                f"med={self.meditation:.0f}, " +
                ", ".join(f"{n}={getattr(self, n):.0f}" for n in BAND_FIELDS) + ")")


class BrainLinkParser:
    """TGAM 流解析器；接口与官方 BrainLinkParser 一致（5 个回调 + parse(bytes)）"""

    def __init__(self, eeg_callback=None, eeg_extend_callback=None,
                 gyro_callback=None, rr_callback=None, raw_callback=None):
        self._cb_eeg = eeg_callback
        self._cb_raw = raw_callback
        # 保留三个不支持的通道参数，仅为签名兼容；本解析器永不触发它们（私有协议，见模块文档）
        self._unsupported = [n for n, cb in
                             (("eeg_extend_callback", eeg_extend_callback),
                              ("gyro_callback", gyro_callback),
                              ("rr_callback", rr_callback)) if cb is not None]

        self._buf = bytearray()
        # 跨帧保持的状态：signal 通常 1Hz、bands/att/med 更高频，缺段时沿用上次值
        self._signal = 200                 # 保守初值：未收到 signal 前按「不可用」处理
        self._att = 0.0
        self._med = 0.0
        self._bands = [0] * 8

        # 现场诊断计数：真机「没数据」时，靠它区分「没收到字节」/「校验全错」/「段不认识」
        self.stats = {"frames": 0, "eeg_callbacks": 0, "bad_checksum": 0,
                      "bad_plen": 0, "resync_drop": 0, "unknown_seg": {}, "raw": 0}

    # -- 流式入口 -------------------------------------------------------
    def parse(self, data: bytes) -> int:
        """喂入任意长度的字节流，返回本次成功回调 onEEG 的次数"""
        if data:
            self._buf.extend(data)
        fired = 0
        buf = self._buf
        while True:
            if len(buf) < 3:
                break
            idx = buf.find(b"\xAA\xAA")
            if idx < 0:
                # 无同步头：只保留最后 1 字节（可能是下一个 AA 的前半）
                drop = len(buf) - 1
                if drop > 0:
                    self.stats["resync_drop"] += drop
                    del buf[:drop]
                break
            if idx > 0:
                self.stats["resync_drop"] += idx
                del buf[:idx]
            if len(buf) < 3:
                break
            plen = buf[2]
            if plen == 0 or plen > MAX_PLEN:
                self.stats["bad_plen"] += 1
                del buf[0]                      # 假同步头，挪一位再找
                continue
            total = 3 + plen + 1
            if len(buf) < total:
                break                           # 半包：等下一次 parse 补齐
            payload = bytes(buf[3:3 + plen])
            chk = buf[3 + plen]
            if (sum(payload) + chk) & 0xFF != 0xFF:
                self.stats["bad_checksum"] += 1
                del buf[0]                      # 校验不过：当假头，重新同步
                continue
            del buf[:total]
            self.stats["frames"] += 1
            if self._parse_payload(payload):
                fired += 1
        return fired

    # -- payload --------------------------------------------------------
    def _parse_payload(self, p: bytes) -> bool:
        i = 0
        has_bands = False
        has_esense = False
        while i < len(p):
            code = p[i]
            i += 1
            if code == SEG_SIGNAL:
                if i >= len(p):
                    break
                self._signal = p[i]
                i += 1
            elif code == SEG_ATT:
                if i >= len(p):
                    break
                self._att = float(p[i])
                i += 1
                has_esense = True
            elif code == SEG_MED:
                if i >= len(p):
                    break
                self._med = float(p[i])
                i += 1
                has_esense = True
            elif code == SEG_RAW:
                if i + 1 >= len(p):
                    break
                raw = int.from_bytes(p[i:i + 2], "big", signed=True)
                i += 2
                self.stats["raw"] += 1
                if self._cb_raw:
                    self._cb_raw(raw)
            elif code == SEG_BANDS:
                if i >= len(p):
                    break
                ln = p[i]
                i += 1
                if ln < 24 or i + ln > len(p):
                    self.stats["unknown_seg"]["0x83/bad_len"] = \
                        self.stats["unknown_seg"].get("0x83/bad_len", 0) + 1
                    break
                self._bands = [int.from_bytes(p[i + k * 3:i + k * 3 + 3], "big")
                               for k in range(8)]
                i += ln                          # 按声明长度前进（兼容 >24 的变体）
                has_bands = True
            else:
                # 私有/未知段：长度未知，无法安全前进 → 结束本帧解析（已解字段保留）
                key = f"0x{code:02X}"
                self.stats["unknown_seg"][key] = self.stats["unknown_seg"].get(key, 0) + 1
                break
        if (has_bands or has_esense) and self._cb_eeg:
            d = BrainLinkData(self._signal, self._att, self._med, self._bands)
            self._cb_eeg(d)
            self.stats["eeg_callbacks"] += 1
            return True
        return False

    # -- 诊断 -----------------------------------------------------------
    def health(self) -> str:
        """一行健康摘要（真机现场贴日志用）"""
        s = self.stats
        unk = ", ".join(f"{k}×{v}" for k, v in s["unknown_seg"].items()) or "无"
        return (f"帧 {s['frames']}｜EEG 回调 {s['eeg_callbacks']}｜校验错 {s['bad_checksum']}"
                f"｜长度错 {s['bad_plen']}｜重同步丢弃 {s['resync_drop']} 字节"
                f"｜未知段 {unk}｜raw {s['raw']}")


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    """合成帧自检：正例 + 5 条反例。断言的是「算得出来」，不是「有数据」。"""
    print("=== tgam_parser 自检（合成帧，无需真机）===")
    fails: list[str] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    def std_payload(signal=25, att=65, med=55, bands=(120, 95, 60, 45, 80, 40, 18, 12),
                    raw: int | None = None) -> bytes:
        p = bytes([SEG_SIGNAL, signal])
        if raw is not None:
            p += bytes([SEG_RAW]) + int(raw).to_bytes(2, "big", signed=True)
        p += bytes([SEG_BANDS, 24])
        for b in bands:
            p += int(b).to_bytes(3, "big")
        p += bytes([SEG_ATT, att, SEG_MED, med])
        return p

    # 1 标准大包：字段逐项对得上
    got = []
    pr = BrainLinkParser(eeg_callback=got.append)
    pr.parse(build_frame(std_payload()))
    d = got[0] if got else None
    check("标准包解析", d is not None and (d.signal, d.attention, d.meditation) == (25, 65, 55)
          and (d.delta, d.theta, d.lowAlpha, d.highAlpha) == (120, 95, 60, 45)
          and (d.lowBeta, d.highBeta, d.lowGamma, d.highGamma) == (80, 40, 18, 12),
          str(d))

    # 2 半包：整帧逐字节喂，仍应恰好回调 1 次（流式缓冲正确）
    got2 = []
    pr2 = BrainLinkParser(eeg_callback=got2.append)
    frame = build_frame(std_payload(signal=10))
    for byte in frame:
        pr2.parse(bytes([byte]))
    check("逐字节半包拼帧", len(got2) == 1 and got2[0].signal == 10 and pr2.stats["bad_checksum"] == 0,
          f"回调 {len(got2)} 次，缓冲区残留 {len(pr2._buf)} 字节")

    # 3 反例·坏校验：必须不回调，且计入 bad_checksum
    bad = bytearray(build_frame(std_payload()))
    bad[-1] = (bad[-1] + 1) & 0xFF
    got3 = []
    pr3 = BrainLinkParser(eeg_callback=got3.append)
    pr3.parse(bytes(bad))
    check("反例·单字节篡改校验和 → 不回调", len(got3) == 0 and pr3.stats["bad_checksum"] >= 1,
          f"回调 {len(got3)} 次，bad_checksum={pr3.stats['bad_checksum']}")

    # 4 反例·前置垃圾：重同步后仍应正确解出（并且记录丢弃字节数）
    got4 = []
    pr4 = BrainLinkParser(eeg_callback=got4.append)
    pr4.parse(b"\x00\x13\x37garbage" + build_frame(std_payload(signal=7)))
    check("前置垃圾字节 → 重同步成功", len(got4) == 1 and got4[0].signal == 7
          and pr4.stats["resync_drop"] >= 10,
          f"丢弃 {pr4.stats['resync_drop']} 字节，回调 {len(got4)} 次")

    # 5 反例·未知私有段：不能崩、不能瞎读，已解析字段要保留
    got5 = []
    pr5 = BrainLinkParser(eeg_callback=got5.append)
    p5 = bytes([SEG_SIGNAL, 30, SEG_ATT, 70, SEG_MED, 60]) + bytes([0x5A, 0x01, 0x02, 0x03])
    pr5.parse(build_frame(p5))
    check("未知段 0x5A → 不崩溃且保留已解字段",
          len(got5) == 1 and got5[0].attention == 70 and got5[0].meditation == 60
          and pr5.stats["unknown_seg"].get("0x5A") == 1,
          f"unknown_seg={pr5.stats['unknown_seg']}")

    # 6 raw 有符号：负值必须解成负数（无符号解析会静默给出 65000+）
    raw_vals = []
    pr6 = BrainLinkParser(eeg_callback=lambda d: None, raw_callback=raw_vals.append)
    pr6.parse(build_frame(std_payload(raw=-312)))
    check("raw 有符号解析", raw_vals == [-312], f"raw={raw_vals}")

    # 7 反例·连发两帧：必须回调 2 次（不能把第二帧吞进第一帧）
    got7 = []
    pr7 = BrainLinkParser(eeg_callback=got7.append)
    pr7.parse(build_frame(std_payload(signal=11)) + build_frame(std_payload(signal=12)))
    check("两帧粘连 → 回调 2 次", len(got7) == 2 and [g.signal for g in got7] == [11, 12],
          f"signals={[g.signal for g in got7]}")

    # 8 反例·只有 signal 无 bands/eSense：不应凭空造一帧 EEG（避免把心跳包当脑电）
    got8 = []
    pr8 = BrainLinkParser(eeg_callback=got8.append)
    pr8.parse(build_frame(bytes([SEG_SIGNAL, 200])))
    check("反例·仅 signal 段 → 不回调", len(got8) == 0 and pr8.stats["frames"] == 1,
          f"回调 {len(got8)} 次，帧计数 {pr8.stats['frames']}")

    # 9 电极未接触皮肤：signal=200 必须原样透传（门控交给上层，解析器不改语义）
    got9 = []
    pr9 = BrainLinkParser(eeg_callback=got9.append)
    pr9.parse(build_frame(std_payload(signal=200)))
    check("signal=200 原样透传", len(got9) == 1 and got9[0].signal == 200,
          f"signal={got9[0].signal if got9 else None}")

    print()
    if fails:
        print(f"自检失败 {len(fails)} 项：{fails}")
        return 1
    print(f"自检全部通过（9 项，含 3 条反例）。接口与官方 BrainLinkParser 一致，可直接替换 import。")
    print("注意：onExtendEEG / onGyro / onRR 不会被触发（BrainLink 私有协议，见模块文档）。")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
