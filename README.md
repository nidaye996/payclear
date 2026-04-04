# 薪核通 PayClear

农民工工资核对管理系统，面向建筑工程项目，帮助管理方核查分包队伍每月提交的工资发放数据，确保数据一致、银行信息准确。

## 功能概览

- **月度核对**：上传实名制花名册、工资表、银行支付明细三张表，自动完成多层交叉核查
- **核查报告**：生成详细报告，包含格式错误（身份证/银行卡/手机号）、跨表不一致、银行联号匹配等问题
- **历史数据导入**：支持导入历史月份数据，并通过打款回执 PDF 验证实际到账情况
- **银行联号库**：导入标准联号 Excel 文件，自动比对所有工人银行信息与联号库是否一致
- **三级账号体系**：管理员 / 操作员 / 队伍负责人，权限分级管理
- **数据备份恢复**：一键下载备份包，支持跨设备迁移数据

## 技术栈

- **后端**：Python · FastAPI · SQLAlchemy · SQLite
- **前端**：原生 HTML / CSS / JavaScript（多页应用，无框架依赖）
- **文件解析**：openpyxl · pandas · pdfplumber

## 快速开始

**环境要求**：Python 3.10+

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
bash start.sh
```

启动后访问 `http://localhost:8000`，默认管理员账号：

```
用户名：admin
密码：admin123
```

> 首次登录后请立即修改密码。

## 项目结构

```
payclear/
├── backend/
│   ├── main.py          # FastAPI 入口
│   ├── models.py        # 数据库模型
│   ├── routers/         # 各功能路由
│   └── services/        # 核查逻辑、文件解析
├── frontend/            # 前端页面
├── requirements.txt
└── start.sh
```
