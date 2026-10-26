"""拟人化操作工具

把脚本里「固定 sleep + 精确点中心」的机械化操作，换成带随机性的拟人操作：
- 随机节奏：所有固定等待改成随机区间，每次运行的时序都不一样；
- 人类点击：点之前随机「思考」一下，落点在元素内按比例随机偏移，点完短暂停留；
- 人类滑动：贝塞尔弧线轨迹，起终点带抖动、控制点随机偏离、首尾慢中间快；
- 坐标抖动：固定坐标点击加 ±N 像素随机偏移，避免每次落在同一个像素上；
- 状态轮询：需要反复判断状态的场景用「不思考的快速点击 + 短随机间隔」。

设计原则
--------
1. **不改变业务逻辑**：每个函数的参数、返回值、异常行为都尽量与原调用保持一致，
   主脚本只要把 `time.sleep(x)` / `xxx.click()` 换成这里的同名语义函数即可。
2. **可以安全降级**：拿不到元素 bounds 时退回元素自带的 click，不抛异常；
   设备不支持滚动中心点等特性时退回普通实现。
3. **默认可用**：模块自带一个随机源，不依赖任何第三方库（不引入 numpy 之外的依赖，
   实际上只用到标准库 random / math / time）。

典型用法
--------
    from humanize import (human_click, human_sleep, human_swipe,
                          jitter_point, poll_click, tap_xy)

    human_sleep(3)                 # 3 秒左右，实际 2.4~4.2 秒
    human_click(d, d(text="去完成"))  # 思考 + 随机落点 + 点击后停留
    human_swipe(d)                 # 在屏幕中部随手滑一下（贝塞尔弧线）
    tap_xy(d, screen_width // 2, 150)  # 固定坐标点击，自带 ±15px 抖动
"""

import math
import random
import time

# ---------------------------------------------------------------------------
# 全局可调参数：想整体调快/调慢只改这里
# ---------------------------------------------------------------------------

# 随机节奏的抖动比例：sleep(x) 实际等待 x * uniform(1-RATIO, 1+RATIO)
SLEEP_JITTER_RATIO = 0.4

# 点击前的「思考」延迟（秒）
THINK_DELAY = (0.25, 0.9)

# 状态轮询场景使用的短间隔（秒）——不做思考，只留自然停顿
POLL_INTERVAL = (0.3, 0.8)

# 点击落点在元素内的偏移比例（相对宽/高的一半），±0.16 约等于元素内部 68% 的随机区域
TAP_OFFSET_RATIO = 0.16

# 点击后短暂停留（秒）
AFTER_TAP_PAUSE = (0.05, 0.2)

# 固定坐标点击的抖动像素
COORD_JITTER_PX = 15

# 滑动轨迹采样点数
SWIPE_STEPS = 40


def _sleep_range(low, high):
    """在 [low, high] 内随机等待"""
    time.sleep(random.uniform(low, high))


# ---------------------------------------------------------------------------
# 随机节奏
# ---------------------------------------------------------------------------

def human_sleep(seconds, jitter=SLEEP_JITTER_RATIO, minimum=0.0):
    """拟人版 time.sleep：在 seconds 附近随机浮动

    Args:
        seconds: 期望的等待时长（秒）
        jitter: 浮动比例，0.4 表示 ±40%
        minimum: 下限，避免抖动后变成 0 或负数
    """
    if seconds <= 0:
        return
    actual = seconds * random.uniform(1 - jitter, 1 + jitter)
    actual = max(minimum, actual)
    time.sleep(actual)


def sleep_range(low, high):
    """在指定区间内随机等待（两个参数都按 ±jitter 再浮动一次，避免区间过于规律）"""
    _sleep_range(low, high)


def poll_sleep(seconds=None):
    """状态轮询专用短间隔：默认 0.3~0.8 秒"""
    if seconds is None:
        low, high = POLL_INTERVAL
    else:
        low, high = seconds * 0.7, seconds * 1.3
    time.sleep(random.uniform(low, high))


