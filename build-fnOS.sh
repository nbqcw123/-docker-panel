#!/bin/bash
# build-fnOS.sh - 在飞牛 NAS (fnOS) 上构建并部署 docker-panel
# 飞牛 fnOS 使用标准 Docker，路径与群晖不同

set -e

echo "=== 飞牛 fnOS Docker Panel 部署脚本 ==="

# 配置
CONTAINER_NAME="docker-panel"
PORT="50087"
IMAGE_NAME="docker-panel:latest"

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
  $IMAGE_NAME

echo "=== 部署完成 ==="
echo "访问: http://$(hostname -I | awk '{print $1}'):$PORT"
