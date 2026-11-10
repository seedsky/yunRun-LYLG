# 上游与随包资源

- 原始用户版本：yunRun-LYLG-v2.1.1-内置环境.zip。
- 上游：[Zirconium233/yunForNewVersion develop](https://github.com/Zirconium233/yunForNewVersion/tree/develop)，本轮检查时分支 SHA：15aed080b81d6ef8a41802e7fcbe9ffed2ea404e。
- Leaflet 1.9.4：随包 JS/CSS/图标，许可证见 static/vendor/LEAFLET-LICENSE.txt，https://leafletjs.com/。
- qrcode-generator 1.4.4：随包 qrcode.js，源文件保留 MIT 许可头；包说明见 static/vendor/QRCODE-LICENSE.txt，https://github.com/kazuhikoarase/qrcode-generator。
- 高德地图：仅联网加载底图，页面保留 attribution。坐标为 GCJ-02；离线不分发底图瓦片。
- Super Proxy APK：用户提供原文件随包，信息见 CAPTURE.md，未修改 APK。
- 内置 Python 及依赖沿用用户原包，许可证与各包 dist-info 信息位于 runtime/。

新工作台源代码保留原协议与几何模块的来源说明。第三方许可证与来源不因本次界面重构而改变。

1.0.0 保留用户在 seedsky/yunRun-LYLG 创建的 MIT 原文和 SpringSoap 署名。适用范围与上游无明确整体许可证的核对结果见根目录 NOTICE.md。公开 Git 仓库不收录 runtime 或用户提供的 APK；本机便携包仍保留相应第三方附件与许可信息。
