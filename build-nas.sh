#!/bin/bash
# build-nas.sh - 在群晖 NAS 上构建并部署 docker-panel
# 使用方法：将 main.py、Dockerfile、version.json 传到 NAS 后执行此脚本

set -e

echo "=== 群晖 Docker Panel 部署脚本 ==="

# Docker 路径（群晖）
DOCKER="/volume1/@appstore/ContainerManager/usr/bin/docker"
CONTAINER_NAME="docker-panel"
PORT="50087"
IMAGE_NAME="docker-panel:latest"

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 停止旧容器 ==="
sudo $DOCKER stop $CONTAINER_NAME 2>/dev/null || true
sudo $DOCKER rm $CONTAINER_NAME 2>/dev/null || true

echo "=== 构建镜像 ==="
sudo $DOCKER build -t $IMAGE_NAME .

echo "=== 启动容器 ==="
# ⚠️ 必须挂载 docker.sock，否则容器管理 API 会 500 错误
sudo $DOCKER run -d \
  --name $CONTAINER_NAME \
  --restart always \
  -p $PORT:50087 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  $IMAGE_NAME

echo "=== 部署完成 ==="
echo "访问: http://$(hostname -I | awk '{print $1}'):$PORT"
