# Skills Repository — 部署指南

## 环境要求

- Python 3.9+
- 端口 8080（可配置）

---

## 方式一：直接 Python 运行

适用于开发测试或简单部署场景。

### 1. 获取代码

```bash
git clone <repo-url>
cd skills_repo

# 或者将代码解压到目标目录
```

### 2. 安装依赖

```bash
# 创建虚拟环境（可选但推荐）
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置

```bash
cp config_template.json config.json
```

编辑 `config.json`：

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "auth_enabled": true,
  "default_user": "admin",
  "skills_dir": "skills",
  "llm_api_url": "",
  "llm_api_key": "",
  "llm_model": ""
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `host` | 监听地址 | `0.0.0.0` |
| `port` | 监听端口 | `8080` |
| `auth_enabled` | 是否启用认证 | `true` |
| `default_user` | 默认管理员用户名 | `admin` |
| `skills_dir` | Skill 存储目录，支持相对/绝对路径和 `~/` | `skills` |
| `llm_api_url` | LLM API 地址（用于 AI 生成 Skill） | - |
| `llm_api_key` | LLM API Key | - |
| `llm_model` | LLM 模型名称 | - |

`skills_dir` 配置的目录不存在时会自动创建。

### 4. 启动

```bash
python server.py
```

首次启动会提示设置管理员密码。完成后访问 `http://localhost:8080`。

### 5. 后台运行

```bash
# 使用 nohup
nohup python server.py > server.log 2>&1 &

# 或使用 screen / tmux
screen -S skills_repo
python server.py
# Ctrl+A D 断开
```

### 6. 停止

```bash
pkill -f "python server.py"
```

---

## 方式二：Systemd 生产部署

适用于 Ubuntu/Debian 等 Linux 发行版的长期运行部署。

### 1. 安装依赖

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
```

### 2. 部署项目

```bash
# 克隆项目到 /opt
git clone <repo-url> /opt/skills_repo

# 创建虚拟环境并安装依赖
python3 -m venv /opt/skills_repo/venv
/opt/skills_repo/venv/bin/pip install -r /opt/skills_repo/requirements.txt
```

### 3. 配置

```bash
cp /opt/skills_repo/config_template.json /opt/skills_repo/config.json
```

编辑 `/opt/skills_repo/config.json`，参考上面的配置说明。

### 4. 添加用户

```bash
/opt/skills_repo/venv/bin/python3 /opt/skills_repo/server.py --add-user
```

### 5. 创建 Systemd 服务

创建 `/etc/systemd/system/skills-repo.service`：

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
sudo chown -R www-data:www-data /opt/skills_repo

sudo systemctl daemon-reload
sudo systemctl enable --now skills-repo
sudo systemctl status skills-repo
```

常用命令：

```bash
sudo systemctl status skills-repo   # 查看状态
sudo systemctl restart skills-repo  # 重启
sudo systemctl stop skills-repo     # 停止
sudo journalctl -u skills-repo -f   # 查看日志
```

---

## 防火墙

```bash
sudo ufw allow 8080/tcp
```

---

## Nginx 反向代理（可选）

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

---

## 验证

```bash
# 本地验证
curl http://localhost:8080/api/config

# 远程验证
curl http://<server-ip>:8080/api/config
```

浏览器访问 `http://<server-ip>:8080` 进入 Web 界面。

---

## 更新升级

```bash
# 停止服务
sudo systemctl stop skills-repo
# 或直接 python 部署时：pkill -f "python server.py"

# 替换文件
cp server.py /opt/skills_repo/
cp -r web /opt/skills_repo/

# 重启
sudo systemctl start skills-repo
# 或：python server.py
```
