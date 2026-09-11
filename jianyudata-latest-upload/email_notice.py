"""SMTP 超额通知。密码只读取环境变量，不写入任务文件。"""
import hashlib
import os
import smtplib
import ssl
from email.message import EmailMessage


class SMTPNotifier:
    def __init__(self, host, port, sender, recipient, username='', password='', security='ssl'):
        if not host or not sender or not recipient:
            raise ValueError('必须填写 SMTP 主机、发件人和收件人')
        if security not in {'ssl', 'starttls'}:
            raise ValueError('SMTP_SECURITY 必须为 ssl 或 starttls')
        self.host, self.port = host, int(port)
        self.sender, self.recipient = sender, recipient
        self.username, self.password, self.security = username, password, security

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('SMTP_HOST', ''), os.environ.get('SMTP_PORT', '465'),
                   os.environ.get('SMTP_FROM', ''), os.environ.get('ALERT_TO', 'zihao.zhang@smartx.com'),
                   os.environ.get('SMTP_USER', ''), os.environ.get('SMTP_PASSWORD', ''),
                   os.environ.get('SMTP_SECURITY', 'ssl'))

    def send(self, record):
        message = EmailMessage()
        message['From'], message['To'] = self.sender, self.recipient
        message['Subject'] = f"剑鱼导出超额：{record['date']} {record['region']} {record['count']}条，已跳过"
        key = hashlib.sha256(f"{record['date']}|{record['region']}|{self.recipient}".encode()).hexdigest()
        message['Message-ID'] = f'<jianyu-{key}@export.local>'
        message.set_content(
            f"标讯日期：{record['date']}\n省份：{record['region']}\n"
            f"查询数据量：{record['count']} 条\n每日额度：800 条\n"
            "关键词：超融合、分布式存储、私有云、虚拟化；匹配方式：全选。\n"
            "处理结果：按配置放弃该日期该省份的导出，未扣除该省份额度，继续处理后续地区。\n"
            "该记录保存在 range_progress.json 的 skipped 中，不计入已导出数据。\n")
        context = ssl.create_default_context()
        if self.security == 'ssl':
            client = smtplib.SMTP_SSL(self.host, self.port, timeout=30, context=context)
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=30)
        with client:
            if self.security == 'starttls':
                client.starttls(context=context)
            if self.username:
                client.login(self.username, self.password)
            refused = client.send_message(message)
            if refused:
                raise RuntimeError('收件人被邮件服务器拒绝')
