# 缺陷报告：离线端点连接一旦失败即永久不可用（需重启后端）

**致**：成员 B（`backend/app/providers/local_llm.py` 的归属者）
**发现日期**：2026-10-08　**发现场景**：演示前本机联调，前端无法对话
**严重度**：高——**演示现场会表现为"整个页面无法对话"，且只有重启后端才能恢复**

## 一、现象

前端页面发消息，后端返回 502，页面拿不到任何回复：

```
HTTP 502 Bad Gateway
{"detail":"离线模式下所有本地 LLM 后端都不可用，已中止（不会回退到云端）。
失败原因 —— endpoint（APIConnectionError: Connection error.）；
llama_cpp（RuntimeError: 未配置 OFFLINE_LLM_MODEL_PATH，无法使用进程内后端）..."}
```

后端日志同步打印：

```
WARNING xiaonuan.llm.offline 离线后端 endpoint 不可用：Connection error.
WARNING xiaonuan.llm.offline 离线后端 llama_cpp 不可用：未配置 OFFLINE_LLM_MODEL_PATH
```

## 二、已排除的原因（都有实验）

| 猜测 | 验证方式 | 结论 |
|------|---------|------|
| llama-server 挂了 | `curl /health`（8090） | ❌ 一直回 `{"status":"ok"}`，`netstat` 显示仍在监听 |
| 端点地址配错 | 同环境变量下用 `openai` SDK 直连 8090 | ❌ 正常返回模型列表 |
| llama-server 侧状态坏了 | **把 llama-server 整个杀掉重起一个新的** | ❌ **仍然 502** |
| 空闲导致连接池里的连接失效 | 复用同一 client，空闲 8/16/24 秒后再探活 | ❌ 每次都在 0.02 秒内成功 |
| 浏览器提前断开流式请求把连接池弄脏 | 分别在"未开始生成"和"生成中途（收到首个 SSE 事件后）"断开 | ❌ 两种都不复现，之后对话正常 |

**关键对照结论：换一个全新的 llama-server 没有用，只有重启后端进程才有用。**
说明坏掉的状态在后端进程内部，不在模型服务侧。

## 三、根因（代码定位）

`local_llm.py` 有两层缓存，失败时只清了一层：

```python
_ENDPOINT_CLIENTS: dict = {}          # 第 52 行：模块级，key = (base_url, timeout)

def client(self):                     # 第 108-125 行
    client = _ENDPOINT_CLIENTS.get(key)
    if client is None:
        client = OpenAI(..., max_retries=0)
        _ENDPOINT_CLIENTS[key] = client
    return client

def _invalidate(self):                # 第 351-356 行
    _RESOLVED.pop(fp, None)           # 只清了「已解析的后端对象」
    self._backend = None              # ⚠️ 没有清 _ENDPOINT_CLIENTS
```

`grep _ENDPOINT_CLIENTS` 全文件只有三处：初始化、`get`、赋值——**没有任何一处会删除它**。

于是形成如下死结：

1. 某次连接失败（瞬时原因），httpx 连接池里留下坏连接；
2. 失败后 `_invalidate()` 让下一次请求重新解析后端 —— 这没问题；
3. 但重新解析出来的 `_EndpointBackend` 调用 `self.client()`，**拿回来的还是缓存里那个坏 client**（模块级字典，key 与 base_url/timeout 都相同）；
4. 于是每一次后续探活都复用坏连接 → 永远 `Connection error` → 每次都 502；
5. 因为 `max_retries=0`（这个设置本身是为了避免 CPU 慢推理被放大成三次），SDK 不会自愈。

**restart 后端 = 清空模块级字典 = 唯一恢复途径。** 这也解释了"换新 llama-server 没用"。

### 触发条件尚未定位

我复现不出稳定触发。发生时的现场特征记录如下，供排查参考：

- 系统内存压力极大：可用内存从 8.0 GB 降到 **1.1 GB、占用率 93%**；
- 浏览器页面刚被打开（约 20 秒前）；
- 距上一次成功请求约 1 分钟。

倾向性判断：**触发是一次瞬时连接失败（内存压力下 socket 分配失败是可能原因），而"永久卡死"是缺陷**。
即便触发难以复现，第 3 节那个永不自愈的机制是确定存在的——它把一个瞬时故障放大成"必须重启"。

## 四、建议修法（最小改动）

在连接类失败时**把池里的 client 一起丢弃**，让下次请求重建：

