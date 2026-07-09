# 薪核通 PayClear

薪核通 PayClear 是一个面向建筑工程项目的农民工工资管理软件，用于管理队伍、工人银行卡信息、工资核对、打款回执、用工协议和数据备份。

本项目只保存代码。真实工资数据、身份证号、银行卡号、合同 PDF、数据库备份不要提交到 GitHub。

## 功能概览

- 月度工资核对：上传实名制表、工资表、支付明细，自动生成核对报告
- 工人信息管理：维护工人身份信息、银行卡、开户行、联行号
- 银行联号库：导入联号 Excel，检查工人银行信息是否匹配
- 历史数据导入：结合历史表格和打款回执 PDF，补充历史月份数据
- 打款回执解析：解析银行回执 PDF，确认成功/失败记录
- 用工协议管理：上传协议 PDF，识别姓名、身份证、日工资等信息
- 账号权限：管理员、操作员、队伍负责人分级管理
- 数据备份恢复：管理员可下载和恢复备份包

## 角色权限

| 角色 | 适合谁 | 主要权限 |
| --- | --- | --- |
| 管理员 admin | 你本人或核心管理者 | 用户管理、备份恢复、全量数据、系统配置、删除数据 |
| 操作员 operator | 内部协助人员 | 多队伍数据处理、上传核对、查看报告 |
| 队伍负责人 team_leader | 分包队伍/现场负责人 | 只能查看和处理自己队伍相关数据 |

重要规则：

- 备份、恢复、用户管理只允许管理员操作。
- 队伍负责人不能查看其他队伍的数据。
- 新系统不再内置 `admin/admin123` 这种固定默认账号。

## 技术栈

- 后端：Python、FastAPI、SQLAlchemy、SQLite
- 前端：原生 HTML/CSS/JavaScript
- 文件解析：openpyxl、pandas、pdfplumber、pdfminer、Tesseract OCR
- 部署：systemd、uvicorn

## 本地开发

环境要求：Python 3.10 或更高版本。

```bash
bash start.sh
```

访问：

```text
http://localhost:19268
```

如果本地数据库还没有管理员，可以临时这样启动：

```bash
INITIAL_ADMIN_USERNAME=qwe INITIAL_ADMIN_PASSWORD='换成至少10位强密码' bash start.sh
```

本地开发会使用开发环境密钥。生产服务器不要直接用 `start.sh`。

## 新服务器一键部署

推荐在 Ubuntu 22.04/24.04 上部署。

先把代码拉到服务器，例如：

```bash
cd /opt
git clone https://github.com/nidaye996/payclear.git
cd payclear
sudo bash deploy.sh
```

第一次运行 `deploy.sh` 时，脚本会询问：

```text
请输入第一个管理员账号
请输入第一个管理员密码
```

脚本会自动完成：

- 安装系统依赖
- 创建 Python 虚拟环境
- 安装 Python 依赖
- 生成生产环境 `SECRET_KEY`
- 创建第一个管理员账号
- 写入 systemd 服务
- 启动 PayClear
- 检查服务是否返回 HTTP 200

部署成功后访问：

```text
http://服务器IP:19268
```

没有域名时可以先用 IP 访问。因为工资系统包含敏感信息，建议只给少数固定人员账号，并使用强密码。后续有域名后，再配置 HTTPS。

## 已有服务器更新

在服务器项目目录执行：

```bash
cd /opt/payclear
sudo bash update.sh
```

更新脚本会：

- 拉取 GitHub 最新代码
- 部署前备份当前数据
- 更新依赖
- 重启 systemd 服务
- 检查服务是否正常

服务状态查看：

```bash
systemctl status payclear
```

查看日志：

```bash
journalctl -u payclear -n 100 --no-pager
```

## 管理员初始化说明

系统不再自动创建固定的 `admin/admin123`。

新数据库第一次启动时，只有同时满足下面条件才会创建管理员：

- 数据库里还没有任何管理员
- 环境变量里设置了 `INITIAL_ADMIN_USERNAME`
- 环境变量里设置了 `INITIAL_ADMIN_PASSWORD`

管理员创建成功后，部署脚本会清理初始化密码，避免密码长期留在服务器配置里。

如果是从旧服务器迁移数据库，不需要重新创建管理员。旧数据库里的管理员账号会跟着迁移。

## 备份和恢复

管理员可以在后台下载备份包。服务器部署脚本和更新脚本也会在关键操作前创建备份。

服务器自动/手动备份建议放在：

```text
/var/backups/payclear
```

不要把下面内容提交到 GitHub：

- `backend/data/salary.db`
- `backend/data/bank_routing.db`
- `backend/data/uploads/`
- `backend/data/contracts/`
- 任何 `.zip`、`.tar.gz` 备份包

## 常见问题

### HTTP 200 是什么意思？

表示 PayClear 服务正常响应了请求，网页入口是通的。它说明服务没有崩，但不代表所有业务功能都已经完整测试。

### 没有域名能不能用？

可以先用 `http://服务器IP:19268`。但 HTTP 不是加密传输，后续建议购买域名并配置 HTTPS。

### 忘记管理员密码怎么办？

如果还有其他管理员账号，可以登录后重置。若没有，需要在服务器上通过数据库维护方式重置密码，操作前必须先备份数据库。

### 能不能把备份放 GitHub？

不能。备份里包含工资、身份证、银行卡、合同等敏感信息。GitHub 只放代码。

## 目录结构

```text
payclear/
├── backend/
│   ├── main.py
│   ├── models.py
│   ├── routers/
│   ├── services/
│   └── data/
├── frontend/
├── deploy.sh
├── start.sh
├── update.sh
└── requirements.txt
```
