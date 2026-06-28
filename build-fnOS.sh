#!/bin/bash
# build-fnOS.sh - 在飞牛 NAS (fnOS) 上构建并部署 docker-panel
# 飞牛 fnOS 使用标准 Docker，路径与群晖不同
# 前提：飞牛能访问 GitHub（git clone）

set -e

echo "=== 飞牛 fnOS Docker Panel 部署脚本 ==="

# 配置
CONTAINER_NAME="docker-panel"
PORT="50087"
IMAGE_NAME="docker-panel:latest"
REPO="https://github.com/nbqcw123/docker-panel.git"

# 进入脚本目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 如果目录已有代码则更新，否则 clone
if [ -d ".git" ]; then
    echo "=== 更新代码 ==="
    git pull origin master
else
    echo "=== 克隆仓库 ==="
    git clone "$REPO" .
fi

echo "=== 停止旧容器 ==="
docker stop $CONTAINER_NAME 2>/dev/null || true
docker rm $CONTAINER_NAME 2>/dev/null || true

echo "=== 构建镜像 ==="
docker build -t $IMAGE_NAME .

echo "=== 启动容器 ==="
docker run -d \
  --name $CONTAINER_NAME \
  --restart always \
  -p $PORT:50087 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /:/host:ro \
  -e HOST_ROOT=/host \
  $IMAGE_NAME

echo "=== 部署完成 ==="
echo "访问: http://$(hostname -I | awk '{print $1}'):$PORT"
