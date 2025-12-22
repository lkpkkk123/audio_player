#!/bin/bash
# 拷贝服务文件
cp audio_server.service /etc/systemd/system/
# 刷新 systemd 配置
systemctl daemon-reload
# 开机自启
systemctl enable audio_server
# 立即启动
systemctl start audio_server
echo "Audio Server 部署完成并已尝试启动。"
