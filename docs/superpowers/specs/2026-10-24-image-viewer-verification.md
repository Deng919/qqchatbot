# 单击图片放大验收

日期：2026-10-24

商品卡片、商品详情和共享原消息上下文里的图片，普通单击改为当前页面的放大预览。优先读取原图，失败时仅尝试已有本机预览一次。图片适配窗口，加载失败显示状态；图片本身不触发关闭，点空白、关闭按钮或 Esc 返回。关闭只影响图片层，保留原商品详情、原消息和阅读位置，恢复焦点并释放图片源。

实现只修改 `base.html`：复用既有图片链接，通过共享事件处理与原生 dialog 展示；不新增图库导航、缩放拖动状态、后端接口或存储结构。只接受当前来源的两类已有图片 API，拦截普通单击的新窗口默认行为；原链接仍作为浏览器回退。动态文字不进入 HTML。

## 验证

- TDD 先观察共享查看器缺失的失败，再验证原图放大、无新标签页、嵌套原生／自定义弹层、Esc、空白、关闭按钮、焦点与滚动恢复、一次回退、错误状态、外部地址拒绝及宽窄屏适配。
- 56个 CJS 全部通过，17项相关 Python 测试通过（`test_secondhand_web.py`、`test_message_images.py`、`test_desktop.py`）。本轮未重跑 Python 全量，后端与 EXE 未改动。
- 真实图片分别在源码、冻结包服务、冻结包原生 WebView及正式服务验证：卡片、详情及原消息单击均能显示更大原图，关闭不误关底层商品详情，1440／390宽度没有横向溢出。查看过程无写请求、AI 调用或页面异常。
- 独立代码复核没有待处理发现。123个模块、56份模板与最终源码一致，2192个文件清单完整性通过。

## 本机交付

- 当前：`D:\Apps\QQDigestDesktop-2026-10-24-ImageViewer-Release`
- 入口：`D:\Desktop\QQ Digest.lnk`
- 回退：`D:\Apps\QQDigestDesktop-2026-10-24-AutoMarketImages-Release`
- 验收、截图及版本记录：`D:\Cache\QQDigestImageViewer-20261024`
- 实际效果：`D:\Cache\QQDigestImageViewer-20261024\ui\live-card-large.png`

新目录从已校验 AutoMarketImages 包复制，仅替换共享模板并重新生成完整版本清单，EXE 哈希一致。后端标识沿用 `2026-10-24-auto-market-images`；原生更新器确认切换，正式服务也核实新模板已生效，配置哈希不变。普通群14个、二手群8个，历史范围仍为13个群、1257条要点。

通过版本登记器移除 `D:\Apps\QQDigestDesktop-2026-10-24-HistoryMarket-Release`，保留 ImageViewer 当前、AutoMarketImages 回退、CurrentMarketPhotos、MarketPhotos 和 SessionStartFix 共五个版本。未重写摘要或交易记录，原图缓存继续复用，公开安装包未重新发布。
