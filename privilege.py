"""执行层权限与痕迹处理

说明（很重要，请先读）
--------------------
「用 root 权限隐藏，让 APP 检测不到」这件事，需要先说清楚能做什么、不能做什么：

* 本模块**不会**伪装成 root，也不会在没 root 的机器上假装自己隐藏了。
  没有任何代码能在没有 root 的设备上隐藏 root 痕迹。
* 脚本对 App 的可见性，主要来自**输入注入方式**和**无障碍服务**：
  - uiautomator2 默认用 `input tap/swipe`（Android 的 input 命令），
    这类事件来源是 uid 2000(shell)，和 root 无关；
  - `d.dump_hierarchy()` 依赖 uiautomator 无障碍服务，部分 App 会检测无障碍服务是否开启。
* 真正的「隐藏」需要 root + 隐藏方案（KernelSU + Zygisk 模块 / Shamiko / HMA 等），
  这些是**系统层**的东西，由你在手机上装好；本模块只负责检测、选择更隐蔽的执行方式，
  并在不具备条件时如实降级并提示。

本模块提供三件事
--------------
1. `detect_privilege(d)`：探测当前执行层能力（shell / root / KernelSU / Shizuku / 无障碍）。
2. `hide_traces(d)`：在**有权限**时做的痕迹收敛（关闭/恢复自动旋转、屏幕常亮、
   尽量少用无障碍 dump、走 root 注入等）；没有权限时安全跳过。
3. `get_input_backend(d)`：返回当前实际可用的输入通道描述，便于日志里确认走的是哪条路。

检测结果会缓存，重复调用不会反复执行 adb 命令。
"""

import shutil
import subprocess
import time

# 缓存检测结果
_PRIVILEGE_CACHE = {}
_ROOT_CACHE = {"checked": False, "available": False, "method": None}


def _shell(d, cmd, timeout=10):
    """执行 adb 命令，异常时返回空字符串（不影响主流程）"""
    try:
        return d.shell(cmd, timeout=timeout).output or ""
    except Exception:
        return ""


