#!/usr/bin/env bash
# OfferPilot ECS 一次性初始化脚本。
# 在 ECS 上以 root 运行，目标系统 Alibaba Cloud Linux 3（RHEL 系）。
# 只做宿主机准备，不涉及任何密钥。
set -euo pipefail

APP_DIR=/root/offerpilot

echo "==> 1/4 安装 Docker Engine + compose 插件"
if command -v docker >/dev/null 2>&1; then
  echo "已安装: $(docker --version)"
else
  dnf install -y dnf-plugins-core || true
  if dnf config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo 2>/dev/null \
     && dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin; then
    echo "通过阿里云镜像源安装成功"
  else
    echo "镜像源安装失败，回退官方脚本"
    curl -fsSL https://get.docker.com | sh
  fi
  systemctl enable --now docker
fi

echo "==> 2/4 添加 2G swap（2C2G 机器防 OOM）"
if swapon --show 2>/dev/null | grep -q '/swapfile'; then
  echo "swap 已存在，跳过"
else
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "2G swap 已启用"
fi

echo "==> 3/4 准备应用目录 ${APP_DIR}"
mkdir -p "${APP_DIR}"

echo "==> 4/4 检查云助手 Agent（RunCommand 部署依赖它）"
if systemctl is-active --quiet aliyun.service 2>/dev/null; then
  echo "云助手 Agent 运行中"
else
  echo "警告: 未检测到 aliyun.service，RunCommand 可能不可用，请到 ECS 控制台确认云助手已安装"
fi

cat <<'NEXT'

初始化完成。接下来手动做三件事:
  1) 把 docker-compose.prod.yml 和 .env 放进 /root/offerpilot
  2) docker login registry.cn-beijing.aliyuncs.com   （用 ACR 固定密码）
  3) cd /root/offerpilot && docker compose -f docker-compose.prod.yml up -d
NEXT
