"""Step 22 — EmailAdapter: send email via aiosmtplib with STARTTLS."""
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid

import aiosmtplib

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)


class EmailAdapter:
    name = "email_send"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER}

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        if caller not in self.allowed_callers:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED,
                    message=f"{caller!r} is not permitted to use {self.name}",
                    retriable=False,
                ),
            )

        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")

        if not smtp_host:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="SMTP_HOST is not configured",
                    retriable=False,
                ),
            )
        if not smtp_user:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="SMTP_USER is not configured",
                    retriable=False,
                ),
            )
        if not smtp_pass:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="SMTP_PASS is not configured",
                    retriable=False,
                ),
            )

        to_addr: str = payload.get("to", "")
        subject: str = payload.get("subject", "")
        body: str = payload.get("body", "")

        if not to_addr or not subject or not body:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="to, subject, and body are required",
                    retriable=False,
                ),
            )

        from_addr: str = payload.get(
            "from_addr",
            os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")),
        )
        cc: list[str] = payload.get("cc", [])
        bcc: list[str] = payload.get("bcc", [])
        content_type: str = payload.get("content_type", "text/plain")
        attachments: list[dict] = payload.get("attachments", [])

        smtp_port: int = int(os.environ.get("SMTP_PORT", "587"))
        use_tls: bool = os.environ.get("SMTP_TLS", "true").lower() != "false"

        try:
            msg = _build_message(
                to_addr=to_addr,
                from_addr=from_addr,
                subject=subject,
                body=body,
                content_type=content_type,
                cc=cc,
                bcc=bcc,
                attachments=attachments,
            )
        except FileNotFoundError as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"Attachment not found: {exc}",
                    retriable=False,
                ),
            )

        try:
            smtp = aiosmtplib.SMTP(
                hostname=smtp_host,
                port=smtp_port,
                use_tls=False,
                timeout=deadline_s,
            )
            async with smtp:
                if use_tls:
                    await smtp.starttls()
                await smtp.login(smtp_user, smtp_pass)
                recipients = [to_addr] + cc + bcc
                await smtp.sendmail(from_addr, recipients, msg.as_string())
        except aiosmtplib.SMTPException as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=True,
                ),
            )

        return Result(ok=True, data={"message_id": msg["Message-ID"]})

    async def health(self) -> bool:
        return bool(os.environ.get("SMTP_HOST", ""))

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="to", type="str", description="Recipient email address"),
                AdapterParam(name="subject", type="str", description="Email subject line"),
                AdapterParam(name="body", type="str", description="Email body content"),
            ],
            optional=[
                AdapterParam(name="from_addr", type="str", description="Sender address (default: SMTP_FROM or SMTP_USER env)"),
                AdapterParam(name="cc", type="list[str]", description="CC recipients"),
                AdapterParam(name="bcc", type="list[str]", description="BCC recipients"),
                AdapterParam(name="content_type", type="str", description="'text/plain' (default) or 'text/html'"),
                AdapterParam(name="attachments", type="list[dict]", description="List of {path, mimetype} dicts"),
            ],
        )


def _build_message(
    *,
    to_addr: str,
    from_addr: str,
    subject: str,
    body: str,
    content_type: str,
    cc: list[str],
    bcc: list[str],
    attachments: list[dict],
) -> MIMEMultipart | MIMEText:
    message_id = make_msgid()

    if attachments or content_type == "text/html":
        if attachments:
            msg: MIMEMultipart = MIMEMultipart("mixed")
            if content_type == "text/html":
                alt = MIMEMultipart("alternative")
                alt.attach(MIMEText(body, "plain"))
                alt.attach(MIMEText(body, "html"))
                msg.attach(alt)
            else:
                msg.attach(MIMEText(body, "plain"))
        else:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(body, "html"))

        msg["Message-ID"] = message_id
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc)
        if bcc:
            msg["Bcc"] = ", ".join(bcc)

        for att in attachments:
            path: str = att["path"]
            mimetype: str = att.get("mimetype", "application/octet-stream")
            main_type, sub_type = (mimetype.split("/", 1) if "/" in mimetype else ("application", "octet-stream"))
            with open(path, "rb") as fh:
                data = fh.read()
            part = MIMEBase(main_type, sub_type)
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=os.path.basename(path),
            )
            msg.attach(part)

        return msg

    plain = MIMEText(body, "plain")
    plain["Message-ID"] = message_id
    plain["From"] = from_addr
    plain["To"] = to_addr
    plain["Subject"] = subject
    if cc:
        plain["Cc"] = ", ".join(cc)
    if bcc:
        plain["Bcc"] = ", ".join(bcc)
    return plain
