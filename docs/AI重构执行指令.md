# HDZZ 项目重构执行指令

> **目标：** 根据《项目深度分析与重构方案.md》执行代码优化和重构
> **执行者：** Claude Code / AI 助手
> **项目路径：** `/Users/zsc5200x/Desktop/杰尼龟共享文件夹/hdzz`

---

## 📋 执行前必读

**前置条件：**
1. ✅ 已阅读 `docs/项目深度分析与重构方案.md`
2. ✅ 已停止所有运行中的服务：`python app.py stop`
3. ✅ 已创建 Git 分支或备份：`git checkout -b refactor/optimization-v2`

**核心原则：**
- 🔒 每次修改前必须备份原文件
- 📝 每个步骤完成后等待用户确认
- 🧪 修改后必须验证 JSON 格式和 Python 语法
- 📊 记录所有改动并输出总结

---

## 🎯 执行指令（请严格按顺序执行）

---

## 阶段 1：短期优化（10 分钟见效）

### 任务 1.1：优化 follow_settings.json 配置

**目标：** 调整 5 个关键参数，提升成功率 10%+

**执行步骤：**

```markdown
1. 备份配置文件
   - 读取：data/follow_settings.json
   - 备份到：data/follow_settings.json.backup.YYYYMMDD_HHMMSS
   - 命令：cp data/follow_settings.json data/follow_settings.json.backup.$(date +%Y%m%d_%H%M%S)

2. 查找完整配置结构
   - 搜索代码中的 FOLLOW_SETTINGS_DEFAULTS
   - 位置应该在：src/hdzz/api/webui/follow_settings.py
   - 读取默认配置结构

3. 合并当前配置和新优化参数
   - 保留用户现有的配置（enabled, mode, fixed_amount 等）
   - 添加/更新以下参数：

```json
{
  "split_count": 3,
  "split_delay_seconds": 0.5,
  "wait_line_not_found_max_seconds": 45,
  "wait_line_not_found_probe_seconds": 2,
  "game_more_scan_all_gids": true,
  "use_quote_member_for_total_odds": true,
  "order_view_confirm_before_bet": true,
  "allow_quote_member_for_bet": false
}
```

4. 展示 diff
   - 使用 git diff 或文本对比展示修改内容
   - 高亮显示新增/修改的配置项

5. 等待用户确认后写入文件
   - 确保 JSON 格式正确（使用 jq 或 python -m json.tool 验证）
   - 写入 data/follow_settings.json
```

**验证方法：**
```bash
# 验证 JSON 格式
python -c "import json; json.load(open('data/follow_settings.json'))"

# 查看修改内容
git diff data/follow_settings.json
```

**预期效果：**
- ✅ 分单速度提升 50%（8秒 → 0.5秒/单）
- ✅ LINE_NOT_FOUND 探测时间延长 50%（30秒 → 45秒）
- ✅ 查水融合功能确保开启

---

### 任务 1.2：优化 arsenal.json（会员池配置）

**目标：** 添加 role 字段，区分查水账号和下注账号

**执行步骤：**

```markdown
1. 备份文件
   - 备份：data/arsenal.json → data/arsenal.json.backup.YYYYMMDD_HHMMSS

2. 读取现有配置
   - 检查 members 数组中是否已有 role 字段

3. 为每个会员添加 role 字段
   - 如果会员用于查水：role = "quote"
   - 如果会员用于下注：role = "bet"
   - 如果两者都做：role = "both"（不推荐）

4. 添加友好标签 label 字段
   - label: "查水账号A" / "下注账号B" 等
   - 用于 WebUI 展示

5. 示例格式：

```json
{
  "members": [
    {
      "id": "abc123",
      "username": "test001",
      "label": "查水账号A",
      "role": "quote",
      "base_url": "https://hga027.com",
      "enabled": true,
      "status": "online"
    },
    {
      "id": "def456",
      "username": "test002",
      "label": "下注账号B",
      "role": "bet",
      "base_url": "https://hga027.com",
      "enabled": true,
      "status": "online"
    }
  ]
}
```

6. 展示 diff 并等待确认

7. 写入文件并验证
```

**验证方法：**
```bash
# 验证 JSON 格式
python -c "import json; json.load(open('data/arsenal.json'))"

# 检查 role 字段
grep -E '"role":\s*"(quote|bet|both)"' data/arsenal.json
```

