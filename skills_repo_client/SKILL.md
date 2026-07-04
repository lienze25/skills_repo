---
name: skills-repo-client
description: 与 Skills Repository API 交互 — 搜索、上传、下载、删除 Skill 以及管理标签。
---

# Skills Repository 客户端

通过 CLI 工具 `skills_repo_client.py` 与 Skills Repository 服务交互。所有操作需要认证。

## 触发场景

- 用户说："搜索 skill"、"查找 skill"、"skills search"
- 用户说："上传 skill"、"发布 skill"、"upload skill"
- 用户说："下载 skill"、"download skill"、"安装 skill"
- 用户说："删除 skill"、"delete skill"、"移除 skill"
- 用户说："列出标签"、"tags"、"管理标签"
- 用户说："skills list"、"查看 skills 列表"

## 首次使用

**执行任何操作前，必须先检查是否已配置。** 

检查 `~/.skills_repo_config` 是否存在且包含 `url` 字段。如果未配置，依次向用户询问（每次只问一个）：

1. **Skills Repository 服务器地址** — 如 `http://localhost:8080`
2. **登录用户名**
3. **登录密码**

收集完成后执行：

```bash
python3 skills_repo_client.py config --url <url> --username <username> --password <password>
```

配置保存到 `~/.skills_repo_config` 后即可进行后续操作。

## 配置

CLI 工具配置优先级：命令行参数 > 环境变量 > `~/.skills_repo_config`

### 环境变量

```bash
export SKILLS_REPO_URL="http://localhost:8080"
export SKILLS_REPO_USERNAME="admin"
export SKILLS_REPO_PASSWORD="admin"
# 或使用 token（优先于 username/password）
export SKILLS_REPO_TOKEN="your-token"
```

### Token 配置

如果设置了 `SKILLS_REPO_TOKEN`，所有请求将使用 Bearer token 认证，优先级高于 session cookie。

## 命令

### 搜索/列表

```bash
python3 skills_repo_client.py list                    # 列出所有 skills
python3 skills_repo_client.py list --q docker         # 关键词搜索
python3 skills_repo_client.py list --tag python       # 按标签筛选
python3 skills_repo_client.py list --tag python --json # JSON 输出
```

### 查看详情

```bash
python3 skills_repo_client.py get <skill-id>          # 查看 skill 详情
python3 skills_repo_client.py get <skill-id> --json   # JSON 输出
```

### 上传

```bash
python3 skills_repo_client.py upload --id my-skill --file ./SKILL.md --desc "描述" --tags "tag1,tag2"
python3 skills_repo_client.py upload --id my-skill --dir ./folder/ --desc "描述" --tags "tag1,tag2"
```

**约束：**
- `--file` 必须是 `.md` 或 `.mdc` 文件
- `--dir` 目录下必须包含 `SKILL.md`
- `--id` 格式：`^[a-z0-9]+(-[a-z0-9]+)*$`，客户端和服务端双重校验

### 下载

```bash
python3 skills_repo_client.py download <skill-id>             # 下载单个 skill
python3 skills_repo_client.py download <skill-id> --output a.zip
python3 skills_repo_client.py download-all                    # 下载所有 skills
```

### 删除

```bash
python3 skills_repo_client.py delete <skill-id>    # 删除 skill（仅上传者或管理员可删）
```

### 管理标签

```bash
python3 skills_repo_client.py tags --list                    # 列出所有标签
python3 skills_repo_client.py tags <skill-id> --set "a,b,c"  # 设置 skill 标签（覆盖）
```

## Skill ID 规范

`^[a-z0-9]+(-[a-z0-9]+)*$` — 小写字母数字，单连字符分隔。

## 备选：curl 方式

如果 CLI 工具不可用，可以用 curl（需提前登录获取 session cookie）：

```bash
# 登录
curl -c /tmp/cookies.txt -X POST {{SKILLS_REPO_URL}}/api/login -F "username=admin" -F "password=admin"

# 后续请求加 -b /tmp/cookies.txt
curl -s -b /tmp/cookies.txt {{SKILLS_REPO_URL}}/api/skills | python3 -m json.tool
curl -s -b /tmp/cookies.txt {{SKILLS_REPO_URL}}/api/skills/<id> | python3 -m json.tool
curl -s -b /tmp/cookies.txt -X POST {{SKILLS_REPO_URL}}/api/skills/upload \
  -F "files=@./SKILL.md" -F "skill_id=my-skill" -F "description=描述" -F "tags=tag1,tag2"
curl -s -b /tmp/cookies.txt -o out.zip {{SKILLS_REPO_URL}}/api/skills/<id>/download
curl -s -b /tmp/cookies.txt -o all.zip {{SKILLS_REPO_URL}}/api/skills/download/all
curl -s -b /tmp/cookies.txt -X DELETE {{SKILLS_REPO_URL}}/api/skills/<id>
curl -s -b /tmp/cookies.txt -X PUT {{SKILLS_REPO_URL}}/api/skills/<id>/tags \
  -H "Content-Type: application/json" -d '["a","b","c"]'
curl -s -b /tmp/cookies.txt {{SKILLS_REPO_URL}}/api/tags
```

## 注意事项

- 单文件上传仅支持 `.md`/`.mdc` 扩展名
- 目录上传必须包含 `SKILL.md` 文件
- 上传前确保 skill_id 未被占用（重复返回 409）
- skill_id 需匹配 `^[a-z0-9]+(-[a-z0-9]+)*$`
- 标签支持中文，逗号分隔
- 认证：CLI 自动处理 cookie/token，无需手动登录
- 下载解压后对应目录结构: `skills/<id>/SKILL.md`
- Token 方式比 cookie 更简单，推荐在脚本/CI 中使用
