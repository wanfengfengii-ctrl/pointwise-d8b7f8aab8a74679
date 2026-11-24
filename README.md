# 栖湾泵房双表巡检实收

栖湾泵房的早班采集员接收记录器的甲乙压力表原页。每表只有一个当前槽，放页才能继续。质控员按设备刻度窗口签出有据可查的乙减甲。次班在记录器下线后按成果号领取，核对当时使用的两张原读页和各自的右端依据。

使用 Python 3.11 标准库和 SQLite。从项目根目录运行。

```text
python3 -B check_project.py
python3 -B workbench.py demo
python3 -B workbench.py init station.sqlite examples/rounds.json
```

标准回归入口随项目物化提供。演示使用两份临时 SQLite 库，依次完成开轮、放页失答、待证查询、重开续办、签出 2 kPa 和断源复核。`init` 以给定的登记文件开轮。已有轮同参复用，同号改配对、角色或范围报错。重复初始化保留逐表账、续办和成果。

## 调用接口

`workbench.open_archive(path)` 初始化并返回巡检库连接。`workbench.open_device(path, recording, delivery=None)` 初始化并返回模拟设备。两个路径分别由巡检库和设备使用。调用方负责关闭连接和设备。用同一录制重开同一设备库。录制中尚未供出的页由 `DeliverySchedule.visible_counts[(cycle, channel)]` 控制，省略该项时本路当前页随槽推进可见。`requests` 中每页可排入设备请求结局，`replies` 中每页可排入投递布尔值。释放和查结按调用先后消耗该页的应答计划。

| 工作 | Python 入口 | 返回 |
| --- | --- | --- |
| 开轮 | `rounds.registration.open_cycle(db, cycle, a, b, start, stop)` | `CycleRegistration` |
| 设备读页 | `device.read_current(cycle, channel)` | `SourcePage` 或 `None` |
| 设备放页、查结 | `device.release_page(ref)`、`device.query_release(ref)` | `ReleaseOutcome` |
| 实收与续办 | `capture.intake.collect(db, device, cycle, channel, page=None)` | `CollectionResult` 或 `None` |
| 逐表查账 | `audit.channel_history.read_channel_history(db, cycle, channel)` | 按设备原序排列的普通读数元组 |
| 请求签发 | `signing.commit.sign_window(db, cycle, start, stop)` | `SignOutcome`，状态为 `signed`、`pending` 或 `disjoint` |
| 成果复领 | `audit.retrieval.retrieve_result(db, result_id)` | `SignedResult` 或 `None` |
| 离线复核 | `audit.reconcile.reconcile_result(result)` | `Reconciliation`，含有效性和问题明细 |

高层写入接口使用空闲连接并自行提交。`collect` 省略页参数时读取当前槽，也可提交读到的 `SourcePage`。提交 `PageRef` 时，该页必须已经接纳。没有当前页时返回 `None`，包括尚未供页和已耗尽。逐表账包括已接纳且释放待查结的读数。低层读写辅助函数由事务所有者调用。

## 录制和计算

`device.recording.load_recording(path)` 读取带 `pages` 数组的 JSON 文件。数组内各轮各表按自己的设备原序排列，序号从 1 开始。每项包含 `cycle`、`channel`、`sequence`、`page_id`、`kind`、`tick`、`value` 和 `provenance`。有效普通页的 `kind` 为 `reading`，值为整数 kPa。`terminal` 页的值为 `null`，刻度可在登记右端。原序决定直接后继。普通读数的刻度允许相同，前一页在同刻发生换页时有效期为空。尚无直接后继的读数可查账，等到后继或轮末实收才获得可签的半开有效期。

查询裁剪到登记范围，签存保留原输入。空窗或窗外均为 `disjoint`。正向窗口内若有两路已证区间共同覆盖，本实现选最早整数刻度。甲 9、乙 11 各自覆盖 `[12,14)` 时，所选刻度为 12，签出 `11 - 9 = 2 kPa`。目前无见证且未来合法事件可能建立共同覆盖时返回 `pending`。两表区间只在右端和左端相触时，若窗内再无可能的共同刻度则返回 `disjoint`。两种未签状态均无成果和首执，零差值与负差值是合法结果。

倒置窗、非整数刻度、未知轮表、坏页和冲突身份抛出 `ValueError`。坏页不会触发释放。设备按具体原页身份持久记录实际结果，释放响应可能在动作生效后丢失。按旧身份查结成功时只补记本地进度。确认未生效且仍为当前页时才重发。模拟设备同步执行请求，查结时同身份仍处当前槽且无成功记录也可确认尚未生效。应答缺失时保留待查结。

`collect(..., fail_at='before_intake_commit')` 和 `sign_window(..., fail_at='before_receipt')` 或 `fail_at='before_sign_commit'` 可在演练中制造指定事务点的 `sqlite3.OperationalError`。同轮同原窗复签直接返回固定成果和原首执。次班用 `retrieve_result` 读取归档副本，复核不需要设备库，后续巡检也不会改变旧件。

`examples/rounds.json` 含两轮登记。`examples/shift.json` 演练接纳、轮末闭合和隔轮复领。`examples/touch.json` 用于核对只相触的覆盖。`examples/equal.json` 用于核对等值换页的独立出处。测试固定录制和操作次序，无时间或随机输入。