---

### 任务 1.3：创建配置诊断脚本

**目标：** 自动检测配置问题，给出优化建议

**执行步骤：**

```markdown
1. 创建新文件：scripts/check_config.py

2. 实现以下功能：
   - 读取 data/follow_settings.json
   - 检查关键参数是否设置合理
   - 读取 data/arsenal.json
   - 检查是否有 role=quote 的会员
   - 输出诊断报告和优化建议

3. 脚本模板（你可以基于这个完善）：

```python
#!/usr/bin/env python3
"""
配置诊断脚本
检查 follow_settings.json 和 arsenal.json 的配置是否合理
"""

import json
import sys
from pathlib import Path

def check_follow_settings():
    """检查跟单配置"""
    path = Path("data/follow_settings.json")
    if not path.exists():
        print("❌ data/follow_settings.json 不存在")
        return False

    config = json.loads(path.read_text())
    issues = []
    suggestions = []

    # 检查分单参数
    split_count = config.get("split_count", 1)
    split_delay = config.get("split_delay_seconds", 0)

    if split_count > 3:
        issues.append(f"⚠️  split_count={split_count} 过大，建议设置为 2-3")
        suggestions.append("split_count 建议值：3")

    if split_delay > 2.0:
        issues.append(f"⚠️  split_delay_seconds={split_delay} 过大，建议设置为 0.5-1.0")
        suggestions.append("split_delay_seconds 建议值：0.5")

    # 检查查水融合
    if not config.get("game_more_scan_all_gids", False):
        issues.append("⚠️  game_more_scan_all_gids=false，建议开启")
        suggestions.append("game_more_scan_all_gids 建议值：true")

    if not config.get("use_quote_member_for_total_odds", False):
        issues.append("⚠️  use_quote_member_for_total_odds=false，建议开启")
        suggestions.append("use_quote_member_for_total_odds 建议值：true")

    # 检查探测窗口
    wait_time = config.get("wait_line_not_found_max_seconds", 30)
    if wait_time < 40:
        issues.append(f"⚠️  wait_line_not_found_max_seconds={wait_time} 过小，建议设置为 45-60")
        suggestions.append("wait_line_not_found_max_seconds 建议值：45")

    return issues, suggestions

def check_arsenal():
    """检查会员池配置"""
    path = Path("data/arsenal.json")
    if not path.exists():
        print("❌ data/arsenal.json 不存在")
        return False

    config = json.loads(path.read_text())
    members = config.get("members", [])

    issues = []
    suggestions = []

    # 检查是否有 quote 会员
    quote_members = [m for m in members if m.get("role") == "quote"]
    if not quote_members:
        issues.append("❌ 没有配置 role=quote 的查水账号")
        suggestions.append("建议：至少添加 1 个 role=quote 的会员用于查水")

    # 检查是否有 label
    no_label = [m for m in members if not m.get("label")]
    if no_label:
        issues.append(f"⚠️  {len(no_label)} 个会员缺少 label 字段")
        suggestions.append("建议：为每个会员添加友好的 label 标签")

    return issues, suggestions