def task_poll_sleep(seconds=4.0):
    """任务轮询间隔（原脚本里的 time.sleep(4)）：约 2.6~5.4 秒"""
    low, high = seconds * 0.65, seconds * 1.35
    time.sleep(random.uniform(low, high))


def page_load_sleep(seconds=3.0):
    """页面加载后的等待（原脚本里的 time.sleep(3)）：约 2.4~4.2 秒"""
    low, high = seconds * 0.8, seconds * 1.4
    time.sleep(random.uniform(low, high))


# ---------------------------------------------------------------------------
# 人类点击
# ---------------------------------------------------------------------------

def jitter_point(x, y, radius=COORD_JITTER_PX):
    """给固定坐标加上 ±radius 的随机偏移，避免每次都点在同一个像素上"""
    nx = x + random.uniform(-radius, radius)
    ny = y + random.uniform(-radius, radius)
    return int(round(nx)), int(round(ny))


def element_tap_point(bounds, ratio=TAP_OFFSET_RATIO):
    """在元素矩形内计算一个「不那么准」的点击点

    真实手指点击不会精确落在正中心，而是分布在中心附近的区域。
    这里在中心点 ±(宽/高 * ratio) 的范围内随机取点。
    """
    if not bounds or len(bounds) != 4:
        return None
    left, top, right, bottom = bounds
    width = max(1, right - left)
    height = max(1, bottom - top)
    cx = left + width / 2
    cy = top + height / 2
    x = cx + random.uniform(-width * ratio, width * ratio)
    y = cy + random.uniform(-height * ratio, height * ratio)
    # 保证落在元素内部（留 1px 余量）
    x = min(max(x, left + 1), right - 1)
    y = min(max(y, top + 1), bottom - 1)
    return int(round(x)), int(round(y))


def _get_bounds(element):
    """尽力获取元素 bounds，取不到返回 None（不抛异常）"""
    for attr in ("bounds",):
        try:
            value = getattr(element, attr)
            value = value() if callable(value) else value
            if value and len(value) == 4:
                return tuple(value)
        except Exception:
            pass
    return None


def think(delay=THINK_DELAY):
    """点击前「思考」一下"""
    _sleep_range(*delay)


def tap_xy(d, x, y, think_first=True, radius=COORD_JITTER_PX, settle=True):
    """拟人版固定坐标点击：思考 → 坐标抖动 → 点击 → 短暂停留

    参数顺序与原 d.click(x, y) 一致，方便替换。
    """
    if think_first:
        think()
    px, py = jitter_point(x, y, radius)
    d.click(px, py)
    if settle:
        _sleep_range(*AFTER_TAP_PAUSE)
    return px, py


def human_click(d, element, think_first=True, ratio=TAP_OFFSET_RATIO, settle=True):
    """拟人版元素点击

    与 element.click() 语义一致，只是点击前思考、落点随机偏移、点击后停留。
    取不到 bounds 时退回元素自带的 click()，保证任何情况下都能点。
    """
    if think_first:
        think()
    bounds = _get_bounds(element)
    point = element_tap_point(bounds, ratio) if bounds else None
    if point:
        d.click(point[0], point[1])
    else:
        element.click()
    if settle:
        _sleep_range(*AFTER_TAP_PAUSE)


def poll_click(d, element, ratio=TAP_OFFSET_RATIO):
    """状态轮询专用点击：不做「思考」，点完立刻返回，由调用方控制间隔速度"""
    human_click(d, element, think_first=False, ratio=ratio, settle=False)


# ---------------------------------------------------------------------------
# 人类滑动（贝塞尔弧线）
# ---------------------------------------------------------------------------

