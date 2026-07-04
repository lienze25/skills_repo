# Skills Repository — Ubuntu 服务器部署指南

## 环境要求

- Ubuntu 20.04+ / 22.04 / 24.04
- Python 3.9+
- 端口 8080（可配置）

## 1. 安装依赖

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
```

## 2. 部署项目

```bash
# 克隆项目（或其他方式获取代码）
git clone <repo-url> /opt/skills_repo
# 或者手动创建目录后复制文件：
# mkdir -p /opt/skills_repo && cp -r . /opt/skills_repo/

# 创建虚拟环境并安装依赖
python3 -m venv /opt/skills_repo/venv
/opt/skills_repo/venv/bin/pip install -r /opt/skills_repo/requirements.txt

# 确保数据目录存在
mkdir -p /opt/skills_repo/skills
```

## 3. 配置

编辑 `/opt/skills_repo/config.json`：

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "auth_enabled": true,
  "default_user": "admin",
  "server_version": "1.0.0"
}
```

### 添加用户

```bash
/opt/skills_repo/venv/bin/python3 /opt/skills_repo/server.py --add-user
```

## 4. Systemd 服务

创建服务文件 `/etc/systemd/system/skills-repo.service`：

```ini
[Unit]
Description=Skills Repository Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/skills_repo
ExecStart=/opt/skills_repo/venv/bin/python3 /opt/skills_repo/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
# 确保 www-data 对项目目录有读取权限
sudo chown -R www-data:www-data /opt/skills_repo

sudo systemctl daemon-reload
sudo systemctl enable --now skills-repo
sudo systemctl status skills-repo
```

常用管理命令：

```bash
sudo systemctl status skills-repo   # 查看状态
sudo systemctl restart skills-repo  # 重启
sudo systemctl stop skills-repo     # 停止
sudo journalctl -u skills-repo -f   # 查看日志
```

## 5. 防火墙

```bash
sudo ufw allow 8080/tcp
```

## 6. Nginx 反向代理（可选）

生产环境建议加一层 Nginx：标准 80/443 端口、SSL 证书、静态缓存、请求过滤。

```bash
sudo apt install -y nginx
```

创建 `/etc/nginx/sites-available/skills-repo`：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用站点：

```bash
sudo ln -s /etc/nginx/sites-available/skills-repo /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 7. 验证

```bash
# 本地验证
curl http://localhost:8080/api/config

# 远程验证
curl http://<server-ip>:8080/api/config
```

浏览器访问 `http://<server-ip>:8080` 即可进入 Web 界面。

## 8. 更新升级

```bash
# 停止服务
sudo systemctl stop skills-repo

# 替换 server.py 和 web/ 文件
cp server.py /opt/skills_repo/
cp -r web /opt/skills_repo/

# 重启
sudo systemctl start skills-repo
```

## 目录结构

```
/opt/skills_repo/
├── server.py              # 后端入口
├── requirements.txt       # Python 依赖
├── venv/                  # Python 虚拟环境
├── web/                   # 前端页面
├── skills_repo_client/    # CLI 工具源文件
├── skills/                # Skill 数据（自动创建）
├── config.json            # 配置文件
└── users.json             # 用户数据
```
