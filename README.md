# 🐳 Docker Panel

轻量级 Docker 容器管理 Web 面板，支持 **群晖 Synology**、**飞牛 fnOS** 等 NAS 系统。

## 分支说明

| 分支 | 说明 | 适用 |
|------|------|------|
| `master` | 主分支，通用版本 | 群晖 / 标准 Linux / Docker |
| `fnos` | 飞牛 fnOS 适配版 | 飞牛 NAS（含专属部署脚本） |

## ✨ 特性

- 📊 **系统概览** — CPU、内存、网络、磁盘、容器实时状态（点击 pill 弹出详情 Modal）
- 🐳 **容器管理** — 启动/停止/重启/删除，支持筛选和搜索
- 💾 **磁盘可视化** — 圆形饼图展示各共享文件夹占用比例 + 剩余空间
- 🎨 **多主题** — 暗黑 / 亮色 / 海洋 / 紫色 四种主题
- 📱 **响应式设计** — 支持桌面和移动端
- ⚡ **轻量级** — Python FastAPI + 原生 HTML/JS，单文件后端
- 🔄 **版本检查** — 自动从 GitHub 检测新版本

## 🚀 快速开始

### Docker 容器（通用）

```bash
docker build -t docker-panel:latest .

# 标准 Linux / 飞牛 fnOS
docker run -d --name docker-panel -p 50087:50087 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /:/host:ro \
  -e HOST_ROOT=/host \
  --restart always docker-panel:latest

# 群晖 Synology（bridge 模式，推荐）
docker run -d --name docker-panel --restart always \
  -p 50087:50087 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /:/host:ro \
  -e HOST_ROOT=/host \
  docker-panel:latest
```

### 群晖 Synology 快速部署

```bash
git clone https://github.com/nbqcw123/docker-panel.git
cd docker-panel
bash build-nas.sh
```

> ⚠️ **重要**：群晖上必须挂载 `/var/run/docker.sock`，否则容器管理 API 会返回 500 错误，页面显示"无法加载"。

### 飞牛 fnOS 快速部署

```bash
# 方式1：克隆后直接运行部署脚本
git clone https://github.com/nbqcw123/docker-panel.git
cd docker-panel
bash build-fnOS.sh

# 方式2：如果飞牛无法访问 GitHub，可手动上传代码到 NAS 后执行：
# 将 docker-panel 项目上传到飞牛任意目录，进入该目录运行：
bash build-fnOS.sh
```

> ⚠️ **重要**：`build-fnOS.sh` 会自动 git clone/pull 代码、构建镜像并启动容器（含 `/:/host:ro` 挂载和 `HOST_ROOT=/host` 环境变量）。

### 直接运行 Python

```bash
pip3 install fastapi uvicorn pydantic
python3 -m uvicorn main:app --host 0.0.0.0 --port 50087
```

## 系统适配

| 特性 | 群晖 Synology | 飞牛 fnOS | 标准 Linux |
|------|-------------|----------|-----------|
| Docker 路径 | `/volume1/@appstore/...` | `/usr/bin/docker` | `docker` |
| 磁盘检测 | `/volume1`, `/` | `/`, `/mnt/*`, `/data*` | `/` |
| 网络模式 | host 模式 | bridge / host | bridge |

Docker 二进制路径和磁盘挂载点均为**自动检测**，无需手动配置。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 面板页面 |
| GET | `/api/version` | 版本信息（本地/远程/更新检测） |
| GET | `/api/check-update` | 检查是否有新版本（含 changelog） |
| POST | `/api/upgrade` | 一键自升级（拉取代码→重建容器） |
| POST | `/api/restart` | 重启面板服务 |
| GET | `/api/containers` | 获取所有容器列表 |
| GET | `/api/containers/all-stats` | 容器列表 + 实时统计 |
| GET | `/api/container/{id}/stats` | 单个容器统计（CPU/内存/网络） |
| GET | `/api/container/{id}/disk` | 单个容器磁盘占用 |
| GET | `/api/system` | 系统信息（内存、磁盘、端口） |
| GET | `/api/system/cpu-info` | CPU 详细信息（型号/核心/负载/各核占用） |
| GET | `/api/system/network-info` | 网络信息（网卡/IP/收发流量） |
| GET | `/api/system/disk-info` | 磁盘信息（分区/共享文件夹/剩余空间） |
| POST | `/api/container/{id}/action` | 容器操作（start/stop/restart） |
| POST | `/api/container/{id}/custom-name` | 设置自定义名称 |
| POST | `/api/container/{id}/description` | 设置容器用途 |

## 文件结构

| 文件 | 说明 |
|------|------|
| `main.py` | 后端 FastAPI + 前端 HTML（单文件，2346行） |
| `version.json` | 版本信息 |
| `Dockerfile` | Docker 镜像构建 |
| `build-fnOS.sh` | 飞牛 fnOS 部署脚本 |
| `run-fnOS.sh` | 飞牛 fnOS 直接运行脚本 |
| `README.md` | 本文档 |

## 📝 更新日志

### v1.5.0 (2026-06-27)
- 刷新按钮改为加载按钮，首次自动加载，后续需手动点击刷新
- 端口列表改为可点击，显示该端口所使用的容器信息
- "已占用端口"改为"已使用端口"
- 修复磁盘检测：100TB阈值计算错误、overlay过滤、飞牛/群晖vol挂载识别
- 关于页面检查更新后显示「立即升级」按钮，支持一键自升级（拉取代码→重建容器）
- 异步化外部 API 调用，避免 uvicorn 事件循环阻塞

### v1.4.9 (2026-06-26)
- 磁盘圆形分割区域加入剩余空间显示
- 关于页面精简：版本、作者、更新日志、GitHub链接、检查更新按钮
- 作者改为 GitHub 用户名 nbqcw123
- 新增 fnos 分支（飞牛 fnOS 适配）

### v1.4.8 (2026-06-26)
- 磁盘 Modal 改为实心饼图（圆形分割区域）
- header 右侧加"关于"按钮（居中模态框）

### v1.4.7 (2026-06-25)
- 磁盘饼图展示占用和总容量

### v1.4.6 (2026-06-25)
- 关于页增加系统概览、负载、网络流量

### v1.4.5 (2026-06-25)
- 磁盘 Modal 增加共享文件夹 du 统计

### v1.4.4 (2026-06-25)
- CPU/内存/网络/磁盘 Modal 详细信息增强

### v1.4.3 (2026-06-25)
- 修复 CPU/内存/网络/磁盘/容器/关于 6 个 Modal 点击不弹出

### v1.4.2 (2026-06-25)
- 去掉 header 版本号显示
- 新增磁盘 SVG 环形饼图 Modal

### v1.4.1 (2026-06-21)
- 修复 lastSysData 变量未声明问题
- 增强四个 Modal 详细信息

### v1.4.0 (2026-06-21)
- 圆形比例图方式显示磁盘空间
- 磁盘可点击弹出详细 Modal

### v1.3.5 (2026-06-21)
- 修复路由覆盖主页问题
- 优化 SSH 传输方案

### v1.3.2 (2026-06-21)
- 网络详情改为心电图波形图
- 系统详情增加容器磁盘占用图表
- 新增 CPU/网络实时信息 API

## 👤 作者

**nbqcw123**

## 📄 License

MIT
