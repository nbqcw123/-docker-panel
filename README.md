# 🐳 Docker Panel

一个轻量级的 Docker Web 管理面板，专为 NAS（群晖/飞牛）设计，提供直观的容器管理、系统监控和磁盘使用情况可视化。

## ✨ 特性

- 📊 **系统概览** — CPU、内存、网络、磁盘、容器实时状态
- 🐳 **容器管理** — 启动/停止/重启/删除，支持筛选和搜索
- 💾 **磁盘可视化** — 圆形饼图（饼图分割区域）展示各共享文件夹占用与剩余空间
- 🎨 **多主题** — 暗黑 / 亮色 / 海洋 / 紫色 四种主题适配
- 📱 **响应式设计** — 支持桌面和移动端访问
- ⚡ **轻量级** — Python FastAPI + 原生 HTML/JS，无前端构建步骤

## 🚀 快速开始

### Docker 运行

```bash
docker run -d \
  --name docker-panel \
  -p 50087:50087 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /volume1:ro \
  docker-panel:latest
```

### Docker Compose

```yaml
version: "3"
services:
  docker-panel:
    image: nbqcw123/docker-panel:latest
    container_name: docker-panel
    ports:
      - "50087:50087"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    restart: always
```

### 参数说明

| 端口 | 说明 |
|------|------|
| `50087` | Web 管理界面访问端口 |

## 🛠️ 构建

```bash
docker build -t docker-panel:latest .
```

## 📸 截图

- **系统监控页** — 顶部 Header 显示 CPU/内存/网络/磁盘/容器统计，点击弹出详细 Modal
- **磁盘详情** — 圆形饼图展示共享文件夹占用比例，中心显示已用/剩余空间
- **容器管理** — 卡片列表，支持一键操作

## 📝 更新日志

### v1.4.9 (2026-06-26)
- 磁盘圆形分割区域加入剩余空间显示
- 关于页面精简：版本、作者、更新日志、GitHub链接、检查更新按钮
- 作者改为 GitHub 用户名

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
