# 薪核通 PayClear

薪核通 PayClear 是用于建筑工程项目农民工工资管理的软件。它可以管理队伍、工人银行卡信息、工资核对、打款回执、用工协议和数据备份。

本文既适合第一次部署服务器的用户，也适合需要维护系统的技术人员。

> 重要：系统会保存身份证号、银行卡号、工资和合同等敏感信息。代码可以放 GitHub，真实数据、上传文件和备份文件不能放 GitHub。

## 你可以用它做什么

- 上传实名制表、工资表和支付明细，自动生成核对报告
- 管理工人、银行卡、开户行和联行号
- 导入银行联号库，检查银行信息是否匹配
- 导入历史工资数据和打款回执
- 上传用工协议 PDF，识别姓名、身份证号和日工资
- 按管理员、操作员、队伍负责人分配权限
- 下载和恢复系统备份

## 第一次部署

本节适合刚购买云服务器、第一次安装 PayClear 的用户。按顺序完成即可。

### 1. 准备一台服务器

推荐使用 Ubuntu 22.04 或 24.04，并确认：

- 服务器有公网 IP
- 你可以使用 SSH 登录服务器
- 你登录的账号可以执行 sudo
- 云服务器安全组或防火墙已放行 TCP 端口 19268

如果不清楚服务器 IP，可以在云服务商控制台查看公网 IP。

### 2. 登录服务器

在自己的电脑终端中执行。把“你的服务器IP”换成实际公网 IP：

~~~bash
ssh ubuntu@你的服务器IP
~~~

如果服务器使用密钥登录，请按云服务商的说明在 SSH 命令中指定密钥文件。

成功后，终端会显示服务器提示符，例如：

~~~text
ubuntu@server:~$
~~~

### 3. 下载 PayClear

在服务器中依次执行：

~~~bash
cd /opt
sudo git clone https://github.com/nidaye996/payclear.git
cd payclear
~~~

如果提示 git: command not found，先执行：

~~~bash
sudo apt-get update
sudo apt-get install -y git
~~~

### 4. 安装并启动

仍在 /opt/payclear 目录中，执行：

~~~bash
sudo bash deploy.sh
~~~

第一次运行时，程序会要求输入：

~~~text
请输入第一个管理员账号
请输入第一个管理员密码
~~~

这就是系统第一个管理员账号。请使用自己记得住的账号和至少 10 位的强密码，不要使用 admin123、手机号或生日。

安装过程会自动安装运行环境、创建数据库、启动服务并检查服务是否正常。看到下面提示，说明安装成功：

~~~text
部署完成：PayClear 已正常响应 HTTP 200
~~~

### 5. 打开系统

在电脑浏览器中打开：

~~~text
http://你的服务器IP:19268
~~~

使用刚才创建的管理员账号登录。

如果网页打不开，请先看“常见问题”中的“浏览器打不开系统”。

## 日常使用

### 账号权限

| 角色 | 适合谁使用 | 主要权限 |
| --- | --- | --- |
| 管理员 admin | 项目负责人、核心管理者 | 用户管理、备份恢复、全量数据、删除数据 |
| 操作员 operator | 内部协助处理资料的人 | 多队伍数据处理、上传核对、查看报告 |
| 队伍负责人 team_leader | 分包队伍或现场负责人 | 只处理自己队伍的数据 |

管理员应只分配给少数可信人员。普通使用者应使用操作员或队伍负责人账号。

### 更新系统

登录服务器后执行：

~~~bash
cd /opt/payclear
sudo bash update.sh
~~~

更新会自动拉取最新代码、备份现有数据、安装所需依赖、重启服务并检查网页入口。

### 查看服务是否正常

~~~bash
sudo systemctl status payclear
~~~

看到 active (running) 表示服务正在运行。

查看最近日志：

~~~bash
sudo journalctl -u payclear -n 100 --no-pager
~~~

### 备份和恢复

管理员可以在系统后台下载备份。每次部署或更新前，服务器也会自动备份数据到：

~~~text
/var/backups/payclear
~~~

恢复备份前，请先下载一次当前备份。恢复后，之前的系统数据会被备份中的数据替换。

## 换服务器或迁移

迁移时需要带走两类内容：

- 代码：从 GitHub 重新下载即可
- 数据：旧服务器的 PayClear 备份文件

在新服务器上按“第一次部署”完成安装并创建管理员后，使用管理员账号登录，在后台恢复旧服务器导出的备份。恢复前请确认备份来自可信的旧服务器。

如果直接迁移旧服务器的数据库，原有管理员账号会一同迁移，无需再次创建固定默认账号。

## 本地试用和开发

本地电脑已安装 Python 3.10 或更高版本时，可以在项目目录执行：

~~~bash
bash start.sh
~~~

浏览器访问：

~~~text
http://localhost:19268
~~~

本地首次启动且数据库没有管理员时，可以临时设置第一个管理员：

~~~bash
INITIAL_ADMIN_USERNAME=你的账号 INITIAL_ADMIN_PASSWORD='至少10位强密码' bash start.sh
~~~

start.sh 仅用于本地试用和开发。生产服务器请使用 deploy.sh。

## 常见问题

### 浏览器打不开系统

按下面顺序检查：

1. 浏览器地址是否为 http://你的服务器IP:19268
2. 服务器服务是否运行：sudo systemctl status payclear
3. 云服务器安全组和服务器防火墙是否放行 TCP 端口 19268
4. 查看日志：sudo journalctl -u payclear -n 100 --no-pager

### HTTP 200 是什么意思

它表示服务器已经收到请求并正常返回网页入口。它说明服务没有崩溃，但不代表所有业务流程都已经完成测试。

### 忘记管理员密码怎么办

如果还有其他管理员，可以登录后重置密码。若没有任何管理员可用，需要在服务器上维护数据库。操作前务必备份数据库。

### 没有域名能不能使用

可以，先通过 http://服务器IP:19268 使用。

但 HTTP 不加密，工资和身份证等敏感信息不适合长期通过 HTTP 传输。正式长期使用时，建议购买域名并配置 HTTPS。

### 能把备份放到 GitHub 吗

不能。备份中可能包含工资、身份证、银行卡、合同和上传文件。GitHub 仓库只保存程序代码。

## 技术和维护参考

### 运行环境

- 后端：Python、FastAPI、SQLAlchemy、SQLite
- 前端：原生 HTML、CSS、JavaScript
- 文件解析：openpyxl、pandas、pdfplumber、pdfminer、Tesseract OCR
- 服务管理：systemd、uvicorn

### 管理员初始化机制

系统不会自动创建固定的 admin/admin123 账号。

只有新数据库中不存在任何管理员，同时部署时输入了管理员账号和密码，系统才会创建第一个管理员。创建成功后，部署脚本会清理初始化密码，避免它长期保留在服务器配置中。

### 需要保密的数据

不要提交或公开以下内容：

- backend/data/salary.db
- backend/data/bank_routing.db
- backend/data/uploads/
- backend/data/contracts/
- 所有 .zip、.tar.gz 备份文件
- 服务器环境配置中的密钥和管理员密码

### 项目结构

~~~text
payclear/
├── backend/          后端和数据库
├── frontend/         网页界面
├── deploy.sh         首次部署或重新部署
├── update.sh         拉取更新并重新部署
├── start.sh          本地试用和开发
└── requirements.txt  Python 依赖
~~~