def bezier_points(start, end, control=None, steps=SWIPE_STEPS, ease=True):
    """生成二次贝塞尔曲线上的点（用于模拟手指滑动的弧线轨迹）

    Args:
        start / end: 起终点 (x, y)
        control: 控制点，None 时按起终点自动随机生成一个偏离直线的点
        steps: 采样点数
        ease: 是否做「首尾慢、中间快」的非线性时间分布
    """
    (x0, y0), (x1, y1) = start, end
    if control is None:
        # 在起终点连线的中垂线方向随机偏离，形成自然弧线
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1.0
        offset = random.uniform(-0.18, 0.18) * length
        control = (mx - dy / length * offset, my + dx / length * offset)

    cx, cy = control
    points = []
    for i in range(steps + 1):
        t = i / steps
        if ease:
            # smoothstep 的逆：让手指在起止阶段移动得快一点、中间更连贯
            # 这里用 ease-in-out 曲线控制「进度」分布
            t = t * t * (3 - 2 * t)
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1
        y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1
        points.append((int(round(x)), int(round(y))))
    return points


def human_swipe(d, fx=None, fy=None, tx=None, ty=None, duration=None,
                jitter=12, arc=0.18, steps=SWIPE_STEPS):
    """拟人版滑动：起终点抖动 + 贝塞尔弧线 + 变速

    不传坐标时：在屏幕中部随机选一段「向上滑」的轨迹。
    传了坐标时：在起终点附近抖动 jitter 像素后，沿弧线滑动。

    Returns:
        (start, end) 实际使用的起终点
    """
    width, height = d.window_size()
    if fx is None:
        fx = random.uniform(width * 0.15, width * 0.75)
        fy = random.uniform(height * 0.62, height * 0.85)
        tx = fx + random.uniform(-width * 0.08, width * 0.02)
        ty = fy - random.uniform(height * 0.22, height * 0.42)
    else:
        fx, fy = jitter_point(fx, fy, jitter)
        tx, ty = jitter_point(tx, ty, jitter)

    start = (int(fx), int(fy))
    end = (int(tx), int(ty))

    # 控制点在连线中垂线方向随机偏离
    mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    offset = random.uniform(-arc, arc) * length
    control = (mx - dy / length * offset, my + dx / length * offset)

    points = bezier_points(start, end, control=control, steps=steps)

    # 分段变速：每段间隔不同，首尾慢、中间快
    if duration is None:
        duration = random.uniform(0.35, 0.75)

    try:
        d.swipe_points(points, duration=duration)
    except Exception:
        # 部分固件/服务端不支持 swipe_points，退回普通 swipe
        d.swipe(start[0], start[1], end[0], end[1], duration)

    _sleep_range(*AFTER_TAP_PAUSE)
    return start, end


def human_scroll(d, direction="up", duration=None):
    """按方向做一次拟人滑动（up: 内容向上翻一屏）"""
    width, height = d.window_size()
    if direction in ("up", "forward"):
        fx = random.uniform(width * 0.2, width * 0.8)
        fy = random.uniform(height * 0.68, height * 0.85)
        ty = fy - random.uniform(height * 0.3, height * 0.5)
        tx = fx + random.uniform(-60, 60)
    else:
        fx = random.uniform(width * 0.2, width * 0.8)
        fy = random.uniform(height * 0.3, height * 0.45)
        ty = fy + random.uniform(height * 0.3, height * 0.5)
        tx = fx + random.uniform(-60, 60)
    return human_swipe(d, fx, fy, tx, ty, duration=duration)


# ---------------------------------------------------------------------------
# 便捷封装
# ---------------------------------------------------------------------------

def human_long_click(d, element_or_point, duration=3.0, think_first=True,
                     ratio=TAP_OFFSET_RATIO, settle=True):
    """拟人版长按：思考 → 随机落点 → 长按 duration 秒 → 停留"""
    if think_first:
        think()
    if isinstance(element_or_point, (tuple, list)) and len(element_or_point) == 2:
        x, y = jitter_point(element_or_point[0], element_or_point[1])
    else:
        bounds = _get_bounds(element_or_point)
        point = element_tap_point(bounds, ratio) if bounds else None
        if point is None:
            # 拿不到坐标时退回元素自带长按
            element_or_point.long_click(duration=duration)
            if settle:
                _sleep_range(*AFTER_TAP_PAUSE)
            return
        x, y = point
    # 长按时长也带一点随机，避免每次都精确 3 秒
    d.long_click(x, y, duration * random.uniform(0.9, 1.15))
    if settle:
        _sleep_range(*AFTER_TAP_PAUSE)
