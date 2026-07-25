# Skills Repository

opencode Skill 的存储、管理与分发服务。提供 Web 管理界面和 CLI 客户端工具。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 复制配置模板
cp config_template.json config.json

# 启动服务
python server.py

# 访问 http://localhost:8080
```

默认管理员账号 `admin`，首次启动时通过控制台设置密码。

## 配置

部署时将 `config_template.json` 复制为 `config.json`，然后编辑：

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "auth_enabled": true,
  "default_user": "admin",
  "skills_dir": "skills",
  "llm_api_url": "https://api.deepseek.com",
  "llm_api_key": "sk-xxx",
  "llm_model": "deepseek-v4-pro",
  "icp_number": null
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `host` | 监听地址 | `0.0.0.0` |
| `port` | 监听端口 | `8080` |
| `auth_enabled` | 是否启用认证 | `true` |
| `default_user` | 默认管理员 | `admin` |
| `skills_dir` | Skill 存储目录，支持相对/绝对路径和 `~/` | `skills` |
| `llm_api_url` | LLM API 地址（用于 AI 生成 Skill） | - |
| `llm_api_key` | LLM API Key | - |
| `llm_model` | LLM 模型名称 | - |
| `icp_number` | 网站备案号，`null` 或空时不显示 | - |

`skills_dir` 配置的目录不存在时会自动创建。

## API

### 认证
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 用户登录 |
| POST | `/api/logout` | 用户登出 |
| GET | `/api/me` | 获取当前用户信息 |
| GET | `/api/config` | 获取服务端配置 |

### Skill 管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 搜索/列出 Skill |
| GET | `/api/skills/{id}` | 获取 Skill 详情 |
| POST | `/api/skills/upload` | 上传 Skill |
| GET | `/api/skills/{id}/download` | 下载单个 Skill |
| GET | `/api/skills/download/all` | 下载全部 Skill |
| PUT | `/api/skills/{id}/tags` | 管理 Skill 标签 |
| DELETE | `/api/skills/{id}` | 删除 Skill |
| GET | `/api/tags` | 获取所有标签 |

### AI 生成
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/generate` | 调用 LLM 生成 Skill |

### 用户管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users` | 获取用户列表 |
| POST | `/api/users` | 添加用户 |
| PUT | `/api/users/{username}/password` | 修改用户密码 |
| PUT | `/api/users/{username}/permissions` | 修改用户权限 |
| DELETE | `/api/users/{username}` | 删除用户 |

## CLI 客户端

安装方式：在 Web 界面中下载或从 `/api/skills/download/all` 获取，安装到 opencode。

```bash
# 配置服务器地址
skills_repo_client setup

# 搜索 Skill
skills_repo_client search <keyword>

# 下载 Skill
skills_repo_client download <skill_id>
```

## 部署

### 直接运行

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config_template.json config.json
python server.py
```

### 生产部署

生产环境（Systemd + Nginx）部署请参考 [DEPLOY.md](DEPLOY.md)。

## 目录结构

```
skills_repo/
├── server.py              # 服务端入口
├── requirements.txt       # Python 依赖
├── config_template.json   # 配置模板
├── users.json             # 用户数据
├── web/                   # Web 前端页面
├── skills_repo_client/    # CLI 客户端
│   ├── SKILL.md           # opencode Skill 定义
│   └── skills_repo_client.py
└── skills/                # Skill 存储目录（可配置）
    └── skills_list.json   # Skill 索引
```

## License

MIT
