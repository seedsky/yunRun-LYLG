# 来源与许可范围

本项目由 SpringSoap（seedsky）维护，基于用户先前修改的洛阳理工 2.1.1 版本，原始上游为
[Zirconium233/yunForNewVersion 的 develop 分支](https://github.com/Zirconium233/yunForNewVersion/tree/develop)。
本次核对的上游提交为 `15aed080b81d6ef8a41802e7fcbe9ffed2ea404e`。

## 上游及既有文件

`main.py`、`yun_http.py`、`yun_face.py`、`yun_route.py`、`history.py` 为保留或修改的上游模块；
`taskgen.py`、任务表及既有示例还包含用户先前版本的实现和资源。
保留原作者及原有贡献者的权利，不将这些文件整体标为本项目原创或 MIT 授权。
核对的上游 develop 文件树和 README 未提供明确的整体许可证；本仓库新增 MIT 许可
不替代上游授权。公开可见与已获统一开源授权是两件事。

## 本次原创新增内容

`workbench/`、不含 vendor 的 `static/`、`tests/`、`launchers/`、新 `web.py`、
构建与发行脚本、新抓包桥和新文档采用 [MIT](LICENSES/MIT.txt)，许可原文见 [LICENSE](LICENSE)，本说明明确其仅适用于原创新增部分。

## 第三方

- Leaflet：BSD-2-Clause，原文见 `static/vendor/LEAFLET-LICENSE.txt`。
- qrcode-generator：MIT，原文见 `static/vendor/QRCODE-LICENSE.txt`，保留文件许可头。
- Python 及其依赖：遵循各自许可证；便携包保留 runtime 中原许可与包元数据。
- Super Proxy：第三方软件，不受本项目 MIT 许可覆盖。公开源码仓库不收录用户提供的 APK；
  可从 [官方站点](https://superproxy.cc/) 获取。用户本机便携包按既有要求附带该 APK，未修改。
- 地图：高德在线底图，保留页面 attribution，不分发瓦片缓存。

完整资源清单见 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)。