def main():
    print("=" * 60)
    print("HDZZ 配置诊断报告")
    print("=" * 60)
    print()

    # 检查跟单配置
    print("【1】检查 follow_settings.json")
    print("-" * 60)
    follow_issues, follow_suggestions = check_follow_settings()

    if not follow_issues:
        print("✅ 配置正常")
    else:
        print(f"发现 {len(follow_issues)} 个问题：")
        for issue in follow_issues:
            print(f"  {issue}")
        print()
        print("优化建议：")
        for sugg in follow_suggestions:
            print(f"  • {sugg}")

    print()

    # 检查会员池
    print("【2】检查 arsenal.json")
    print("-" * 60)
    arsenal_issues, arsenal_suggestions = check_arsenal()

    if not arsenal_issues:
        print("✅ 配置正常")
    else:
        print(f"发现 {len(arsenal_issues)} 个问题：")
        for issue in arsenal_issues:
            print(f"  {issue}")
        print()
        print("优化建议：")
        for sugg in arsenal_suggestions:
            print(f"  • {sugg}")

    print()
    print("=" * 60)
    print("诊断完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
```

4. 设置执行权限：chmod +x scripts/check_config.py

5. 运行诊断：python scripts/check_config.py
```

---

## 阶段 2：中期重构（3-4 周）

### 任务 2.1：拆分 engine.py - 提取 quote_manager.py

**目标：** 将查水管理逻辑从 engine.py（6846 行）中提取出来

**执行步骤：**

```markdown
1. 备份 engine.py
   - cp src/hdzz/core/follower/engine.py src/hdzz/core/follower/engine.py.backup

2. 创建新文件：src/hdzz/core/follower/quote_manager.py

3. 从 engine.py 中提取以下函数/逻辑：
   - _get_quote_member() - 获取查水会员
   - _load_quote_member_from_file() - 从文件加载查水会员配置
   - 所有涉及 role=quote 的逻辑
   - FT_order_view 相关的查水调用

4. 新文件结构：

```python
"""
查水管理器

负责管理查水会员、实时水位查询、缓存管理。
"""

from __future__ import annotations
from typing import Dict, Any, Optional
from pathlib import Path
import json

class QuoteManager:
    """查水管理器"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.quote_member = None
        self._load_quote_member()

    def _load_quote_member(self) -> None:
        """加载查水会员配置"""
        # 从 data/arsenal.json 读取 role=quote 的会员
        pass

    def get_quote_member(self) -> Optional[Dict[str, Any]]:
        """获取查水会员"""
        return self.quote_member

    def fetch_live_odds(
        self,
        gid: str,
        market: str,
        side: str
    ) -> Dict[str, Any]:
        """
        查询实时水位（使用 FT_order_view）

        Args:
            gid: 比赛 ID
            market: 玩法（RE/ROU）
            side: 方向（H/C）

        Returns:
            {
                "success": bool,
                "spread": str,
                "ioratio": str,
                "score": str,
                "code": str
            }
        """
        pass

    def warmup_quote_member(self) -> bool:
        """预热查水会员会话"""
        pass

# 提供全局单例（可选）
_quote_manager = None

def get_quote_manager(data_dir: Path) -> QuoteManager:
    global _quote_manager
    if _quote_manager is None:
        _quote_manager = QuoteManager(data_dir)
    return _quote_manager
```

5. 修改 engine.py 导入：
   - 在 engine.py 顶部添加：from .quote_manager import QuoteManager, get_quote_manager
   - 替换所有查水相关调用

6. 展示修改内容（diff）

7. 等待确认后执行
```

**验证方法：**
```bash
# 检查 Python 语法
python -m py_compile src/hdzz/core/follower/quote_manager.py
python -m py_compile src/hdzz/core/follower/engine.py

# 统计 engine.py 行数（应该减少 800+ 行）
wc -l src/hdzz/core/follower/engine.py
```

---

### 任务 2.2：拆分 engine.py - 提取 game_ctx_cache.py

**目标：** 将缓存管理逻辑提取出来

**执行步骤：**

```markdown
1. 创建新文件：src/hdzz/core/follower/game_ctx_cache.py

2. 从 engine.py 中提取以下逻辑：
   - _GAME_CTX_CACHE 相关代码
   - _XML_CACHE_LOCK 相关代码
   - _GAME_LIST_XML_CACHE / _GAME_MORE_XML_CACHE
   - 所有缓存管理函数

3. 新文件结构：

```python
"""
比赛上下文缓存管理

管理 lid/ecid 缓存、XML 缓存，提高查询效率。
"""

from __future__ import annotations
import threading
import time
from typing import Dict, Any, Tuple, Optional

class GameContextCache:
    """比赛上下文缓存"""

    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, gid: str) -> Optional[Dict[str, Any]]:
        """获取缓存的 lid/ecid"""
        pass

    def set(self, gid: str, lid: str, ecid: str) -> None:
        """设置缓存"""
        pass

    def clear_expired(self) -> None:
        """清理过期缓存"""
        pass

class XmlResponseCache:
    """XML 响应缓存"""

    def __init__(self, ttl_seconds: float = 0.8, max_items: int = 256):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._cache: Dict[str, Tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        """获取缓存的 XML 响应"""
        pass

    def set(self, key: str, xml_text: str) -> None:
        """设置缓存"""
        pass

    def clear(self) -> None:
        """清空缓存"""
        pass
```

4. 修改 engine.py 导入

5. 展示 diff 并等待确认
```

---

### 任务 2.3：拆分 engine.py - 提取 retry_scheduler.py

**目标：** 将重试调度逻辑提取出来

**执行步骤：**

```markdown
1. 创建新文件：src/hdzz/core/follower/retry_scheduler.py

2. 从 engine.py 中提取：
   - RetryJob 相关逻辑
   - 重试队列管理
   - 延迟调度
   - LINE_NOT_FOUND 探测窗口

3. 新文件结构：

```python
"""
重试调度器

管理失败订单的重试队列、延迟调度、探测窗口。
"""

from __future__ import annotations
import time
from typing import List, Dict, Any, Optional
from .models import RetryJob

class RetryScheduler:
    """重试调度器"""

    def __init__(self):
        self.retry_jobs: List[RetryJob] = []

    def add_job(self, job: RetryJob) -> None:
        """添加重试任务"""
        pass

    def get_ready_jobs(self) -> List[RetryJob]:
        """获取可执行的重试任务"""
        pass

    def update_job_status(self, job_id: str, status: str) -> None:
        """更新任务状态"""
        pass

    def get_line_not_found_jobs(self) -> List[RetryJob]:
        """获取 LINE_NOT_FOUND 探测窗口中的任务"""
        pass
```

4. 修改 engine.py 导入

5. 展示 diff 并等待确认
```

---

### 任务 2.4：改进 WebUI - 添加配置面板

**目标：** 在跟单队列页添加配置面板，实时调整参数

**执行步骤：**

```markdown
1. 备份文件：
   - app/webui/chashui/queue.html
   - app/webui/chashui/queue.js
   - app/webui/chashui/queue.css

2. 在 queue.html 添加配置面板（在 <main> 标签内添加）：

```html
<section class="card configCard">
  <div class="cardHead">
    <h2>配置优化</h2>
    <div class="pillRow">
      <span id="stConfigStatus" class="pill">未修改</span>
    </div>
  </div>

  <div class="configGrid">
    <div class="field">
      <div class="label">分单数量</div>
      <input id="inSplitCount" type="number" min="1" max="5" value="3" />
      <div class="hint">建议：2-3（减少耗时）</div>
    </div>

    <div class="field">
      <div class="label">分单延迟（秒）</div>
      <input id="inSplitDelay" type="number" min="0" max="10" step="0.1" value="0.5" />
      <div class="hint">建议：0.5-1.0（加快速度）</div>
    </div>

    <div class="field">
      <div class="label">探测窗口（秒）</div>
      <input id="inWaitLineNotFound" type="number" min="10" max="120" value="45" />
      <div class="hint">建议：45-60（延长等待）</div>
    </div>

    <div class="field span2">
      <div class="label">查水融合</div>
      <div class="inlineRow">
        <label class="check">
          <input id="ckScanAllGids" type="checkbox" checked />
          <span>跨 gid 扫描</span>
        </label>
        <label class="check">
          <input id="ckUseQuote" type="checkbox" checked />
          <span>优先 quote 会员</span>
        </label>
        <label class="check">
          <input id="ckConfirmOdds" type="checkbox" checked />
          <span>下注前确认</span>
        </label>
      </div>
    </div>
  </div>

  <div class="actionsRow">
    <button id="btnSaveConfig" class="btn btn-primary">保存配置</button>
    <button id="btnResetConfig" class="btn btn-ghost">重置为推荐值</button>
  </div>
</section>
```

3. 在 queue.js 添加配置保存逻辑

4. 在 queue.css 添加样式

5. 展示修改内容并等待确认
```

---

### 任务 2.5：改进错误提示

**目标：** 让错误信息更友好、更易懂

**执行步骤：**

```markdown
1. 在 queue.js 中添加错误信息映射：

```javascript
const ERROR_MESSAGES = {
  'LINE_NOT_FOUND': {
    title: '盘口线位变化',
    message: '目标盘口线暂时找不到，正在持续探测中...',
    solution: '建议：延长探测窗口时间或减少分单数量',
    icon: '🔍'
  },
  'GAME_NOT_FOUND': {
    title: '比赛已下架',
    message: '该比赛在会员端已找不到',
    solution: '可能原因：比赛已结束、被撤盘、或数据同步延迟',
    icon: '❌'
  },
  'SCORE_CHANGED': {
    title: '比分已变化',
    message: '滚球比分已更新，护栏机制拒绝下注',
    solution: '建议：如需跟进球瞬间的单，请调整护栏配置',
    icon: '⚽'
  },
  'AMOUNT_EXCEED': {
    title: '金额超限',
    message: '单笔金额超过会员限额',
    solution: '建议：增加分单数量或降低单笔金额',
    icon: '💰'
  }
};

function formatError(errorCode, details) {
  const info = ERROR_MESSAGES[errorCode] || {
    title: '未知错误',
    message: details,
    solution: '请查看日志获取详细信息',
    icon: '⚠️'
  };

  return `
    <div class="errorCard">
      <div class="errorIcon">${info.icon}</div>
      <div class="errorContent">
        <div class="errorTitle">${info.title}</div>
        <div class="errorMessage">${info.message}</div>
        <div class="errorSolution">${info.solution}</div>
      </div>
    </div>
  `;
}
```

2. 修改订单卡片显示逻辑，使用友好错误信息

3. 展示修改内容并等待确认
```

---

## 阶段 3：长期规划（1-2 月）

### 任务 3.1：智能配置助手

**目标：** 自动分析日志，生成优化建议

**执行步骤：**

```markdown
1. 创建新文件：scripts/analyze_logs.py

2. 实现以下功能：
   - 解析 data/follow_results.jsonl
   - 统计成功率、失败率、错误类型分布
   - 分析 LINE_NOT_FOUND 发生的时间窗口
   - 分析分单耗时对成功率的影响
   - 生成优化建议

3. 输出优化建议 JSON：
   - 推荐的 split_count
   - 推荐的 split_delay_seconds
   - 推荐的 wait_line_not_found_max_seconds
   - 是否需要增加查水账号

4. 展示代码并等待确认
```

---

## 📊 执行检查清单

每完成一个任务，请在这里打勾：

**阶段 1（短期优化）：**
- [ ] 1.1 优化 follow_settings.json
- [ ] 1.2 优化 arsenal.json
- [ ] 1.3 创建配置诊断脚本

**阶段 2（中期重构）：**
- [ ] 2.1 提取 quote_manager.py
- [ ] 2.2 提取 game_ctx_cache.py
- [ ] 2.3 提取 retry_scheduler.py
- [ ] 2.4 改进 WebUI - 添加配置面板
- [ ] 2.5 改进错误提示

**阶段 3（长期规划）：**
- [ ] 3.1 智能配置助手

---

## 🧪 验证方法

### 每次修改后必须验证：

```bash
# 1. JSON 格式验证
python -c "import json; json.load(open('data/follow_settings.json'))"
python -c "import json; json.load(open('data/arsenal.json'))"

# 2. Python 语法验证
python -m py_compile src/hdzz/core/follower/*.py

# 3. 启动测试
python app.py start

# 4. 查看日志（观察 5 分钟）
tail -f .run/logs/follow_engine.err.log

# 5. 检查成功率
python scripts/check_success_rate.py  # 如果有的话
```

---

## 📈 预期效果

| 阶段 | 完成时间 | 成功率目标 | LINE_NOT_FOUND 目标 |
|------|---------|-----------|-------------------|
| 阶段 1 | 1 周 | 71.5% → **80%** | 76.6% → **50%** |
| 阶段 2 | 1 月 | 80% → **85%** | 50% → **30%** |
| 阶段 3 | 3 月 | 85% → **90%** | 30% → **<20%** |

---

## ⚠️ 重要提醒

1. **每次修改前必须备份**
2. **修改后必须验证格式和语法**
3. **每个步骤完成后展示 diff 给用户确认**
4. **遇到不确定的地方，停下来询问用户**
5. **记录所有改动，便于回滚**

---

## 💬 执行示例

**用户可以这样对你说：**

```
执行 docs/AI重构执行指令.md 中的：
- 阶段 1 的所有任务
- 每个任务完成后暂停，等我确认
- 遇到问题立即停止并询问我
```

或者分步执行：

```
执行任务 1.1：优化 follow_settings.json
按照文档中的步骤执行，每步完成后给我看结果
```

---

**准备好执行了吗？** 🚀