```python
def client(self, fresh: bool = False):
    key = (self.base_url, self.timeout)
    with _LOCK:
        client = _ENDPOINT_CLIENTS.get(key)
        if client is None or fresh:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url,
                            timeout=self.timeout, max_retries=0)
            _ENDPOINT_CLIENTS[key] = client
        return client

def probe(self, timeout: float) -> None:
    client = self.client()
    try:
        client.with_options(timeout=timeout).models.list()
    except APIConnectionError:
        # 连接级失败：池里的连接可能已坏，丢弃后重试一次新连接
        client = self.client(fresh=True)
        client.with_options(timeout=timeout).models.list()
```

`_invalidate()` 里也建议一并清 `_ENDPOINT_CLIENTS`（或按 `base_url` 前缀清理）。

要点：**只对连接级失败（`APIConnectionError`）做一次重建+重试**，不要放宽 `max_retries`——
否则 CPU 档一次超时会被放大成三次，那个顾虑仍然成立。

## 五、顺带：`--parallel 2` 能同时降低触发概率

`OFFLINE_LLM_PROBE_TIMEOUT=3`，而 `--parallel 1` 只有一个 slot：主对话在生成时，
探活请求会和它抢同一个 slot。改成 `--parallel 2`（配 `-c 24576`）后探活有独立 slot，
**既修前缀缓存（省 3.2 s/轮，见 `docs/offline_latency_findings.md`），又让探活不再和生成互踩**。

## 六、演示期的临时处置

1. 出现"页面无法对话"时，**先看后端日志有没有 `离线后端 endpoint 不可用`**；
2. 确认是本条 → 重启整套（`stop.ps1` + `start.ps1`），不要只重启 llama-server（没用）；
3. 彩排时把"断开后有回复"作为一项检查，避免现场第一次遇到。

## 七、附带发现：`stop.ps1` 不停后端，"关掉重起"按文档做会失败

> **已修**：`stop.ps1` 现在会一并停止后端（按端口查找，默认 8000，可用
> `-BackendPort` 覆盖；只会停 python 进程，端口被别的程序占用时跳过并提示）。
> 实测三个端口全部释放，`stop.ps1` → `start.ps1` 可以直接重起。

`stop.ps1` 原先只停 `llama.pid` 与 `voice.pid` 两个进程。**后端是 `start.ps1` 的前台
进程，没有 pid 文件，因此不会被停。** 实测：跑完 `stop.ps1` 后 8000 端口仍被占用、
后端仍在监听。

后果：照文档"关掉再重起"会失败——`start.ps1` 里的 uvicorn 绑不上 8000。

而第六节给出的**唯一恢复手段就是重启**，所以这条必须一起修，否则现场恢复不了。

## 八、同一族的第二个缺口：空闲后**第一个**请求仍会失败（已修）

第三节的修复（连接错误时丢弃缓存的 client）解决的是"**此后每次**都复用坏连接、
只有重启后端才能恢复"。但它留下一个更隐蔽的缺口：**丢弃发生在抛错之后**，
所以那一次请求本身还是失败了 —— 表现是"端点闲着（或重启）之后，
**第一个请求必 502，第二个开始才正常**"。

复现（2026-10-10，演示前实际撞上）：7B/GPU 档空闲约一个半小时，重新提问，
第一句返回 502 `Bad Gateway`，随后连续五个问题全部正常。

**根因链**：client 是进程级缓存的 → 端点回收空闲 keep-alive 连接（或端点重启）→
缓存的连接已死 → 首次请求抛 `APIConnectionError` → `chat()` 只 `_invalidate()` 然后
抛错 → 502。而 `max_retries=0`（为防 CPU 档一次超时被放大成三次，见
`docs/offline_two_tier_measurements.md`）**同时关掉了 SDK 对失效 keep-alive 的透明重连**
—— 这个善意的设置正是缺口的一部分。

**修法**：在 `_EndpointBackend.create()`（`chat` 与 `chat_stream` 的唯一收口）上
对**连接类**错误换新连接重试一次：

- 只重试一次，`max_retries=0` 保持不变；
- **刻意排除 `APITimeoutError`**：它也是 `APIConnectionError` 的子类，但它意味着请求
  可能已在端点侧跑起来，重试会把 CPU 档一次 600s 超时翻倍成 20 分钟，还可能重复推理。
  用 `_is_retryable_connection_error()` 表达这个区分。

**验证**：
- 回归测试 `test_stale_connection_on_chat_is_retried`（撤掉修复后确实失败）与
  `test_timeout_is_not_retried`（锁住超时不重试的刻意排除）。
- 真实场景：热请求建立池化连接 → 仅重启 `llama-server`（后端进程不动）→
  不做任何预热直接提问 → **首个请求返回 200**（修复前此处 502）。