def _local_adb(args, timeout=10):
    """在本机执行 adb 命令（用于检查设备属性等）"""
    adb = shutil.which("adb") or "adb"
    try:
        result = subprocess.run([adb] + args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception:
        return ""


def root_available(d):
    """检测设备上是否真的能拿到 root（su 可用且能返回 uid=0）

    Returns:
        (available: bool, method: str)
    """
    if _ROOT_CACHE["checked"]:
        return _ROOT_CACHE["available"], _ROOT_CACHE["method"]

    available, method = False, None

    # 1) 常见 su 路径
    su_paths = ["su", "/system/bin/su", "/system/xbin/su", "/sbin/su", "/data/adb/ksu/bin/su"]
    for su in su_paths:
        out = _shell(d, f"{su} -c id 2>/dev/null")
        if "uid=0" in out:
            available, method = True, f"su({su})"
            break

    # 2) KernelSU（未提供 su 命令时也可能可用）
    if not available:
        out = _shell(d, "ksud --version 2>/dev/null")
        if out.strip():
            out2 = _shell(d, "ksud su -c id 2>/dev/null")
            if "uid=0" in out2:
                available, method = True, "ksud"

    # 3) Magisk
    if not available:
        out = _shell(d, "magisk -v 2>/dev/null")
        if out.strip():
            available, method = True, "magisk"

    _ROOT_CACHE.update(checked=True, available=available, method=method)
    return available, method


def detect_privilege(d):
    """探测当前执行层能力，返回 dict

    Returns:
        {
          "shell_uid": "2000" 等,
          "root": bool, "root_method": str|None,
          "kernelsu": bool, "shizuku": bool,
          "dump_method": "uiautomator"|"uiautomator_root"|"screencap",
          "input_backend": "shell_input"|"root_input",
          "notes": [提示信息],
        }
    """
    if "info" in _PRIVILEGE_CACHE:
        return _PRIVILEGE_CACHE["info"]

    info = {
        "shell_uid": None,
        "root": False,
        "root_method": None,
        "kernelsu": False,
        "shizuku": False,
        "input_backend": "shell_input",
        "notes": [],
    }

    uid_line = _shell(d, "id")
    if "uid=" in uid_line:
        info["shell_uid"] = uid_line.split("uid=")[1].split("(")[0].strip()

    root_ok, root_method = root_available(d)
    info["root"] = root_ok
    info["root_method"] = root_method
    if root_ok:
        info["input_backend"] = "root_input"

    if _shell(d, "pm list packages me.weishu.kernelsu").strip():
        info["kernelsu"] = True
    if _shell(d, "pm list packages moe.shizuku.privileged.api").strip():
        info["shizuku"] = True

    if not root_ok:
        info["notes"].append(
            "未检测到可用的 root：执行层仍为 shell(uid 2000)，无法隐藏 root/注入痕迹"
        )
        if info["kernelsu"]:
            info["notes"].append(
                "已安装 KernelSU 但当前未生效（无 su/ksud 权限），请确认刷入的内核与授权状态"
            )

    _PRIVILEGE_CACHE["info"] = info
    return info


def get_input_backend(d):
    """返回当前实际使用的输入通道：root_input / shell_input"""
    return detect_privilege(d)["input_backend"]


def print_privilege_report(d):
    """打印一份执行层能力报告，方便判断为什么「隐藏」没生效"""
    info = detect_privilege(d)
    print("=" * 40)
    print("执行层权限检测")
    print(f"  shell uid     : {info['shell_uid']}")
    print(f"  root 可用     : {info['root']}" + (f"（{info['root_method']}）" if info["root_method"] else ""))
    print(f"  KernelSU      : {'已安装' if info['kernelsu'] else '未安装'}")
    print(f"  Shizuku       : {'已安装' if info['shizuku'] else '未安装'}")
    print(f"  输入通道      : {get_input_backend(d)}")
    for note in info["notes"]:
        print(f"  ! {note}")
    print("=" * 40)
    return info


def root_shell(d, cmd, timeout=10):
    """尽量以 root 执行命令；拿不到 root 时返回 None（调用方据此降级）"""
    available, method = root_available(d)
    if not available:
        return None
    if method and method.startswith("su("):
        su = method[3:-1]
        return _shell(d, f"{su} -c {cmd}", timeout=timeout)
    if method == "ksud":
        return _shell(d, f"ksud su -c {cmd}", timeout=timeout)
    if method == "magisk":
        return _shell(d, f"magisk su -c {cmd}", timeout=timeout)
    return None


def restore_system_settings(d):
    """恢复脚本可能改动过的系统设置（自动旋转）

    脚本里为了滑动稳定会关掉自动旋转，跑完应该恢复，否则用户自己用手机时
    会发现屏幕不转了 —— 这种「改了系统设置不恢复」的痕迹也很容易被注意到。
    """
    try:
        _shell(d, "settings put system accelerometer_rotation 1")
        return True
    except Exception:
        return False


def hide_traces(d):
    """有权限时做痕迹收敛；没有权限时安全跳过并返回 False

    当前实现（不需要 root 也能生效的部分）：
      - 保证脚本结束时恢复自动旋转设置（由调用方在 finally 里配合 restore_system_settings）
      - 记录了实际输入通道，便于排查

    需要 root 才能做的部分（如：以 root 身份注入输入事件、卸载无障碍服务、
    隐藏 su/模块痕迹）不会伪造，仅在 root 可用时把输入通道切到 root。
    """
    info = detect_privilege(d)
    if not info["root"]:
        return False
    # 有 root 时把输入切到 root 通道（具体注入由 humanize/调用方按 backend 选择）
    info["input_backend"] = "root_input"
    return True


def keep_screen_on(d, enable=True):
    """跑任务期间保持屏幕常亮，结束后调用 enable=False 还原"""
    try:
        if enable:
            _shell(d, "svc power stayon true")
        else:
            _shell(d, "svc power stayon false")
        return True
    except Exception:
        return False
